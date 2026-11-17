"""Работа со звуковыми устройствами через PortAudio (sounddevice)."""
from __future__ import annotations

import sys
import threading
import time

import numpy as np

from .drift import DriftControl, Resampler
from .ring import Ring

try:
    import sounddevice as sd
    SD_ERROR = ""
except Exception as e:  # PortAudio не найден и т.п.
    sd = None
    SD_ERROR = str(e)

PREFERRED_HOSTAPI = "Windows WASAPI" if sys.platform == "win32" else ""


def hostapi_names() -> list[str]:
    if sd is None:
        return []
    return [h["name"] for h in sd.query_hostapis()]


def _preferred_hostapi_index():
    if sd is None:
        return None
    for i, h in enumerate(sd.query_hostapis()):
        if h["name"] == PREFERRED_HOSTAPI:
            return i
    return None


def list_devices(kind: str = "output", show_all: bool = False) -> list[dict]:
    """kind: output | input. Возвращает [{index, name, hostapi, default}]."""
    if sd is None:
        return []
    apis = sd.query_hostapis()
    pref = _preferred_hostapi_index()
    key = "max_output_channels" if kind == "output" else "max_input_channels"
    dkey = "default_output_device" if kind == "output" else "default_input_device"
    res = []
    for i, d in enumerate(sd.query_devices()):
        if d[key] <= 0:
            continue
        if not show_all and pref is not None and d["hostapi"] != pref:
            continue
        api = apis[d["hostapi"]]
        res.append({
            "index": i,
            "name": d["name"],
            "hostapi": api["name"],
            "default": api.get(dkey) == i,
            "channels": d[key],
        })
    return res


def rescan_portaudio():
    """Пересканировать устройства. Вызывать ТОЛЬКО когда все потоки закрыты."""
    if sd is None:
        return
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        pass


def default_device(kind: str = "output") -> dict | None:
    devs = list_devices(kind)
    for d in devs:
        if d["default"]:
            return d
    return devs[0] if devs else None


def find_device(kind: str, name: str, hostapi: str = "") -> int | None:
    if sd is None:
        return None
    devs = list_devices(kind, show_all=True)
    if not name:
        d = default_device(kind)
        return d["index"] if d else None
    for d in devs:
        if d["name"] == name and (not hostapi or d["hostapi"] == hostapi):
            return d["index"]
    for d in devs:
        if d["name"] == name:
            return d["index"]
    low = name.lower()
    for d in devs:  # MME обрезает имена до 31 символа — ищем по началу
        if d["name"].lower().startswith(low[:28]) or low.startswith(d["name"].lower()):
            return d["index"]
    return None


# ---------------------------------------------------------------- виртуальные кабели
# (производитель, признак в имени, приоритет). VB-Audio — основной ориентир, остальные распознаются тоже.
VENDORS = [
    ("VB-Audio", ("vb-audio", "voicemeeter"), 0),
    ("VAC", ("virtual audio cable",), 1),
    ("Voicemod", ("voicemod",), 2),
    ("Wave Link", ("wave link", "elgato virtual"), 2),
    ("SteelSeries Sonar", ("steelseries sonar",), 2),
    ("NVIDIA Broadcast", ("nvidia broadcast", "nvidia rtx voice"), 3),
    ("Krisp", ("krisp",), 3),
    ("Steam", ("steam streaming",), 3),
    ("Виртуальное", ("virtual", "cable", "loopback", "blackhole", "soundflower"), 4),
]


def vendor_of(name: str):
    low = (name or "").lower()
    for vendor, keys, prio in VENDORS:
        if any(k in low for k in keys):
            return vendor, prio
    return None


def is_virtual_cable(name: str) -> bool:
    return vendor_of(name) is not None


def _cable_label(name: str) -> tuple[str, str]:
    """(понятное имя, короткий код) для виртуального кабеля по имени устройства."""
    import re
    n = name.strip()
    m = re.match(r"(?i)cable-([a-z0-9]+)\s+input", n)
    if m:
        return f"Кабель {m.group(1).upper()}", m.group(1).upper()
    if re.match(r"(?i)cable\s+input", n):
        return "Кабель", "1"
    if re.match(r"(?i)cable in 16ch", n):
        return "Кабель 16ch", "16"
    if re.match(r"(?i)hi-?fi cable\s+input", n):
        return "Hi-Fi кабель", "HF"
    m = re.match(r"(?i)voicemeeter\s*(aux|vaio3)?\s*input", n)
    if m:
        tail = (m.group(1) or "").upper()
        return ("Voicemeeter " + tail).strip(), ("VM" + (tail[:1] if tail else ""))
    v = vendor_of(n)
    short = n.split("(")[0].strip() or n
    if v and v[0] == "VAC":
        return f"VAC {short}", short[-1:]
    if v and v[0] not in ("VB-Audio", "Виртуальное") and v[0].lower().split()[0] not in short.lower():
        return f"{v[0]} · {short}", "?"
    generic = short.lower() in ("speakers", "line", "динамики", "headphones", "наушники", "microphone", "микрофон")
    if generic and _paren(n):
        return f"{_paren(n)} · {short}", "?"
    return short, "?"


