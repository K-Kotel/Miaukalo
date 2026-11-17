"""Захват звука одной программы (и её дочерних процессов) в Windows 10 2004+ / 11.

Используется AUDIOCLIENT_PROCESS_LOOPBACK — тот же механизм, что в официальном
примере Microsoft "ApplicationLoopback". Реализовано на чистом ctypes, без
сторонних библиотек: COM-интерфейсы вызываются через таблицы виртуальных методов.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import uuid
from ctypes import POINTER, byref, c_int32, c_int64, c_long, c_uint16, c_uint32, c_uint64, c_ubyte, c_void_p

import numpy as np

if sys.platform != "win32":
    raise ImportError("process loopback доступен только в Windows")

from ctypes import WINFUNCTYPE, wintypes  # noqa: E402


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_uint16), ("Data3", c_uint16), ("Data4", c_ubyte * 8)]


def _guid(s: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(s).bytes_le)


IID_IUnknown = _guid("{00000000-0000-0000-C000-000000000046}")
IID_IAgileObject = _guid("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
IID_IActivateAudioInterfaceCompletionHandler = _guid("{41D949AB-9862-444A-80F6-C261334DA5EB}")
IID_IAudioClient = _guid("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
IID_IAudioCaptureClient = _guid("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")

S_OK = 0
E_NOINTERFACE = c_long(0x80004002).value

VT_BLOB = 65
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0

AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2

COINIT_MULTITHREADED = 0x0
WAIT_OBJECT_0 = 0

VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"


class BLOB(ctypes.Structure):
    _fields_ = [("cbSize", c_uint32), ("pBlobData", c_void_p)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", c_uint16), ("r1", c_uint16), ("r2", c_uint16), ("r3", c_uint16), ("blob", BLOB)]


class PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    _fields_ = [("TargetProcessId", c_uint32), ("ProcessLoopbackMode", c_int32)]


class ACTIVATION_PARAMS(ctypes.Structure):
    _fields_ = [("ActivationType", c_int32), ("ProcessLoopbackParams", PROCESS_LOOPBACK_PARAMS)]


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", c_uint16), ("nChannels", c_uint16), ("nSamplesPerSec", c_uint32),
        ("nAvgBytesPerSec", c_uint32), ("nBlockAlign", c_uint16), ("wBitsPerSample", c_uint16),
        ("cbSize", c_uint16),
    ]


def _vfunc(ptr, index, restype, *argtypes):
    """Метод COM-объекта по номеру в vtable."""
    vtbl = ctypes.cast(c_void_p(ptr), POINTER(POINTER(c_void_p))).contents
    proto = WINFUNCTYPE(restype, c_void_p, *argtypes)
    fn = proto(vtbl[index])
    return lambda *args: fn(ptr, *args)


def _release(ptr):
    if ptr:
        try:
            _vfunc(ptr, 2, c_uint32)()
        except Exception:
            pass


# ------------------------------------------------ completion handler (свой COM-объект)

QI_T = WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))
REF_T = WINFUNCTYPE(c_uint32, c_void_p)
DONE_T = WINFUNCTYPE(c_long, c_void_p, c_void_p)


class _HandlerVtbl(ctypes.Structure):
    _fields_ = [("QueryInterface", QI_T), ("AddRef", REF_T), ("Release", REF_T), ("ActivateCompleted", DONE_T)]


class _HandlerObj(ctypes.Structure):
    _fields_ = [("lpVtbl", POINTER(_HandlerVtbl))]


_KEEP_ALIVE = []  # система может дёрнуть Release позже — объект не должен исчезнуть


class _CompletionHandler:
    def __init__(self):
        self.done = threading.Event()
        self.hr = -1
        self.client = None

        self._qi = QI_T(self._query_interface)
        self._addref = REF_T(lambda this: 1)
        self._release = REF_T(lambda this: 1)
        self._completed = DONE_T(self._activate_completed)
        self.vtbl = _HandlerVtbl(self._qi, self._addref, self._release, self._completed)
        self.obj = _HandlerObj(ctypes.pointer(self.vtbl))
        _KEEP_ALIVE.append(self)

    @property
    def ptr(self) -> int:
        return ctypes.addressof(self.obj)

    def _query_interface(self, this, riid, ppv):
        want = bytes(riid.contents)
        if want in (bytes(IID_IUnknown), bytes(IID_IAgileObject), bytes(IID_IActivateAudioInterfaceCompletionHandler)):
            ppv[0] = this
            return S_OK
        ppv[0] = None
        return E_NOINTERFACE

    def _activate_completed(self, this, op):
        try:
            hr = c_long(0)
            punk = c_void_p()
            r = _vfunc(op, 3, c_long, POINTER(c_long), POINTER(c_void_p))(byref(hr), byref(punk))
            if r != S_OK:
                self.hr = r
            elif hr.value != S_OK:
                self.hr = hr.value
            else:
                client = c_void_p()
                r = _vfunc(punk.value, 0, c_long, POINTER(GUID), POINTER(c_void_p))(byref(IID_IAudioClient), byref(client))
                _release(punk.value)
                self.hr = r
                if r == S_OK:
                    self.client = client.value
        except Exception:
            self.hr = -1
        finally:
            self.done.set()
        return S_OK


# ------------------------------------------------ сам захват

def is_supported() -> bool:
    try:
        v = sys.getwindowsversion()
        return (v.major, v.build) >= (10, 19041)
    except Exception:
        return False


class ProcessCapture:
    """Поток, который получает звук процесса и отдаёт его в callback(np.ndarray (n,2) float32)."""

    def __init__(self, pid: int, on_audio, sr: int = 48000):
        self.pid = pid
        self.on_audio = on_audio
        self.sr = sr
        self.error = ""
        self.running = False
        self.started = threading.Event()
        self._thread = None

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="proc-loopback")
        self._thread.start()
        self.started.wait(6)

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(2)

    def _run(self):
        ole32 = ctypes.windll.ole32
        kernel32 = ctypes.windll.kernel32
        ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        client = capture = None
        event = None
        try:
            params = ACTIVATION_PARAMS()
            params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
            params.ProcessLoopbackParams.TargetProcessId = self.pid
            params.ProcessLoopbackParams.ProcessLoopbackMode = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
            pv = PROPVARIANT()
            pv.vt = VT_BLOB
            pv.blob.cbSize = ctypes.sizeof(params)
            pv.blob.pBlobData = ctypes.cast(ctypes.pointer(params), c_void_p)

            handler = _CompletionHandler()
            op = c_void_p()
            activate = ctypes.windll.Mmdevapi.ActivateAudioInterfaceAsync
            activate.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT), c_void_p, POINTER(c_void_p)]
            activate.restype = c_long
            hr = activate(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, byref(IID_IAudioClient), byref(pv), handler.ptr, byref(op))
            if hr != S_OK:
                raise OSError(f"ActivateAudioInterfaceAsync: 0x{hr & 0xFFFFFFFF:08X}")
            if not handler.done.wait(5):
                raise OSError("Windows не ответил на запрос захвата")
            _release(op.value)
            if handler.hr != S_OK or not handler.client:
                raise OSError(f"Активация захвата: 0x{handler.hr & 0xFFFFFFFF:08X}")
            client = handler.client

            fmt = WAVEFORMATEX()
            fmt.wFormatTag = 1  # PCM
            fmt.nChannels = 2
            fmt.nSamplesPerSec = self.sr
            fmt.wBitsPerSample = 16
            fmt.nBlockAlign = 4
            fmt.nAvgBytesPerSec = self.sr * 4
            fmt.cbSize = 0

            initialize = _vfunc(client, 3, c_long, c_int32, c_uint32, c_int64, c_int64, POINTER(WAVEFORMATEX), c_void_p)
            flags = (AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK
                     | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY)
            hr = initialize(AUDCLNT_SHAREMODE_SHARED, flags, 1_000_000, 0, byref(fmt), None)
            if hr != S_OK:
                raise OSError(f"IAudioClient::Initialize: 0x{hr & 0xFFFFFFFF:08X}")

            kernel32.CreateEventW.restype = c_void_p
            event = kernel32.CreateEventW(None, False, False, None)
            hr = _vfunc(client, 13, c_long, c_void_p)(event)
            if hr != S_OK:
                raise OSError(f"SetEventHandle: 0x{hr & 0xFFFFFFFF:08X}")

            cap = c_void_p()
            hr = _vfunc(client, 14, c_long, POINTER(GUID), POINTER(c_void_p))(byref(IID_IAudioCaptureClient), byref(cap))
            if hr != S_OK:
                raise OSError(f"GetService: 0x{hr & 0xFFFFFFFF:08X}")
            capture = cap.value

            get_buffer = _vfunc(capture, 3, c_long, POINTER(c_void_p), POINTER(c_uint32), POINTER(c_uint32),
                                POINTER(c_uint64), POINTER(c_uint64))
            release_buffer = _vfunc(capture, 4, c_long, c_uint32)
            next_packet = _vfunc(capture, 5, c_long, POINTER(c_uint32))

            hr = _vfunc(client, 10, c_long)()  # Start
            if hr != S_OK:
                raise OSError(f"Start: 0x{hr & 0xFFFFFFFF:08X}")
            self.started.set()

            kernel32.WaitForSingleObject.argtypes = [c_void_p, c_uint32]
            data_p = c_void_p()
            frames = c_uint32()
            bflags = c_uint32()
            pos = c_uint64()
            qpc = c_uint64()
            pkt = c_uint32()
            while self.running:
                kernel32.WaitForSingleObject(event, 100)
                while self.running:
                    if next_packet(byref(pkt)) != S_OK:
                        raise OSError("поток захвата прервался")
                    if pkt.value == 0:
                        break
                    if get_buffer(byref(data_p), byref(frames), byref(bflags), byref(pos), byref(qpc)) != S_OK:
                        break
                    n = frames.value
                    if n:
                        if bflags.value & AUDCLNT_BUFFERFLAGS_SILENT or not data_p.value:
                            block = np.zeros((n, 2), np.float32)
                        else:
                            raw = ctypes.string_at(data_p.value, n * 4)
                            block = np.frombuffer(raw, dtype=np.int16).reshape(n, 2).astype(np.float32) / 32768.0
                        try:
                            self.on_audio(block)
                        except Exception:
                            pass
                    release_buffer(n)
            _vfunc(client, 11, c_long)()  # Stop
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
        finally:
            self.started.set()
            _release(capture)
            _release(client)
            if event:
                kernel32.CloseHandle(c_void_p(event))
            ole32.CoUninitialize()
            self.running = False
