"""Быстрая самопроверка окружения (запускается в CI на Windows и вручную при проблемах)."""
import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True


def check(name, fn):
    global ok
    try:
        r = fn()
        print(f"[ OK ] {name}" + (f": {r}" if r not in (None, True) else ""))
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"[FAIL] {name}: {e!r}")


check("numpy", lambda: __import__("numpy").__version__)
check("PySide6", lambda: __import__("PySide6").__version__)
check("pedalboard", lambda: __import__("pedalboard").__version__)
check("soundfile/libsndfile", lambda: __import__("soundfile").__libsndfile_version__)
check("soxr", lambda: __import__("soxr").__version__)
check("av", lambda: __import__("av").__version__)
check("mido+rtmidi", lambda: (__import__("mido").set_backend("mido.backends.rtmidi"), __import__("mido").get_input_names())[1])
check("keyboard", lambda: __import__("keyboard") and True)
check("psutil", lambda: __import__("psutil").__version__)


def sd_check():
    import sounddevice as sd
    return f"{sd.__version__}, hostapis: {[h['name'] for h in sd.query_hostapis()]}"


check("sounddevice/PortAudio", sd_check)


def engine_check():
    import numpy as np
    from app.audio.effects import CATALOG, EffectChain, default_params
    from app.model import EffectSlot
    ch = EffectChain()
    ch.sync([EffectSlot(str(i), t, True, default_params(t)) for i, t in enumerate(CATALOG)])
    for _ in range(50):
        out = ch.process(np.random.uniform(-0.3, 0.3, (480, 2)).astype(np.float32), 48000)
    assert out.shape == (480, 2)
    return f"{len(CATALOG)} эффектов"


check("цепочка эффектов", engine_check)


def decode_check():
    from app.audio.decoder import decode_file
    p = Path(__file__).resolve().parent.parent / "assets" / "sounds" / "Та-дам!.wav"
    return decode_file(str(p)).shape


check("декодирование", decode_check)


def ui_import():
    import app.ui.main_window  # noqa: F401
    import app.ui.pad_editor  # noqa: F401
    return True


check("интерфейс импортируется", ui_import)

if sys.platform == "win32":
    def loopback_struct():
        from app.audio import winloopback as w
        assert ctypes.sizeof(w.PROPVARIANT) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
        assert ctypes.sizeof(w.WAVEFORMATEX) == 18
        assert ctypes.sizeof(w.ACTIVATION_PARAMS) == 12
        return f"Windows build {sys.getwindowsversion().build}, process loopback: {w.is_supported()}"

    check("захват звука программ (структуры)", loopback_struct)

    def devices_list():
        from app.audio import devices
        outs = devices.list_devices("output")
        ins = devices.list_devices("input")
        cable = devices.find_vb_cable()
        return f"выходов {len(outs)}, входов {len(ins)}, VB-Cable: {'есть' if cable else 'нет'}"

    check("устройства WASAPI", devices_list)

    def programs_scan():
        from app.audio import detect
        progs = detect.scan_programs()
        return f"программ со звуком: {len(progs)}" + (" (" + ", ".join(p["label"] for p in progs[:5]) + ")" if progs else "")

    check("поиск программ со звуком (pycaw)", programs_scan)

print("\nИТОГ:", "всё в порядке" if ok else "есть проблемы")
sys.exit(0 if ok else 1)