def _paren(name: str) -> str:
    """Содержимое внешних скобок: «Line (Voicemod Virtual Audio Device (WDM))» → «Voicemod … (WDM)»."""
    i = name.find("(")
    return name[i + 1:-1].strip() if i >= 0 and name.endswith(")") else ""


def recording_side(name: str) -> str:
    """Как этот кабель называется со стороны «микрофона» (то, что выбирают в Дискорде/OBS)."""
    import re
    ins = [d["name"] for d in list_devices("input", show_all=True)]
    low_ins = {n.lower(): n for n in ins}
    cands = [
        re.sub(r"(?i)\binput\b", "Output", name, count=1),        # VB-Cable: CABLE Input → CABLE Output
        name,                                                       # VAC: Line 1 — одно имя с обеих сторон
        re.sub(r"(?i)^(speakers|line|динамики|headphones|наушники)\b", "Microphone", name, count=1),
        re.sub(r"(?i)^(speakers|line|динамики|headphones|наушники)\b", "Микрофон", name, count=1),
    ]
    for c in cands:
        if c.lower() in low_ins:
            return low_ins[c.lower()]
    par = _paren(name)
    if par:                                                         # тот же драйвер в скобках
        same = [n for n in ins if _paren(n).lower() == par.lower()]
        if len(same) == 1:
            return same[0]
    v = vendor_of(name)
    if v and v[0] != "Виртуальное":                                  # микрофон того же производителя
        keys = dict((vv, kk) for vv, kk, _ in VENDORS)[v[0]]
        mics = [n for n in ins if any(k in n.lower() for k in keys)]
        mic_like = [n for n in mics if any(w in n.lower() for w in ("microphone", "микрофон", "mic", "stream", "chat"))]
        if len(mic_like) == 1:
            return mic_like[0]
        if len(mics) == 1:
            return mics[0]
    if re.search(r"(?i)\binput\b", name):
        return cands[0]
    return ""


def list_virtual_cables(vendors=None) -> list[dict]:
    """Все виртуальные устройства вывода (кабели). vendors — ограничить производителями."""
    res = []
    seen = set()
    for d in list_devices("output"):
        v = vendor_of(d["name"])
        if not v or d["name"] in seen or (vendors and v[0] not in vendors):
            continue
        seen.add(d["name"])
        label, code = _cable_label(d["name"])
        res.append({**d, "label": label, "code": code, "vendor": v[0], "prio": v[1],
                    "mic": recording_side(d["name"])})
    order = {"1": 0}
    res.sort(key=lambda c: (c["prio"], order.get(c["code"], 1), c["code"], c["label"]))
    return res


def find_vb_cable() -> dict | None:
    for d in list_devices("output"):
        if "cable input" in d["name"].lower() or "vb-audio" in d["name"].lower():
            return d
    return None


def find_any_cable() -> dict | None:
    cables = list_virtual_cables()
    return cables[0] if cables else None


def _extra(index: int):
    if sd is None:
        return None
    try:
        api = sd.query_hostapis()[sd.query_devices(index)["hostapi"]]["name"]
        if api == "Windows WASAPI":
            return sd.WasapiSettings(auto_convert=True)
    except Exception:
        pass
    return None


