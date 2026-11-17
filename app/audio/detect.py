"""Автопоиск источников звука: микрофоны, «микрофонные» концы кабелей, программы, которые звучат сейчас."""
from __future__ import annotations

import os
import sys
import threading
import time

from . import devices

NICE = {
    "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "firefox.exe": "Firefox",
    "browser.exe": "Яндекс Браузер", "opera.exe": "Opera", "opera_gx.exe": "Opera GX", "brave.exe": "Brave",
    "vivaldi.exe": "Vivaldi", "spotify.exe": "Spotify", "yandexmusic.exe": "Яндекс Музыка",
    "discord.exe": "Discord", "telegram.exe": "Telegram", "vlc.exe": "VLC", "aimp.exe": "AIMP",
    "foobar2000.exe": "foobar2000", "steam.exe": "Steam", "obs64.exe": "OBS", "teams.exe": "Teams",
    "zoom.exe": "Zoom", "potplayermini64.exe": "PotPlayer", "mpc-hc64.exe": "MPC-HC", "winamp.exe": "Winamp",
}
SKIP = {"audiodg.exe", "svchost.exe", "explorer.exe", "systemsettings.exe", "shellexperiencehost.exe",
        "miaukalo.exe", "python.exe", "pythonw.exe"}


def nice_name(exe: str) -> str:
    exe = exe.lower()
    if exe in NICE:
        return NICE[exe]
    stem = exe[:-4] if exe.endswith(".exe") else exe
    return stem.replace("_", " ").replace("-", " ").title()


def _is_cable_mic(name: str) -> bool:
    """«Микрофонный» конец виртуального кабеля любого производителя (VB-Audio, VAC, Voicemod, Wave Link…)."""
    return devices.is_virtual_cable(name)


def scan_inputs() -> tuple[list, list]:
    """(микрофоны и прочие входы, «микрофонные» концы виртуальных кабелей)."""
    mics, cables = [], []
    for d in devices.list_devices("input"):
        item = {"type": "input", "device": d["name"], "hostapi": d["hostapi"], "default": d.get("default", False)}
        if _is_cable_mic(d["name"]):
            label = devices._cable_label(d["name"].replace("Output", "Input").replace("output", "input"))[0]
            cables.append({**item, "label": f"{label} (что в него пришло)", "sub": d["name"]})
        else:
            mics.append({**item, "label": d["name"].split("(")[0].strip() or d["name"], "sub": d["name"]})
    mics.sort(key=lambda m: not m["default"])
    return mics, cables


def scan_programs() -> list:
    """Программы со звуком. В Windows — активные аудиосессии с уровнем, иначе — известные плееры из списка процессов."""
    res: dict[str, dict] = {}
    me = os.getpid()
    if sys.platform == "win32":
        try:
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
            for s in AudioUtilities.GetAllSessions():
                try:
                    proc = s.Process
                    if proc is None or proc.pid == me:
                        continue
                    exe = proc.name().lower()
                    if exe in SKIP:
                        continue
                    try:
                        level = float(s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue())
                    except Exception:
                        level = 0.0
                    active = s.State == 1
                    cur = res.get(exe)
                    if cur is None:
                        res[exe] = {"type": "process", "process": exe, "label": nice_name(exe), "sub": exe,
                                    "level": level, "active": active}
                    else:
                        cur["level"] = max(cur["level"], level)
                        cur["active"] = cur["active"] or active
                except Exception:
                    continue
        except Exception:
            pass
    if not res:
        try:
            import psutil
            for p in psutil.process_iter(["name"]):
                exe = (p.info.get("name") or "").lower()
                if exe in NICE and exe not in res:
                    res[exe] = {"type": "process", "process": exe, "label": NICE[exe], "sub": exe,
                                "level": 0.0, "active": False}
        except Exception:
            pass
    return sorted(res.values(), key=lambda r: (not r["active"], -r["level"], r["label"]))


def scan() -> dict:
    mics, cables = scan_inputs()
    return {"inputs": mics, "programs": scan_programs(), "cables": cables, "time": time.time()}


class Scanner:
    """Фоновое сканирование раз в N секунд (только пока панель видна)."""

    def __init__(self, on_result, interval=1.0):
        self.on_result = on_result
        self.interval = interval
        self.running = False
        self._gen = 0

    def start(self):
        if self.running:
            return
        self.running = True
        self._gen += 1
        threading.Thread(target=self._loop, args=(self._gen,), daemon=True, name="source-scan").start()

    def stop(self):
        self.running = False

    def _loop(self, gen):
        com = False
        if sys.platform == "win32":
            try:
                import comtypes
                comtypes.CoInitialize()
                com = True
            except Exception:
                pass
        n = 0
        last_inputs = None
        try:
            while self.running and gen == self._gen:
                try:
                    progs = scan_programs()
                    if last_inputs is None or n % 5 == 0:     # устройства меняются редко
                        last_inputs = scan_inputs()
                    mics, cables = last_inputs
                    from .tabaudio import HUB
                    self.on_result({"inputs": mics, "programs": progs, "cables": cables, "tabs": HUB.snapshot(),
                                    "time": time.time()})
                except Exception:
                    pass
                n += 1
                time.sleep(self.interval)
        finally:
            if com:
                try:
                    import comtypes
                    comtypes.CoUninitialize()
                except Exception:
                    pass


def source_key(t: str, device="", process="") -> str:
    return f"{t}:{(process or device).lower()}"


def pad_source_key(pad) -> str | None:
    if pad.kind != "live":
        return None
    s = pad.source
    return source_key(s.type, s.device, s.process)
