"""Встроенный плеер: плейлист, который играет в выбранный канал (и дальше — по проводам канала)."""
from __future__ import annotations

import random

import numpy as np

from .decoder import SR

PLAYER_ID = "__player"
OPS = {
    "play_pause": "Играть / пауза", "play": "Играть", "pause": "Пауза", "stop": "Стоп",
    "next": "Следующий трек", "prev": "Предыдущий трек", "vol_up": "Громкость плеера +", "vol_down": "Громкость плеера −",
    "seek_fwd": "Перемотка +10 с", "seek_back": "Перемотка −10 с", "shuffle": "Перемешивание вкл/выкл",
    "repeat": "Повтор: всё / один / выкл",
}


def probe(path: str) -> dict:
    """Название и длительность трека без полного декодирования."""
    from pathlib import Path
    info = {"path": path, "title": Path(path).stem, "dur": 0.0}
    try:
        import av
        with av.open(path) as c:
            md = {k.lower(): v for k, v in (c.metadata or {}).items()}
            t, a = md.get("title"), md.get("artist")
            if t:
                info["title"] = f"{a} — {t}" if a else t
            if c.duration:
                info["dur"] = float(c.duration) / 1_000_000
    except Exception:
        pass
    return info


class Player:
    def __init__(self, engine):
        from .engine import SampleVoice  # noqa: F401  (проверка, что движок загружен)
        self.engine = engine
        self.voice = None            # текущий голос
        self.state = "stopped"       # stopped | playing | paused | loading
        self.paused_pos = None       # позиция, с которой продолжить
        self.pending = None          # индекс трека, который ждёт декодирования
        self.order: list[int] = []
        self.error = ""
        self._xfaded = False

    # ------------------------------------------------------------ настройки
    @property
    def cfg(self):
        return self.engine.project.player

    def _playlist(self):
        return self.cfg.playlist

    def current(self):
        pl = self._playlist()
        i = self.cfg.index
        return pl[i] if 0 <= i < len(pl) else None

    def _rt(self):
        return self.engine.channels.get(self.cfg.channel)

    # ------------------------------------------------------------ порядок треков
    def _reorder(self):
        n = len(self._playlist())
        self.order = list(range(n))
        if self.cfg.shuffle and n > 1:
            cur = self.cfg.index
            rest = [i for i in self.order if i != cur]
            random.shuffle(rest)
            self.order = ([cur] if 0 <= cur < n else []) + rest

    def _neighbor(self, step):
        n = len(self._playlist())
        if not n:
            return None
        if len(self.order) != n:
            self._reorder()
        cur = self.cfg.index if 0 <= self.cfg.index < n else 0
        k = self.order.index(cur) if cur in self.order else 0
        k2 = k + step
        if 0 <= k2 < n:
            return self.order[k2]
        if self.cfg.repeat == "all":
            if self.cfg.shuffle and step > 0:
                self._reorder()
                return self.order[1] if n > 1 else self.order[0]
            return self.order[k2 % n]
        return None

    # ------------------------------------------------------------ команды
    def command(self, op):
        cfg = self.cfg
        with self.engine.lock:
            if op == "play_pause":
                self.pause() if self.state == "playing" else self.play()
            elif op == "play":
                self.play()
            elif op == "pause":
                self.pause()
            elif op == "stop":
                self.stop()
            elif op == "next":
                self.skip(1)
            elif op == "prev":
                self.skip(-1)
            elif op == "vol_up":
                self.set_volume(cfg.volume + 0.05)
            elif op == "vol_down":
                self.set_volume(cfg.volume - 0.05)
            elif op == "seek_fwd":
                self.seek_rel(10)
            elif op == "seek_back":
                self.seek_rel(-10)
            elif op == "shuffle":
                cfg.shuffle = not cfg.shuffle
                self._reorder()
            elif op == "repeat":
                cfg.repeat = {"all": "one", "one": "off"}.get(cfg.repeat, "all")
        self.engine._changed()

    def play(self, index=None):
        """Без индекса: продолжить с паузы / начать текущий трек."""
        with self.engine.lock:
            pl = self._playlist()
            if not pl:
                return
            if index is None and self.state == "paused" and self.voice is not None and not self.voice.done:
                self.voice.paused = False
                self.state = "playing"
                return
            if index is not None:
                self.cfg.index = max(0, min(len(pl) - 1, int(index)))
                self.paused_pos = None
            elif not (0 <= self.cfg.index < len(pl)):
                self.cfg.index = 0
            start = self.paused_pos if (index is None and self.paused_pos) else 0
            self._start(self.cfg.index, start_sec=start, fade_in=0.01)

    def pause(self):
        with self.engine.lock:
            if self.voice is not None and self.state == "playing":
                self.voice.paused = True
                self.state = "paused"

    def stop(self):
        with self.engine.lock:
            self._kill(0.08)
            for rt in self.engine.channels.values():         # и затихающий при кроссфейде предыдущий трек
                for v in rt.voices:
                    if v.pad_id == PLAYER_ID:
                        v.paused = False
                        v.stop(int(0.08 * SR))
            self.state = "stopped"
            self.paused_pos = None
            self.pending = None

    def skip(self, step):
        with self.engine.lock:
            if step < 0 and self.position() > 3.0:          # «назад» в середине трека — в начало
                self.seek(0)
                return
            nxt = self._neighbor(step)
            if nxt is None:
                nxt = self.cfg.index if step < 0 else None
            if nxt is None:
                self.stop()
                return
            was_playing = self.state in ("playing", "loading")
            self.cfg.index = nxt
            self.paused_pos = None
            if was_playing or self.state == "stopped":
                self._start(nxt, fade_in=0.02, fade_out=0.05)
            else:                                            # на паузе — просто переключаем трек
                self._kill(0.02)
                self.state = "stopped"

    def seek(self, sec):
        with self.engine.lock:
            v = self.voice
            if v is not None and not v.done:
                v.pos = max(v.start, min(v.end - 1, v.start + int(sec * SR)))
                self._xfaded = False
            elif self.current():
                self.paused_pos = max(0.0, sec)

    def seek_rel(self, d):
        self.seek(self.position() + d)

    def set_volume(self, v):
        self.cfg.volume = max(0.0, min(1.0, round(v, 3)))
        if self.voice is not None:
            self.voice.gain = self._gain()

    def _gain(self):
        v = self.cfg.volume
        return v * v                                          # та же кривая, что у фейдеров

    # ------------------------------------------------------------ состояние для интерфейса
    def position(self):
        v = self.voice
        if v is not None and not v.done:
            return (v.pos - v.start) / SR
        return self.paused_pos or 0.0

    def duration(self):
        v = self.voice
        if v is not None and not v.done:
            return (v.end - v.start) / SR
        cur = self.current()
        return float(cur.get("dur") or 0.0) if cur else 0.0

    # ------------------------------------------------------------ внутреннее
    def _kill(self, fade):
        if self.voice is not None:
            self.voice.paused = False
            self.voice.stop(int(fade * SR))
            self.voice = None

    def _start(self, index, start_sec=0.0, fade_in=0.0, fade_out=0.05, crossfade=False):
        from .engine import SampleVoice
        pl = self._playlist()
        if not (0 <= index < len(pl)):
            return
        path = pl[index]["path"]
        cache = self.engine.cache
        data = cache.get(path)
        rt = self._rt()
        if data is None:
            err = cache.error(path)
            if err:
                self.error = f"не открывается: {pl[index].get('title', path)}"
                if self.engine.on_error:
                    self.engine.on_error("Плеер: " + self.error)
                self.pending = None
                nxt = self._neighbor(1)
                if nxt is not None and nxt != index:
                    self.cfg.index = nxt
                    self._start(nxt, fade_in=fade_in)
                else:
                    self.state = "stopped"
                return
            cache.request(path)
            self.pending = (index, start_sec)
            self.state = "loading"
            if not crossfade:
                self._kill(fade_out)
            return
        if rt is None:
            self.error = "не выбран канал"
            return
        self.pending = None
        if not crossfade:
            self._kill(fade_out)
        elif self.voice is not None:
            self.voice.stop(int(self.cfg.crossfade * SR))
        start = max(0, min(len(data) - 1, int(start_sec * SR)))
        fi = int(max(fade_in, 0.004) * SR)
        v = PlayerVoice(PLAYER_ID, rt.cfg.id, self._gain(), fi, frozenset(), data, 0, len(data), False)
        v.pos = start                     # начало трека — 0, играем с нужного места (продолжение после паузы)
        rt.voices.append(v)
        self.voice = v
        self.cfg.index = index
        self.state = "playing"
        self.paused_pos = None
        self.error = ""
        self._xfaded = False
        nxt = self._neighbor(1)                               # заранее декодируем следующий
        if nxt is not None:
            cache.request(pl[nxt]["path"])
        self._forget_old()

    def _forget_old(self):
        """Держим в памяти только текущий и следующий треки — музыка весит много."""
        pl = self._playlist()
        keep = set()
        cur = self.current()
        if cur:
            keep.add(cur["path"])
        nxt = self._neighbor(1)
        if nxt is not None:
            keep.add(pl[nxt]["path"])
        keep |= {p.file for p in self.engine.project.pads if p.kind == "file"}   # файлы звуков пэдов не трогаем
        for it in pl:
            if it["path"] not in keep:
                self.engine.cache.forget(it["path"])

    def tick(self, n):
        """Вызывается микшером каждый блок (под блокировкой движка)."""
        if self.engine.project is None:
            return
        if self.pending is not None:
            idx, st = self.pending
            path = self._playlist()[idx]["path"] if idx < len(self._playlist()) else None
            if path is None:
                self.pending = None
            elif self.engine.cache.get(path) is not None or self.engine.cache.error(path):
                self._start(idx, start_sec=st, fade_in=0.01)
            return
        v = self.voice
        if v is None or self.state != "playing":
            return
        rt = self._rt()
        if rt is not None and v not in rt.voices and not v.done:     # канал плеера сменили на ходу
            for other in self.engine.channels.values():
                if v in other.voices:
                    other.voices.remove(v)
            v.channel_id = rt.cfg.id
            rt.voices.append(v)
        if v.done or v.stopping:
            if v.ended:                                       # трек доиграл сам
                self.voice = None
                self._advance(crossfade=False)
            elif v.stopping and not self._xfaded:            # остановлен извне: «Стоп всё», переход канала
                self.paused_pos = (v.pos - v.start) / SR
                self.voice = None
                self.state = "paused" if self.paused_pos > 0.2 else "stopped"
            return
        xf = self.cfg.crossfade
        if xf > 0 and not self._xfaded and self.cfg.repeat != "one":
            remaining = (v.end - v.pos) / SR
            if remaining <= xf:
                nxt = self._neighbor(1)
                if nxt is not None and self.engine.cache.get(self._playlist()[nxt]["path"]) is not None:
                    self._xfaded = True
                    old = self.voice
                    self.cfg.index = nxt
                    self._start(nxt, fade_in=xf, crossfade=True)
                    if old is not None and not old.stopping:
                        old.stop(int(xf * SR))

    def _advance(self, crossfade):
        if self.cfg.repeat == "one":
            self._start(self.cfg.index, fade_in=0.004)
            return
        nxt = self._neighbor(1)
        if nxt is None:
            self.state = "stopped"
            self.paused_pos = None
            return
        self._start(nxt, fade_in=0.004)


from .engine import SampleVoice  # noqa: E402


class PlayerVoice(SampleVoice):
    """Голос плеера: умеет паузу и знает, доиграл ли трек сам."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.paused = False
        self.ended = False

    def render_into(self, mix, n):
        if self.paused and self.stopping:         # остановили на паузе — сразу заканчиваем
            self.done = True
            return
        if self.paused or self.done:
            return
        before = self.done
        super().render_into(mix, n)
        if self.done and not before and not self.stopping:
            self.ended = True