class OutputDevice:
    """Поток на одно устройство вывода. Читает готовый звук из кольцевого буфера."""

    def __init__(self, out_id: str, name: str, hostapi: str, sr: int):
        self.out_id = out_id
        self.name = name
        self.hostapi = hostapi
        self.sr = sr
        self.ring = Ring(sr)  # 1 секунда запаса
        self.stream = None
        self.error = ""
        self.last_try = 0.0
        self.volume = 1.0
        self.latency = int(sr * 0.06)  # желаемый запас в буфере (задаёт движок)
        self.chunk = 0                 # самый крупный блок, который забирало устройство
        self.starved = 0               # сколько раз устройству не хватило звука
        self._rs = Resampler()
        self._ctl = DriftControl(self.latency)
        self._primed = False
        self._seen_starved = 0

    @property
    def ok(self) -> bool:
        return self.stream is not None and getattr(self.stream, "active", True) and not self.error

    def _cb(self, outdata, frames, _time, status):
        if frames > self.chunk:
            self.chunk = frames
        got = self.ring.read_into(outdata)
        if got < frames and self._primed:
            self.starved += 1
        if self.volume != 1.0:
            outdata *= self.volume

    def target(self) -> int:
        """Сколько держать в буфере: задержка из настроек + половина блока устройства
        (Bluetooth и некоторые USB-карты забирают звук кусками по 40–100 мс)."""
        return min(self.sr * 3 // 4, self.latency + self.chunk // 2 + 240)

    def push(self, block: np.ndarray):
        """Записать очередной блок микшера, подстроившись под часы устройства."""
        tgt = self.target()
        self._ctl.target = tgt
        fill = self.ring.available()
        if not self._primed or self.starved != self._seen_starved:
            # старт или устройство «проголодалось»: подкладываем тишину до цели, без рывков дальше
            self._seen_starved = self.starved
            if fill < tgt:
                self.ring.write(np.zeros((tgt - fill, 2), np.float32))
                fill = tgt
            self._ctl.reset(fill)
            self._primed = True
        elif fill > tgt + self.sr // 4:
            # после зависания системы накопилось слишком много — сбрасываем лишнее разом
            self.ring.drop(fill - tgt)
            self._ctl.reset(tgt)
            fill = tgt
        step = self._ctl.update(fill)
        self._rs.push(block)
        out = self._rs.pull(len(block) + 8, step)
        self.ring.write(out)

    def _finished(self):
        if self.stream is not None:
            self.error = self.error or "устройство отключилось"

    def start(self) -> bool:
        self.last_try = time.monotonic()
        self.stop()
        if sd is None:
            self.error = "PortAudio не загружен: " + SD_ERROR
            return False
        idx = find_device("output", self.name, self.hostapi)
        if idx is None:
            self.error = "устройство не найдено"
            return False
        try:
            self.stream = sd.OutputStream(
                device=idx, samplerate=self.sr, channels=2, dtype="float32",
                latency="low", extra_settings=_extra(idx), callback=self._cb,
                finished_callback=self._finished,
            )
            self.ring.clear()
            self._rs.reset()
            self._primed = False
            self.chunk = 0
            self.stream.start()
            self.error = ""
            return True
        except Exception as e:  # noqa: BLE001
            self.stream = None
            self.error = str(e)
            return False

    def stop(self):
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.abort()
                s.close()
            except Exception:
                pass


class InputDevice:
    """Поток с устройства ввода в кольцевой буфер (стерео, 48 кГц)."""

    def __init__(self, name: str, hostapi: str, sr: int):
        self.name = name
        self.hostapi = hostapi
        self.sr = sr
        self.ring = Ring(sr)
        self.stream = None
        self.error = ""
        self._ch = 2

    def _cb(self, indata, frames, _time, status):
        if self._ch == 1:
            self.ring.write(np.repeat(indata, 2, axis=1))
        else:
            self.ring.write(indata[:, :2])

    def start(self) -> bool:
        if sd is None:
            self.error = "PortAudio не загружен: " + SD_ERROR
            return False
        idx = find_device("input", self.name, self.hostapi)
        if idx is None:
            self.error = "устройство ввода не найдено"
            return False
        try:
            maxch = sd.query_devices(idx)["max_input_channels"]
            self._ch = 1 if maxch == 1 else 2
            self.stream = sd.InputStream(
                device=idx, samplerate=self.sr, channels=self._ch, dtype="float32",
                latency="low", extra_settings=_extra(idx), callback=self._cb,
            )
            self.stream.start()
            self.error = ""
            return True
        except Exception as e:  # noqa: BLE001
            self.stream = None
            self.error = str(e)
            return False

    def stop(self):
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.abort()
                s.close()
            except Exception:
                pass


def play_test_tone(name: str, hostapi: str, sr: int = 48000):
    """Короткий сигнал на устройство — чтобы понять, куда он идёт."""
    if sd is None:
        raise RuntimeError("PortAudio не загружен")
    idx = find_device("output", name, hostapi)
    if idx is None:
        raise RuntimeError("устройство не найдено")
    t = np.arange(int(sr * 0.6)) / sr
    tone = 0.25 * np.sin(2 * np.pi * 660 * t) * np.minimum(1, (0.6 - t) * 20) * np.minimum(1, t * 50)
    tone += 0.15 * np.sin(2 * np.pi * 990 * t) * (t > 0.3)
    data = np.repeat(tone[:, None], 2, axis=1).astype(np.float32)

    def run():
        try:
            sd.play(data, sr, device=idx, extra_settings=_extra(idx), blocking=True)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()
