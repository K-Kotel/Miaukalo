"""Блоки записи на схеме «Коммутации».

Три режима:
  * file — длинная запись всего, что приходит в блок, прямо в файл (WAV / FLAC / MP3), без ограничений;
  * clip — короткая запись (обычно «пока держишь клавишу»): тишина по краям обрезается, громкость
    выравнивается, файл сохраняется в библиотеку и сразу появляется среди звуков саундпада.
  * replay — «повтор»: блок всё время помнит последние N секунд/минут и по клавише сохраняет их
    в саундпад (как «сохранить момент» в играх).
Блок всегда держит небольшой «предзахват» (по умолчанию 0,3 с), чтобы первый слог не пропадал,
даже если клавишу нажали чуть позже, чем начали говорить.
"""
from __future__ import annotations

import datetime
import queue
import re
import threading
from collections import deque
from pathlib import Path

import numpy as np

FORMATS = {"wav": ("WAV", "PCM_16", "WAV — без потерь, крупный"),
           "flac": ("FLAC", "PCM_16", "FLAC — без потерь, в 2 раза меньше"),
           "mp3": ("MP3", "MPEG_LAYER_III", "MP3 — компактный")}
SLOT_PRESETS = {
    "": "Не назначать",
    "ctrl+alt+digits": "Ctrl+Alt+1 … 9",
    "alt+num": "Alt+Num 1 … 9",
    "ctrl+shift+f": "Ctrl+Shift+F1 … F12",
}


def slot_combos(preset: str) -> list[str]:
    if preset == "ctrl+alt+digits":
        return [f"ctrl+alt+{i}" for i in range(1, 10)]
    if preset == "alt+num":
        return [f"alt+num {i}" for i in range(1, 10)]
    if preset == "ctrl+shift+f":
        return [f"ctrl+shift+f{i}" for i in range(1, 13)]
    return []


def default_folder() -> str:
    home = Path.home()
    for name in ("Music", "Музыка"):
        if (home / name).is_dir():
            return str(home / name / "Мяукало — записи")
    return str(home / "Мяукало — записи")


def rec_defaults(mode: str) -> dict:
    return {
        "folder": default_folder(), "fmt": "wav",          # файл
        "channel": "",                                     # клип: в какой канал класть новый звук
        "trim": True, "normalize": True, "preroll_ms": 300,
        "max_s": 30 if mode == "clip" else 0,              # 0 — без ограничения
        "add_pad": mode == "clip",                         # добавить результат в звуки саундпада
        "slots": "",                                       # клавиши для новых записей
        "prefix": "Повтор" if mode == "replay" else "Запись",
        "replay_s": 30,                                    # повтор: сколько секунд помнить
    }


def available_formats() -> dict:
    try:
        import soundfile as sf
        have = sf.available_formats()
    except Exception:
        return {"wav": FORMATS["wav"]}
    return {k: v for k, v in FORMATS.items() if v[0] in have}


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "Запись"


