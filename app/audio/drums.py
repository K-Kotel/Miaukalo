"""Дрампад: 16 пэдов, петля на N тактов, живая запись по метроному с выравниванием по сетке.

Как устроено:
  * Удары с клавиатуры/MIDI/мыши копятся в очереди и звучат в начале ближайшего блока микшера (≤10 мс).
  * Если идёт запись, удар записывается туда, где пользователь его СЛЫШАЛ (с поправкой на задержку
    вывода), и выравнивается по сетке с заданной силой.
  * Петля хранится в долях (beat), поэтому смена темпа не ломает ритм.
  * Звук дрампада уходит в обычный канал Мяукала (эффекты, разводка, Дискорд), а метроном — только
    в «прослушивание» (наушники), чтобы его не слышали другие.
"""
from __future__ import annotations

import copy
import json
import re
import threading
import time
from pathlib import Path

import numpy as np

from . import drumkit

SR = 48000
GRIDS = {"off": 0.0, "1/4": 1.0, "1/8": 0.5, "1/8T": 1 / 3, "1/16": 0.25, "1/16T": 1 / 6, "1/32": 0.125}
GRID_TITLES = {"off": "без выравнивания", "1/4": "1/4", "1/8": "1/8", "1/8T": "1/8 триоль", "1/16": "1/16",
               "1/16T": "1/16 триоль", "1/32": "1/32"}
METRO = {"off": "Выкл", "rec": "Только при записи", "on": "Всегда"}
DEFAULT_KEYS = ["z", "x", "c", "v", "a", "s", "d", "f", "q", "w", "e", "r", "1", "2", "3", "4"]
PAD_COLORS = ["#ff9a1f", "#ffd36b", "#ff6a2a", "#ff3b30", "#ffe3b0", "#d0843a", "#b9e35a", "#7fd6c2"]
MAX_VOICES = 64
BEAT_KEYS = ("name", "bpm", "swing", "bars", "beats", "grid", "qstrength", "kit", "pads", "events")


