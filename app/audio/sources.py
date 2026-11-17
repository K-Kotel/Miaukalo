"""Живые источники звука: микрофон/кабель, звук отдельной программы, loopback устройства."""
from __future__ import annotations

import sys
import threading
import warnings

from .devices import InputDevice
from .ring import Ring

BROWSERS = ["chrome.exe", "msedge.exe", "firefox.exe", "browser.exe", "opera.exe", "brave.exe",
            "vivaldi.exe", "spotify.exe", "yandexmusic.exe", "vlc.exe", "aimp.exe", "discord.exe"]


def running_apps() -> list[str]:
    """Имена запущенных программ (для выпадающего списка)."""
    try:
        import psutil
    except Exception:
        return BROWSERS[:]
    names = set()
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower()
        if n.endswith(".exe") and n not in ("svchost.exe", "conhost.exe", "runtimebroker.exe"):
            names.add(n)
    first = [b for b in BROWSERS if b in names]
    return first + sorted(names - set(first))


def find_root_pid(exe: str) -> int | None:
    """Главный процесс программы (у браузеров звук идёт из дочернего, поэтому берём корень дерева)."""
    import psutil
    exe = exe.lower().strip()
    if exe.isdigit():
        return int(exe)
    cands = []
    for p in psutil.process_iter(["pid", "name", "ppid", "create_time"]):
        if (p.info.get("name") or "").lower() == exe:
            cands.append(p)
    if not cands:
        return None
    pids = {p.info["pid"] for p in cands}
    roots = [p for p in cands if p.info.get("ppid") not in pids]
    roots.sort(key=lambda p: p.info.get("create_time") or 0)
    return (roots or cands)[0].info["pid"]


class LiveSource:
    """Общий интерфейс: ring + start/stop + error."""

    def __init__(self, sr: int):
        self.sr = sr
        self.ring = Ring(sr)
        self.error = ""
        self.closed = False   # голос уже остановлен — источник надо закрыть, даже если он ещё запускался

    def start(self) -> bool:
        raise NotImplementedError

    def stop(self):
        pass


class InputSource(LiveSource):
    def __init__(self, sr, device, hostapi):
        super().__init__(sr)
        self.dev = InputDevice(device, hostapi, sr)
        self.ring = self.dev.ring

    def start(self):
        ok = self.dev.start()
        self.error = self.dev.error
        return ok

    def stop(self):
        self.dev.stop()


class ProcessSource(LiveSource):
    def __init__(self, sr, exe):
        super().__init__(sr)
        self.exe = exe
        self.cap = None

    def start(self):
        if sys.platform != "win32":
            self.error = "захват программы работает только в Windows 10 (2004+) и 11"
            return False
        try:
            from . import winloopback
        except Exception as e:  # noqa: BLE001
            self.error = f"захват недоступен: {e}"
            return False
        if not winloopback.is_supported():
            self.error = "нужна Windows 10 версии 2004 или новее"
            return False
        pid = find_root_pid(self.exe)
        if pid is None:
            self.error = f"программа {self.exe} не запущена"
            return False
        self.cap = winloopback.ProcessCapture(pid, self.ring.write, self.sr)
        self.cap.start()
        if self.cap.error:
            self.error = self.cap.error
            return False
        self.error = ""
        return True

    def stop(self):
        if self.cap:
            self.cap.stop()
            self.cap = None


class DeviceLoopbackSource(LiveSource):
    """Всё, что играет на устройстве вывода (через библиотеку soundcard)."""

    def __init__(self, sr, device):
        super().__init__(sr)
        self.device = device
        self.running = False
        self._t = None
        self._ready = threading.Event()

    def start(self):
        try:
            import soundcard  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self.error = f"soundcard не установлен: {e}"
            return False
        self.running = True
        self._t = threading.Thread(target=self._run, daemon=True, name="dev-loopback")
        self._t.start()
        self._ready.wait(4)
        return not self.error

    def _run(self):
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        try:
            import soundcard as sc
            warnings.filterwarnings("ignore", module="soundcard")
            spk = None
            if self.device:
                spk = next((s for s in sc.all_speakers() if s.name == self.device), None)
                if spk is None:
                    spk = next((s for s in sc.all_speakers() if self.device.lower() in s.name.lower()
                                or s.name.lower() in self.device.lower()), None)
            spk = spk or sc.default_speaker()
            mic = sc.get_microphone(spk.id, include_loopback=True)
            with mic.recorder(samplerate=self.sr, channels=2, blocksize=480) as rec:
                self._ready.set()
                while self.running:
                    data = rec.record(numframes=480)
                    if data.ndim == 1:
                        data = data[:, None].repeat(2, axis=1)
                    self.ring.write(data[:, :2].astype("float32"))
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
        finally:
            self._ready.set()
            self.running = False

    def stop(self):
        self.running = False
        if self._t:
            self._t.join(1.5)


class TabSource(LiveSource):
    """Звук отдельной вкладки браузера (через расширение). Ждёт вкладку, если она ещё не подключилась,
    и сам переподключается после перезагрузки страницы."""

    def __init__(self, sr, device, host):
        super().__init__(sr)
        self.tab_id = device.split(":", 1)[1] if device.startswith("tab:") else device
        self.host = host or ""
        self.attached = None

    def wants(self, st) -> bool:
        if self.attached is not None and self.attached.connected:
            return False
        if st.tab_id == self.tab_id:
            return True
        # вкладку переоткрыли (новый id): берём вкладку того же сайта, если её никто не слушает
        return bool(self.host) and st.host == self.host and not st.subscribers

    def attach(self, st):
        if self.attached is not None and self in self.attached.subscribers:
            self.attached.subscribers.remove(self)
        self.attached = st
        self.tab_id = st.tab_id
        st.subscribers.append(self)
        self.ring.clear()

    def detach(self, st):
        if self.attached is st:
            self.attached = None
        if self in st.subscribers:
            st.subscribers.remove(self)

    def start(self):
        from .tabaudio import HUB
        if not HUB.running:
            self.error = "приём звука вкладок не запущен: " + (HUB.error or "неизвестная ошибка")
            return False
        HUB.subscribe(self)
        self.error = ""
        return True

    def stop(self):
        from .tabaudio import HUB
        HUB.unsubscribe(self)


def make_source(src, sr) -> LiveSource:
    """src: model.LiveSource."""
    if src.type == "tab":
        return TabSource(sr, src.device, src.process)
    if src.type == "process":
        return ProcessSource(sr, src.process)
    if src.type == "loopback":
        return DeviceLoopbackSource(sr, src.device)
    return InputSource(sr, src.device, src.hostapi)