def unique_path(folder: Path, stem: str, ext: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{stem}.{ext}"
    i = 2
    while p.exists():
        p = folder / f"{stem} ({i}).{ext}"
        i += 1
    return p


def process_clip(x: np.ndarray, sr: int, trim=True, normalize=True) -> np.ndarray | None:
    """Обрезать тишину по краям, выровнять громкость, сгладить края. None — записалась одна тишина."""
    if len(x) == 0:
        return None
    x = np.clip(x.astype(np.float32), -1.0, 1.0)
    mono = np.abs(x).max(axis=1)
    peak = float(mono.max())
    if peak < 0.003:                                    # ниже −50 дБ — это тишина
        return None
    if trim:
        win = max(1, int(sr * 0.005))
        env = np.convolve(mono, np.ones(win, np.float32) / win, mode="same")
        thr = max(0.004, peak * 0.04)                   # −48 дБ или −28 дБ от пика, что больше
        loud = np.nonzero(env > thr)[0]
        if len(loud) == 0:
            return None
        a = max(0, loud[0] - int(sr * 0.03))
        b = min(len(x), loud[-1] + int(sr * 0.12))
        x = x[a:b]
    if normalize:
        p = float(np.abs(x).max())
        if p > 1e-4:
            x = x * (0.89 / p)                           # пик −1 дБ
    n_in, n_out = min(len(x), int(sr * 0.005)), min(len(x), int(sr * 0.02))
    if n_in:
        x[:n_in] *= np.linspace(0, 1, n_in, dtype=np.float32)[:, None]
    if n_out:
        x[-n_out:] *= np.linspace(1, 0, n_out, dtype=np.float32)[:, None]
    return x


class Recorder:
    """Запись одного блока. feed() зовётся микшером каждые 10 мс, тяжёлая работа — в своём потоке."""

    def __init__(self, out_id: str, sr: int):
        self.out_id = out_id
        self.sr = sr
        self.mode = "file"
        self.cfg = rec_defaults("file")
        self.name = "Запись"
        self.on = False
        self.frames = 0
        self.level = 0.0
        self.last_path = ""
        self.error = ""
        self.busy = False                 # идёт сохранение
        self.on_done = None               # колбэк(dict) — запись сохранена / ошибка
        self._pre = deque()
        self._pre_n = 0
        self._blocks: list = []
        self._q: queue.Queue | None = None
        self._lock = threading.Lock()
        self._ring = None                 # повтор: кольцевой буфер int16
        self._w = 0
        self._filled = 0

    REPLAY_MAX = 600                       # не больше 10 минут (≈ 110 МБ памяти)

    def configure(self, name: str, mode: str, cfg: dict):
        self.name = name
        self.mode = mode if mode in ("file", "clip", "replay") else "file"
        base = rec_defaults(self.mode)
        base.update(cfg or {})
        self.cfg = base
        if self.mode == "replay":
            cap = int(self.sr * max(2, min(self.REPLAY_MAX, float(base.get("replay_s", 30)))))
            if self._ring is None or len(self._ring) != cap:
                self._ring = np.zeros((cap, 2), np.int16)
                self._w = 0
                self._filled = 0
        else:
            self._ring = None

    def buffered(self) -> float:
        return self._filled / self.sr if self._ring is not None else 0.0

    def capacity(self) -> float:
        return len(self._ring) / self.sr if self._ring is not None else 0.0

    # ------------------------------------------------------------ поток микшера
    def feed(self, mix: np.ndarray):
        n = len(mix)
        self.level = max(float(np.abs(mix).max()) if n else 0.0, self.level * 0.85)
        if self._ring is not None:                    # повтор: пишем всегда, по кругу
            ring = self._ring
            cap = len(ring)
            blk = (np.clip(mix, -1.0, 1.0) * 32767).astype(np.int16)
            end = self._w + n
            if end <= cap:
                ring[self._w:end] = blk
            else:
                k = cap - self._w
                ring[self._w:] = blk[:k]
                ring[:n - k] = blk[k:]
            self._w = end % cap
            self._filled = min(cap, self._filled + n)
            return
        if not self.on:
            keep = int(self.sr * max(0, int(self.cfg.get("preroll_ms", 300))) / 1000)
            if keep <= 0:
                return
            self._pre.append(mix.copy())
            self._pre_n += n
            while self._pre and self._pre_n - len(self._pre[0]) >= keep:
                self._pre_n -= len(self._pre.popleft())
            return
        blk = mix.copy()
        q = self._q
        if q is not None:
            q.put(blk)
        else:
            self._blocks.append(blk)
        self.frames += n
        mx = float(self.cfg.get("max_s") or 0)
        if mx > 0 and self.frames >= mx * self.sr:
            self.stop()

    # ------------------------------------------------------------ управление
    def seconds(self) -> float:
        return self.frames / self.sr

    def save_replay(self) -> bool:
        """Повтор: забрать последние секунды из буфера и сохранить в фоне."""
        if self._ring is None or self._filled < self.sr // 10:
            self._fail("буфер повтора пока пуст — в блок ещё не пришёл звук")
            return False
        if self._filled < len(self._ring):
            snap = self._ring[:self._filled].copy()
        else:
            snap = np.concatenate([self._ring[self._w:], self._ring[:self._w]])
        self.busy = True
        threading.Thread(target=self._save_clip, args=([snap.astype(np.float32) / 32767.0],),
                         daemon=True, name="rec-replay").start()
        return True

    def start(self) -> bool:
        if self.mode == "replay":
            return self.save_replay()
        with self._lock:
            if self.on:
                return False
            self.error = ""
            self.frames = 0
            pre = list(self._pre) if self.mode == "clip" or self.cfg.get("preroll_ms") else []
            self._pre.clear()
            self._pre_n = 0
            if self.mode == "file":
                fmt = self.cfg.get("fmt", "wav")
                fmts = available_formats()
                if fmt not in fmts:
                    fmt = "wav"
                stamp = datetime.datetime.now().strftime("%Y-%m-%d %H-%M-%S")
                try:
                    path = unique_path(Path(self.cfg.get("folder") or default_folder()), _safe(f"{self.name} {stamp}"), fmt)
                except Exception as e:  # noqa: BLE001
                    self._fail(f"не удалось создать папку: {e}")
                    return False
                self._q = queue.Queue()
                for b in pre:
                    self._q.put(b)
                    self.frames += len(b)
                t = threading.Thread(target=self._writer, args=(path, fmts[fmt], self._q), daemon=True, name="rec-file")
                self.on = self.busy = True
                t.start()
            else:
                self._q = None
                self._blocks = pre
                self.frames = sum(len(b) for b in pre)
                self.on = True
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self.on:
                return False
            self.on = False
            if self._q is not None:
                self._q.put(None)               # писатель закроет файл и сообщит
                self._q = None
            else:
                blocks, self._blocks = self._blocks, []
                self.busy = True
                threading.Thread(target=self._save_clip, args=(blocks,), daemon=True, name="rec-clip").start()
            return True

    def toggle(self):
        if self.mode == "replay":
            self.save_replay()
            return
        if self.on:
            self.stop()
        else:
            self.start()

    def abort(self):
        """Выход из программы / удаление блока: файл закрываем, клип не сохраняем."""
        with self._lock:
            if self._q is not None:
                self._q.put(None)
                self._q = None
            self._blocks = []
            self.on = False

    # ------------------------------------------------------------ фоновые потоки
    def _emit(self, ev: dict):
        cb = self.on_done
        if cb:
            try:
                cb(ev)
            except Exception:
                pass

    def _fail(self, msg):
        self.error = msg
        self._emit({"rec": self.out_id, "kind": "error", "error": msg, "name": self.name})

    def _writer(self, path: Path, fmt, q: queue.Queue):
        import soundfile as sf
        frames = 0
        try:
            with sf.SoundFile(str(path), "w", samplerate=self.sr, channels=2, format=fmt[0], subtype=fmt[1]) as f:
                while True:
                    b = q.get()
                    if b is None:
                        break
                    f.write(np.clip(b, -1.0, 1.0))
                    frames += len(b)
        except Exception as e:  # noqa: BLE001
            self.busy = False
            self.on = False
            self._fail(f"запись в файл прервалась: {e}")
            return
        self.busy = False
        self.last_path = str(path)
        self._emit({"rec": self.out_id, "kind": "file", "path": str(path), "seconds": frames / self.sr,
                    "name": self.name, "add_pad": bool(self.cfg.get("add_pad")), "cfg": dict(self.cfg)})

    def _save_clip(self, blocks):
        try:
            x = np.concatenate(blocks) if blocks else np.zeros((0, 2), np.float32)
            y = process_clip(x, self.sr, self.cfg.get("trim", True), self.cfg.get("normalize", True))
            if y is None:
                self._fail("записалась тишина — проверьте, что в блок записи протянут провод и звук идёт")
                return
            from app.model import data_dir
            import soundfile as sf
            folder = data_dir() / "library" / "Записи"
            stem = _safe(f"{self.cfg.get('prefix') or 'Запись'} {datetime.datetime.now():%H-%M-%S}")
            path = unique_path(folder, stem, "wav")
            sf.write(str(path), y, self.sr, subtype="PCM_16")
            self.last_path = str(path)
            self._emit({"rec": self.out_id, "kind": "clip", "path": str(path), "seconds": len(y) / self.sr,
                        "name": self.name, "add_pad": True, "cfg": dict(self.cfg)})
        except Exception as e:  # noqa: BLE001
            self._fail(f"не удалось сохранить запись: {e}")
        finally:
            self.busy = False