def default_pad(kit: str, i: int) -> dict:
    names = drumkit.kit_names(kit)
    choke = 1 if names[i] in ("Закр. хэт", "Откр. хэт", "Педаль хэта") else 0
    return {"src": f"kit:{kit}:{i}", "name": names[i], "vol": 0.85, "pan": 0.0, "pitch": 0.0, "length_ms": 0,
            "reverse": False, "choke": choke, "mute": False, "color": PAD_COLORS[(i // 4) % len(PAD_COLORS)]}


def default_cfg() -> dict:
    return {
        "name": "Новый бит", "bpm": 92.0, "swing": 50, "bars": 2, "beats": 4, "grid": "1/16", "qstrength": 100,
        "metro": "rec", "metro_vol": 0.6, "countin": True, "repeat": False, "repeat_grid": "1/16",
        "kb_vel": 100, "comp_auto": True, "comp_ms": 0, "channel": "", "kit": "808",
        "pads": [default_pad("808", i) for i in range(16)], "events": [],
        "keys": list(DEFAULT_KEYS), "midi_on": True, "midi_base": 36, "keys_everywhere": False,
        "export_loops": 4, "slots": "",
    }


def normalize_cfg(cfg: dict) -> dict:
    """Дополняет недостающие поля (старые конфиги, проекты битов)."""
    base = default_cfg()
    for k, v in base.items():
        if k not in cfg:
            cfg[k] = copy.deepcopy(v)
    pads = cfg.get("pads") or []
    for i in range(16):
        dp = default_pad(cfg.get("kit", "808") if cfg.get("kit") in drumkit.KITS else "808", i)
        if i >= len(pads):
            pads.append(dp)
        else:
            for k, v in dp.items():
                pads[i].setdefault(k, v)
    cfg["pads"] = pads[:16]
    if len(cfg.get("keys") or []) != 16:
        cfg["keys"] = list(DEFAULT_KEYS)
    cfg["events"] = [[float(e[0]), int(e[1]), int(e[2])] + ([int(e[3])] if len(e) > 3 else [0])
                     for e in cfg.get("events", []) if 0 <= int(e[1]) < 16]
    cfg["bars"] = int(max(1, min(16, cfg.get("bars", 2))))
    cfg["beats"] = int(max(2, min(7, cfg.get("beats", 4))))
    cfg["bpm"] = float(max(40, min(240, cfg.get("bpm", 92))))
    return cfg


# ------------------------------------------------------------------ проекты битов
def beats_dir() -> Path:
    from app.model import data_dir
    d = data_dir() / "beats"
    d.mkdir(exist_ok=True)
    return d


def _safe(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "Бит"


def list_beats() -> list[str]:
    return sorted(p.stem for p in beats_dir().glob("*.json"))


def save_beat(cfg: dict, name: str) -> Path:
    data = {k: copy.deepcopy(cfg[k]) for k in BEAT_KEYS if k in cfg}
    data["name"] = name
    data["events"] = [e[:3] for e in cfg["events"]]
    p = beats_dir() / f"{_safe(name)}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    return p


def load_beat(cfg: dict, name: str):
    data = json.loads((beats_dir() / f"{_safe(name)}.json").read_text("utf-8"))
    for k in BEAT_KEYS:
        if k in data:
            cfg[k] = data[k]
    normalize_cfg(cfg)


def delete_beat(name: str):
    p = beats_dir() / f"{_safe(name)}.json"
    if p.exists():
        p.unlink()


# ------------------------------------------------------------------ звуки
def _click(freq, vol):
    t = np.arange(int(0.03 * SR)) / SR
    x = (np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.008) * vol).astype(np.float32)
    return np.stack([x, x], 1)


def prepare_sample(raw: np.ndarray | None, pad: dict) -> np.ndarray | None:
    """Высота, реверс, длина — заранее, чтобы в реальном времени только складывать."""
    if raw is None or len(raw) == 0:
        return None
    x = np.asarray(raw, dtype=np.float32)
    if x.dtype != np.float32 or x.ndim != 2:
        x = x.astype(np.float32)
    semi = float(pad.get("pitch", 0.0))
    if abs(semi) > 0.01:
        ratio = 2 ** (semi / 12)
        n = max(16, int(len(x) / ratio))
        try:
            import soxr
            x = soxr.resample(x, SR * ratio, SR).astype(np.float32)  # быстрее — значит выше
        except Exception:
            idx = np.linspace(0, len(x) - 1, n)
            x = np.stack([np.interp(idx, np.arange(len(x)), x[:, c]) for c in range(2)], 1).astype(np.float32)
    if pad.get("reverse"):
        x = x[::-1].copy()
    ln = int(float(pad.get("length_ms", 0)) * SR / 1000)
    if 0 < ln < len(x):
        x = x[:ln].copy()
        f = min(ln, int(0.03 * SR))
        x[-f:] *= np.linspace(1, 0, f, dtype=np.float32)[:, None]
    return np.ascontiguousarray(x)


class DrumMachine:
    def __init__(self, engine=None):
        self.engine = engine
        self.cfg = normalize_cfg({})
        self.lock = threading.RLock()
        self.samples: list = [None] * 16
        self._prep_keys: list = [None] * 16
        self.queue: list = []                 # (пэд, сила, время нажатия, записывать?)
        self.voices: list = []                # [звук, позиция, gl, gr, пэд, группа, затухание]
        self.playing = False
        self.recording = False
        self.countin = 0                      # сколько сэмплов отсчёта осталось
        self.pos = 0                          # позиция в петле, сэмплы
        self.passes = 0
        self.take = 0
        self.held: dict = {}                  # пэд → сила (для повтора ноты)
        self.flash = [0.0] * 16               # когда пэд звучал (для подсветки)
        self.ui_focus = False                 # вкладка открыта и окно активно
        self.last_render = time.perf_counter()
        self._sched = None
        self._skip: dict = {}
        self.on_change = None
        self.click_hi = _click(1600, 1.0)
        self.click_lo = _click(1000, 0.7)

    # ------------------------------------------------------------ настройки
    def attach(self, cfg: dict):
        with self.lock:
            self.cfg = normalize_cfg(cfg)
            self._sched = None
        self.refresh()

    def spb(self) -> float:
        return 60.0 / self.cfg["bpm"] * SR

    def loop_beats(self) -> int:
        return self.cfg["bars"] * self.cfg["beats"]

    def loop_len(self) -> int:
        return max(BLOCK_MIN, int(round(self.loop_beats() * self.spb())))

    def changed(self):
        """Звала UI: поменялись события/темп/свинг — пересчитать расписание."""
        with self.lock:
            self._sched = None

    def set_bpm(self, bpm):
        with self.lock:
            old = self.loop_len()
            frac = self.pos / old if old else 0.0
            self.cfg["bpm"] = float(max(40, min(240, bpm)))
            self.pos = int(frac * self.loop_len())
            self._sched = None

    def _source(self, src: str):
        if src.startswith("kit:"):
            _, kit, i = src.split(":")
            return drumkit.sound(kit, int(i))
        eng = self.engine
        if src.startswith("pad:") and eng is not None and eng.project is not None:
            p = eng.project.pad(src[4:])
            if p is None or p.kind != "file":
                return None
            data = eng.cache.get(p.file)
            if data is None:
                eng.cache.request(p.file)
                return None
            a = int(p.trim_start * SR)
            b = int(p.trim_end * SR) if p.trim_end > 0 else len(data)
            return data[a:b].astype(np.float32) / 32768.0
        if src.startswith("file:") and eng is not None:
            data = eng.cache.get(src[5:])
            if data is None:
                eng.cache.request(src[5:])
                return None
            return data.astype(np.float32) / 32768.0
        return None

    def refresh(self) -> bool:
        """Подготовить звуки пэдов (в потоке интерфейса). True — всё готово."""
        ready = True
        for i, pad in enumerate(self.cfg["pads"]):
            key = (pad.get("src"), round(float(pad.get("pitch", 0)), 2), bool(pad.get("reverse")),
                   int(pad.get("length_ms", 0)))
            if key == self._prep_keys[i] and self.samples[i] is not None:
                continue
            try:
                s = prepare_sample(self._source(pad.get("src", "")), pad)
            except Exception:
                s = None
            if s is None:
                ready = False
            self.samples[i] = s
            self._prep_keys[i] = key if s is not None else None
        return ready

    # ------------------------------------------------------------ управление
    def keys_active(self) -> bool:
        return self.cfg.get("keys_everywhere") or self.ui_focus

    def hit(self, pad: int, vel: int = 100, record=True):
        if 0 <= pad < 16:
            with self.lock:
                self.queue.append((pad, max(1, min(127, int(vel))), time.perf_counter(), record))
                if self.cfg.get("repeat"):
                    self.held[pad] = vel

    def release(self, pad: int):
        with self.lock:
            self.held.pop(pad, None)

    def play(self):
        with self.lock:
            if not self.playing:
                self.playing = True
                self.pos = 0
                self.passes = 0
                self._skip.clear()
                self.countin = 0
        self._notify()

    def stop(self):
        with self.lock:
            self.playing = False
            self.recording = False
            self.countin = 0
            self.held.clear()
        self._notify()

    def play_stop(self):
        self.stop() if self.playing else self.play()

    def toggle_rec(self):
        with self.lock:
            if self.recording:
                self.recording = False
            else:
                self.recording = True
                self.take = max([e[3] for e in self.cfg["events"]] + [self.take]) + 1
                if not self.playing:
                    self.playing = True
                    self.pos = 0
                    self.passes = 0
                    self._skip.clear()
                    self.countin = int(self.cfg["beats"] * self.spb()) if self.cfg.get("countin") else 0
        self._notify()

    def undo(self) -> bool:
        with self.lock:
            ev = self.cfg["events"]
            if not ev:
                return False
            last = max(e[3] for e in ev)
            self.cfg["events"] = [e for e in ev if e[3] != last]
            self._sched = None
        self._notify()
        return True

    def clear(self, pad: int | None = None):
        with self.lock:
            self.cfg["events"] = [e for e in self.cfg["events"] if pad is not None and e[1] != pad]
            self._sched = None
        self._notify()

    def toggle_step(self, pad: int, beat: float, vel: int = 100):
        """Клик по сетке: поставить удар или убрать уже стоящий рядом."""
        g = GRIDS.get(self.cfg["grid"]) or 0.25
        with self.lock:
            ev = self.cfg["events"]
            near = [e for e in ev if e[1] == pad and abs(e[0] - beat) < g * 0.5]
            if near:
                for e in near:
                    ev.remove(e)
            else:
                ev.append([float(beat), int(pad), int(vel), 0])
            self._sched = None
        self._notify()

    def double(self):
        """Удвоить петлю: такты ×2, записанное повторяется во второй половине."""
        with self.lock:
            lb = self.loop_beats()
            if self.cfg["bars"] * 2 > 16:
                return
            self.cfg["events"] += [[e[0] + lb, e[1], e[2], e[3]] for e in self.cfg["events"] if e[0] < lb]
            self.cfg["bars"] *= 2
            self._sched = None
        self._notify()

    def _notify(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    # ------------------------------------------------------------ расписание
    def swung(self, beat: float) -> float:
        """Свинг (как на MPC, 50–75 %): каждая вторая 1/16 сдвигается вперёд."""
        sw = (float(self.cfg.get("swing", 50)) - 50) / 50
        if sw <= 0:
            return beat
        q = beat / 0.25
        k = round(q)
        if abs(q - k) < 0.02 and k % 2 == 1:
            return beat + sw * 0.25
        return beat

    def schedule(self):
        if self._sched is None:
            spb, lb = self.spb(), self.loop_beats()
            muted = {i for i, p in enumerate(self.cfg["pads"]) if p.get("mute")}
            items = [(int(round(self.swung(e[0]) * spb)) % self.loop_len(), e[1], e[2], id(e))
                     for e in self.cfg["events"] if e[0] < lb and e[1] not in muted]
            items.sort()
            self._sched = (np.array([i[0] for i in items], dtype=np.int64), items)
        return self._sched

    # ------------------------------------------------------------ звук
    def _trigger(self, pad, vel, offset):
        s = self.samples[pad]
        if s is None:
            return
        p = self.cfg["pads"][pad]
        g = float(p.get("vol", 0.85)) * (vel / 127.0) ** 1.6
        pan = float(p.get("pan", 0.0))
        a = (pan + 1) * np.pi / 4
        choke = int(p.get("choke", 0))
        if choke:
            for v in self.voices:
                if v[5] == choke and v[6] < 0:
                    v[6] = int(0.006 * SR)
        self.voices.append([s, -int(offset), np.float32(g * np.cos(a) * 1.414), np.float32(g * np.sin(a) * 1.414),
                            pad, choke, -1])
        if len(self.voices) > MAX_VOICES:
            self.voices.pop(0)
        self.flash[pad] = time.perf_counter()

    def comp_frames(self) -> int:
        c = int(float(self.cfg.get("comp_ms", 0)) * SR / 1000)
        if self.cfg.get("comp_auto", True) and self.engine is not None:
            lat = self.engine.latency_frames + 480
            try:
                devs = list(self.engine.outputs.values())
                if devs:
                    lat = max(getattr(d, "target", lambda: lat)() for d in devs) + 480
            except Exception:
                pass
            c += lat
        return c

    def _record(self, pad, vel, t_hit, now, block_start):
        """Записать удар туда, где он был услышан, выровнять по сетке."""
        L = self.loop_len()
        heard = block_start - (now - t_hit) * SR - self.comp_frames()
        spb = self.spb()
        g = GRIDS.get(self.cfg["grid"], 0.25)
        beat = heard / spb
        if g > 0:
            q = round(beat / g) * g
            beat = beat + (q - beat) * float(self.cfg.get("qstrength", 100)) / 100
        target = beat * spb
        lb = self.loop_beats()
        wrapped_beat = beat % lb
        ev = [round(wrapped_beat, 6), int(pad), int(vel), self.take]
        self.cfg["events"].append(ev)
        self._sched = None
        # удар уже прозвучал вживую — если его место ещё впереди в этом (или следующем) проходе, не повторяем
        if target >= block_start:
            self._skip[id(ev)] = self.passes + int(target // L)

    def render(self, n: int):
        """(звук ударов или None, щелчки метронома или None)."""
        with self.lock:
            now = time.perf_counter()
            self.last_render = now
            if not self.playing and not self.voices and not self.queue:
                return None, None
            click = None
            q, self.queue = self.queue, []
            for pad, vel, t_hit, rec in q:
                self._trigger(pad, vel, 0)
                if rec and self.recording and self.playing and self.countin == 0:
                    self._record(pad, vel, t_hit, now, self.pos)
            if self.playing:
                click = self._advance(n)
            buf = np.zeros((n, 2), np.float32)
            alive = []
            for v in self.voices:
                s, pos, gl, gr, pad, choke, fade = v
                if pos < 0:                      # начнётся внутри этого блока
                    off = -pos
                    if off >= n:
                        v[1] = pos + n
                        alive.append(v)
                        continue
                    pos = 0
                else:
                    off = 0
                k = min(n - off, len(s) - pos)
                if k > 0:
                    chunk = s[pos:pos + k]
                    if fade >= 0:                # заглушён соседом по группе (хэты)
                        m = min(k, fade)
                        env = np.zeros(k, np.float32)
                        if m > 0:
                            env[:m] = np.linspace(fade / (0.006 * SR), max(0.0, (fade - m) / (0.006 * SR)), m)
                        chunk = chunk * env[:, None]
                        v[6] = fade - m
                        if v[6] <= 0:
                            k = 0
                    buf[off:off + len(chunk), 0] += chunk[:, 0] * gl
                    buf[off:off + len(chunk), 1] += chunk[:, 1] * gr
                v[1] = pos + max(k, 0)
                if v[1] < len(s) and not (fade >= 0 and v[6] <= 0):
                    alive.append(v)
            self.voices = alive
            return buf, click

    def _metro_on(self):
        m = self.cfg.get("metro", "rec")
        return m == "on" or (m == "rec" and (self.recording or self.countin > 0))

    def _advance(self, n: int):
        """Сдвинуть транспорт на блок: отсчёт, удары петли, метроном, повтор ноты."""
        click = None
        spb = self.spb()
        vol = float(self.cfg.get("metro_vol", 0.6))
        if self.countin > 0:
            total = int(self.cfg["beats"] * spb)
            start = total - self.countin
            for b in range(self.cfg["beats"]):
                t = int(round(b * spb)) - start
                if 0 <= t < n:
                    click = self._add_click(click, n, t, b == 0, vol)
            if self.countin > n:
                self.countin -= n
                return click
            n0 = self.countin                    # отсчёт кончился внутри блока — петля стартует с этого места
            self.countin = 0
            self.pos = -n0
        L = self.loop_len()
        times, items = self.schedule()
        start = self.pos
        end = start + n
        seg_start = start
        while seg_start < end:
            a = seg_start % L if seg_start >= 0 else seg_start
            if a < 0:                             # остаток блока после отсчёта
                seg_start = 0
                continue
            seg_len = min(end - seg_start, L - a)
            off0 = seg_start - start
            if len(times):
                i0 = int(np.searchsorted(times, a))
                i1 = int(np.searchsorted(times, a + seg_len))
                for t, pad, vel, eid in items[i0:i1]:
                    if self._skip.get(eid) == self.passes:
                        continue
                    self._trigger(pad, vel, off0 + (t - a))
            if self._metro_on():
                b0 = int(np.ceil(a / spb - 1e-9))
                while b0 * spb < a + seg_len:
                    t = int(round(b0 * spb))
                    if a <= t < a + seg_len:
                        click = self._add_click(click, n, off0 + t - a, b0 % self.cfg["beats"] == 0, vol)
                    b0 += 1
            if self.held and self.cfg.get("repeat"):
                g = (GRIDS.get(self.cfg.get("repeat_grid", "1/16")) or 0.25) * spb
                k0 = int(np.ceil(a / g - 1e-9))
                while k0 * g < a + seg_len:
                    t = int(round(k0 * g))
                    if a <= t < a + seg_len:
                        for pad, vel in list(self.held.items()):
                            self._trigger(pad, vel, off0 + t - a)
                            if self.recording:
                                ev = [round(t / spb, 6), pad, vel, self.take]
                                self.cfg["events"].append(ev)
                                self._skip[id(ev)] = self.passes + 1 if t < a else self.passes
                                self._sched = None
                    k0 += 1
            seg_start += seg_len
            if a + seg_len >= L:
                self.passes += 1
                stale = [k for k, p in self._skip.items() if p < self.passes]
                for k in stale:
                    del self._skip[k]
                times, items = self.schedule()
        self.pos = end % L
        return click

    def _add_click(self, click, n, off, hi, vol):
        if click is None:
            click = np.zeros((n, 2), np.float32)
        s = self.click_hi if hi else self.click_lo
        k = min(n - off, len(s))
        if k > 0:
            click[off:off + k] += s[:k] * vol
        return click

    # ------------------------------------------------------------ офлайн
    def render_offline(self, loops: int = 1, mode: str = "file") -> np.ndarray:
        """mode="loop" — ровно одна петля, хвосты заворачиваются в начало (бесшовный повтор);
        "file" — loops повторов + хвост в конце."""
        self.refresh()
        with self.lock:
            spb = self.spb()
            L = self.loop_len()
            lb = self.loop_beats()
            hits = []
            for k in range(max(1, loops)):
                for e in self.cfg["events"]:
                    if e[0] < lb and not self.cfg["pads"][e[1]].get("mute"):
                        hits.append((k * L + int(round(self.swung(e[0]) * spb)) % L, e[1], e[2]))
            hits.sort()
            samples = list(self.samples)
            pads = copy.deepcopy(self.cfg["pads"])
        total = L * max(1, loops)
        tail = int(4 * SR)
        out = np.zeros((total + tail, 2), np.float32)
        # длина каждого удара с учётом групп (открытый хэт глушится закрытым)
        next_in_group: dict = {}
        ends = [None] * len(hits)
        for idx in range(len(hits) - 1, -1, -1):
            t, pad, vel = hits[idx]
            g = int(pads[pad].get("choke", 0))
            if g:
                ends[idx] = next_in_group.get(g)
                next_in_group[g] = t
        for idx, (t, pad, vel) in enumerate(hits):
            s = samples[pad]
            if s is None:
                continue
            p = pads[pad]
            g = float(p.get("vol", 0.85)) * (vel / 127.0) ** 1.6
            a = (float(p.get("pan", 0)) + 1) * np.pi / 4
            seg = s
            if ends[idx] is not None:
                ln = max(0, ends[idx] - t)
                if ln < len(s):
                    f = min(int(0.006 * SR), len(s) - ln)
                    seg = s[:ln + f].copy()
                    if f > 0:
                        seg[ln:] *= np.linspace(1, 0, f, dtype=np.float32)[:, None]
            k = min(len(seg), len(out) - t)
            out[t:t + k, 0] += seg[:k, 0] * g * np.cos(a) * 1.414
            out[t:t + k, 1] += seg[:k, 1] * g * np.sin(a) * 1.414
        if mode == "loop":
            res = out[:total].copy()
            res[:tail] += out[total:total + tail][:min(tail, total)] if total >= tail else 0
            if total < tail:                    # петля короче хвоста — заворачиваем по кругу
                rest = out[total:]
                for i in range(0, len(rest), total):
                    ch = rest[i:i + total]
                    res[:len(ch)] += ch
        else:
            nz = np.nonzero(np.abs(out[total:]).max(axis=1) > 1e-4)[0]
            end = total + (int(nz[-1]) + int(0.05 * SR) if len(nz) else 0)
            res = out[:min(len(out), end)].copy()
        peak = float(np.abs(res).max()) if len(res) else 0.0
        if peak > 0.98:                          # не перегружаем файл
            res *= 0.98 / peak
        return res


BLOCK_MIN = 480
