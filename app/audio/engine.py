"""Аудиодвижок: голоса → каналы (эффекты, громкость) → выходные устройства.

Схема:
  * Отдельный поток-микшер рендерит звук блоками по 10 мс.
  * Каждый канал суммирует свои голоса, прогоняет через цепочку эффектов и fader.
  * Для каждого выходного устройства складываются каналы, которые на него направлены,
    результат пишется в кольцевой буфер устройства, откуда его забирает аудиоколбэк.
  * Звук может быть исключён из части выходов (например, браузер не дублировать в
    свои колонки) — такие голоса идут через отдельную копию цепочки эффектов.
"""
from __future__ import annotations

import math
import threading
import time

import numpy as np

from .decoder import SR, SampleCache
from .devices import OutputDevice, rescan_portaudio
from .drift import DriftControl, Resampler
from .effects import EffectChain, random_chain
from .recorder import Recorder
from .sources import make_source

BLOCK = 480                 # 10 мс
TAIL_SECONDS = 6            # сколько ещё крутить эффекты после тишины (хвосты эха/реверба)
LIVE_PRIME = 1440           # 30 мс буфера живого входа перед стартом
LIVE_MAX = 9600             # на 200 мс больше нужного — выкидываем лишнее, чтобы не копилась задержка


def db_to_gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def fader_gain(v: float) -> float:
    """Положение ползунка 0..1 → усиление (квадратичная кривая звучит естественнее)."""
    v = max(0.0, min(1.0, v))
    return v * v


# ======================================================================= voices

class Voice:
    def __init__(self, pad_id, channel_id, gain, fade_in, exclude, delay=0):
        self.pad_id = pad_id
        self.channel_id = channel_id
        self.gain = gain
        self.exclude = exclude          # frozenset id выходов
        self.delay = int(delay)
        self.env = 0.0 if (fade_in > 0 or delay > 0) else 1.0
        self.target = 1.0
        self.rate = 1.0 / fade_in if fade_in > 0 else 1.0
        self.stopping = False
        self.done = False
        self.error = ""
        self.fx = None            # своя цепочка эффектов голоса (случайные эффекты)
        self.src_done = False     # звук закончился, доигрывают хвосты эффектов
        self.fx_tail = 0
        self.fx_names = []

    def stop(self, fade_frames: int):
        if self.done:
            return
        if self.delay > 0:      # ещё не начал звучать
            self.done = True
            return
        self.stopping = True
        self.target = 0.0
        self.rate = 1.0 / max(int(fade_frames), 96)

    def _envelope(self, n: int):
        if self.env == self.target:
            if self.stopping and self.env <= 0.0:
                self.done = True
            return self.env
        direction = 1.0 if self.target > self.env else -1.0
        ramp = self.env + direction * self.rate * np.arange(1, n + 1, dtype=np.float32)
        if direction > 0:
            np.minimum(ramp, self.target, out=ramp)
        else:
            np.maximum(ramp, self.target, out=ramp)
        self.env = float(ramp[-1])
        if self.stopping and self.env <= 0.0:
            self.done = True
        return ramp[:, None]

    def render_into(self, mix: np.ndarray, n: int):
        raise NotImplementedError

    def progress(self):
        return None

    def close(self):
        pass


class SampleVoice(Voice):
    def __init__(self, pad_id, channel_id, gain, fade_in, exclude, data, start, end, loop, delay=0):
        super().__init__(pad_id, channel_id, gain, fade_in, exclude, delay)
        self.data = data
        self.start = start
        self.end = end
        self.pos = start
        self.loop = loop
        self.dir = 1              # -1 — играет задом наперёд (пока держат клавишу)

    def _read_fwd(self, chunk, m):
        filled = 0
        while filled < m:
            avail = self.end - self.pos
            if avail <= 0:
                if self.loop and self.end > self.start:
                    self.pos = self.start
                    continue
                return filled, True
            k = min(avail, m - filled)
            chunk[filled:filled + k] = self.data[self.pos:self.pos + k]
            self.pos += k
            filled += k
        return filled, False

    def _read_back(self, chunk, m):
        filled = 0
        while filled < m:
            avail = self.pos - self.start
            if avail <= 0:
                if self.loop and self.end > self.start:
                    self.pos = self.end
                    continue
                break             # дошли до начала — молчим, пока не отпустят клавишу
            k = min(avail, m - filled)
            chunk[filled:filled + k] = self.data[self.pos - k:self.pos][::-1]
            self.pos -= k
            filled += k
        return filled, False

    def render_into(self, mix, n):
        if self.done:
            return
        offset = 0
        if self.delay > 0:
            offset = min(self.delay, n)
            self.delay -= offset
            if offset >= n:
                return
        m = n - offset
        chunk = np.empty((m, 2), np.float32)
        filled, ended = (self._read_back if self.dir < 0 else self._read_fwd)(chunk, m)
        if filled < m:
            chunk[filled:] = 0.0
        chunk *= self.gain / 32768.0
        chunk *= self._envelope(m)
        mix[offset:] += chunk
        if ended:
            self.done = True

    def progress(self):
        span = self.end - self.start
        return (self.pos - self.start) / span if span > 0 else 0.0


class LiveVoice(Voice):
    """Живой вход. Источник идёт по своим часам (микрофон, программа, вкладка браузера) — читаем
    с плавной подстройкой скорости, чтобы буфер не пустел и не копил задержку."""

    def __init__(self, pad_id, channel_id, gain, fade_in, exclude, source, delay=0):
        super().__init__(pad_id, channel_id, gain, fade_in, exclude, delay)
        self.source = source
        self.primed = False
        self.started = False
        self.level = 0.0
        self._rs = Resampler()
        self._ctl = DriftControl(LIVE_PRIME)
        self.extra = 0          # запас растёт сам, если источник присылает звук неровно

    def _target(self, ring):
        return max(LIVE_PRIME, int(ring.chunk * 1.5) + BLOCK) + self.extra

    def render_into(self, mix, n):
        if self.done:
            return
        if self.source.error:
            self.error = self.source.error
            self.done = True
            return
        offset = 0
        if self.delay > 0:
            offset = min(self.delay, n)
            self.delay -= offset
            if offset >= n:
                return
        m = n - offset
        ring = self.source.ring
        avail = ring.available()
        tgt = self._target(ring)
        self._ctl.target = tgt
        if not self.primed:
            if avail < tgt:
                self._envelope(m)  # огибающая всё равно движется (важно для остановки)
                return
            self.primed = True
            self._rs.reset()
            self._ctl.reset(avail)
        if avail > tgt + LIVE_MAX:
            ring.drop(avail - tgt)      # источник прислал сразу много (после паузы) — не копим задержку
            avail = tgt
            self._ctl.reset(avail)
        step = self._ctl.update(avail)
        need = self._rs.need_in(m, step)
        if need:
            take = np.empty((min(need, ring.available()), 2), np.float32)
            got = ring.read_into(take)
            self._rs.push(take[:got])
        chunk = self._rs.pull(m, step)
        if len(chunk) < m:                      # источник замолчал/запнулся — ждём, пока снова наберётся
            self.primed = False
            if avail > 0:                       # запнулся, а не замолчал: в следующий раз держим запас побольше
                self.extra = min(self.extra + BLOCK // 2, 3 * BLOCK)
            chunk = np.concatenate([chunk, np.zeros((m - len(chunk), 2), np.float32)])
        self.level = float(np.abs(chunk).max()) if m else 0.0
        chunk *= self.gain
        chunk *= self._envelope(m)
        mix[offset:] += chunk

    def close(self):
        src = self.source
        src.closed = True
        threading.Thread(target=src.stop, daemon=True).start()


class DrumVoice(Voice):
    """Постоянный «голос» дрампада в его канале: через него удары проходят эффекты и разводку канала."""

    def __init__(self, channel_id):
        super().__init__("__drums", channel_id, 1.0, 0, frozenset())
        self.buf = None

    def render_into(self, mix, n):
        if self.stopping:
            self.done = True
            return
        b, self.buf = self.buf, None
        if b is not None:
            mix += b


# ======================================================================= channels

class ChannelRT:
    def __init__(self, cfg):
        self.cfg = cfg
        self.voices: list[Voice] = []
        self.chains: dict[frozenset, EffectChain] = {}
        self.vol = fader_gain(0.0 if cfg.muted else cfg.volume)
        self.tail = 0
        self.peak = 0.0

    def sync(self, cfg):
        self.cfg = cfg
        for chain in self.chains.values():
            chain.sync(cfg.effects)

    def chain(self, mask) -> EffectChain:
        c = self.chains.get(mask)
        if c is None:
            c = EffectChain()
            c.sync(self.cfg.effects)
            self.chains[mask] = c
        return c

    @staticmethod
    def _render_fx_voice(v, buf, n):
        """Голос со своими эффектами: звук → его цепочка → канал. После конца звука доигрывают хвосты."""
        tmp = np.zeros((n, 2), np.float32)
        if not v.src_done:
            v.render_into(tmp, n)
            if v.done:
                v.src_done, v.done = True, False
                v.fx_tail = 4 * SR
        elif v.stopping:              # остановили во время хвоста — обрываем
            v.done = True
            return
        out = v.fx.process(tmp)
        buf += out
        if v.src_done:
            v.fx_tail -= n
            quiet = float(np.abs(out).max()) < 2e-4 if len(out) else True
            if v.fx_tail <= 0 or (quiet and v.fx_tail < 4 * SR - SR // 4):
                v.done = True

    def render(self, n: int, route_key=None, capture=None):
        """route_key(voice) -> ("ex", исключённые выходы) | ("own", свои выходы).
        Голоса с одинаковой разводкой сводятся вместе и проходят общую копию цепочки эффектов."""
        groups: dict[tuple, np.ndarray] = {}
        finished = []
        for v in self.voices:
            key = route_key(v) if route_key else ("ex", v.exclude)
            buf = groups.get(key)
            if buf is None:
                buf = groups[key] = np.zeros((n, 2), np.float32)
            tap = capture is not None and v.pad_id == capture[0]
            rendered = np.zeros_like(buf) if tap else buf
            if v.fx is None:
                v.render_into(rendered, n)
            else:
                self._render_fx_voice(v, rendered, n)
            if tap:
                buf += rendered
                capture[1][:] += rendered
            if v.done:
                finished.append(v)
        for v in finished:
            self.voices.remove(v)
            v.close()

        has_fx = any(c.active() for c in self.chains.values()) or (
            any(s.enabled for s in self.cfg.effects))
        if self.voices or groups:
            self.tail = TAIL_SECONDS * SR
        elif self.tail > 0:
            self.tail -= n
        if has_fx and self.tail > 0:
            for mask in list(self.chains):     # хвосты эха/реверба доигрывают даже без звуков
                if mask not in groups:
                    groups[mask] = np.zeros((n, 2), np.float32)

        target = 0.0 if self.cfg.muted else fader_gain(self.cfg.volume)
        if abs(target - self.vol) > 1e-6:
            gain = np.linspace(self.vol, target, n, dtype=np.float32)[:, None]
            self.vol = target
        else:
            gain = self.vol

        out = []
        peak = 0.0
        for mask, buf in groups.items():
            if has_fx:
                buf = self.chain(mask).process(buf, SR)
            buf = buf * gain
            p = float(np.abs(buf).max()) if len(buf) else 0.0
            peak = max(peak, p)
            out.append((mask, buf))
        self.peak = max(peak, self.peak * 0.85)
        return out


# ======================================================================= engine

class Engine:
    def __init__(self, cache: SampleCache | None = None):
        self.cache = cache or SampleCache()
        self.lock = threading.RLock()
        self.project = None
        self.channels: dict[str, ChannelRT] = {}
        self.outputs: dict[str, OutputDevice] = {}
        self.preview_voice: SampleVoice | None = None
        self.editor_recording: tuple[str, Recorder] | None = None
        self.pad_errors: dict[str, str] = {}
        self.on_change = None          # колбэк: что-то поменялось из горячей клавиши
        self.on_error = None           # колбэк(текст)
        self._running = False
        self._thread = None
        self._maint = None
        self.latency_frames = int(SR * 0.06)
        self.render_time = 0.0
        self.out_peaks: dict[str, float] = {}
        self.out_chains: dict[str, EffectChain] = {}   # эффекты на выходах и шинах
        from .player import Player
        self.player = Player(self)
        self.sw_gain: dict[tuple, float] = {}           # текущие уровни положений переключателей (для плавности)
        self.on_ui_action = None       # колбэк(имя) — горячая клавиша просит открыть окно
        self.recorders: dict = {}      # id блока записи → Recorder
        self.on_info = None            # колбэк(текст) — сообщение в строку состояния
        self._reversed: dict = {}      # (область, цель) → голоса, играющие задом наперёд
        self.out_gain: dict = {}       # текущее усиление выходов (плавное заглушение)
        from .drums import DrumMachine
        self.drums = DrumMachine(self)
        self._drum_voice = None
        self.on_record = None          # колбэк(dict) — запись сохранена / ошибка записи
        self.fake_clock = False        # для тестов: рендерить по времени без устройств

    # ----------------------------------------------------------- lifecycle
    def start(self, project):
        self.apply_project(project)
        self._running = True
        self._thread = threading.Thread(target=self._mixer_loop, daemon=True, name="mixer")
        self._thread.start()
        self._maint = threading.Thread(target=self._maintenance, daemon=True, name="audio-maint")
        self._maint.start()
        for pad in project.pads:
            if pad.autostart:
                self.pad_event(pad.id, "press")

    def shutdown(self):
        self._running = False
        if self._thread:
            self._thread.join(1)
        with self.lock:
            for r in self.recorders.values():   # длинная запись в файл: закрываем файл как следует
                if r.on and r.mode == "file":
                    r.stop()
                else:
                    r.abort()
            for ch in self.channels.values():
                for v in ch.voices:
                    v.close()
                ch.voices.clear()
            devs = list(self.outputs.values())
            self.outputs.clear()
            recs = list(self.recorders.values())
            if self.editor_recording:
                _, take = self.editor_recording
                take.stop()
                recs.append(take)
        for d in devs:
            d.stop()
        deadline = time.monotonic() + 4          # даём дописать и закрыть файлы записей
        while any(r.busy for r in recs) and time.monotonic() < deadline:
            time.sleep(0.05)

    def restart_devices(self):
        """Закрыть всё, пересканировать устройства, открыть снова."""
        with self.lock:
            devs = list(self.outputs.values())
            self.outputs.clear()
        for d in devs:
            d.stop()
        rescan_portaudio()
        if self.project:
            self.apply_project(self.project)

    # ----------------------------------------------------------- config
    def apply_project(self, project):
        self.project = project
        if self.drums.cfg is not project.drum:
            self.drums.attach(project.drum)
        self.latency_frames = int(SR * max(20, min(400, project.settings.latency_ms)) / 1000)
        wanted = {o.id: o for o in project.outputs if o.enabled and not o.bus}
        to_start = []
        to_stop = []
        with self.lock:
            # каналы
            ids = {c.id for c in project.channels}
            for cid in list(self.channels):
                if cid not in ids:
                    for v in self.channels[cid].voices:
                        v.close()
                    del self.channels[cid]
            for c in project.channels:
                rt = self.channels.get(c.id)
                if rt is None:
                    self.channels[c.id] = ChannelRT(c)
                else:
                    rt.sync(c)
            # голоса удалённых звуков
            pad_ids = {p.id for p in project.pads}
            for rt in self.channels.values():
                for v in rt.voices:
                    if v.pad_id not in pad_ids:
                        v.stop(int(0.05 * SR))
            # эффекты выходов / шин
            ids = {o.id for o in project.outputs}
            for oid in list(self.out_chains):
                if oid not in ids:
                    del self.out_chains[oid]
            for o in project.outputs:
                if o.effects or o.id in self.out_chains:
                    self.out_chains.setdefault(o.id, EffectChain()).sync(o.effects)
            # блоки записи
            recs = {o.id: o for o in project.outputs if o.recorder}
            for rid in list(self.recorders):
                if rid not in recs:
                    r = self.recorders.pop(rid)
                    r.stop() if r.mode == "file" else r.abort()
            for rid, o in recs.items():
                r = self.recorders.get(rid)
                if r is None:
                    r = self.recorders[rid] = Recorder(rid, SR)
                    r.on_done = self._record_done
                if not r.on:                      # во время записи режим не меняем
                    r.configure(o.name, o.recorder, o.rec)
                else:
                    r.name = o.name
            # выходы
            for oid, dev in list(self.outputs.items()):
                o = wanted.get(oid)
                if o is None or o.device != dev.name or o.hostapi != dev.hostapi:
                    to_stop.append(self.outputs.pop(oid))
                else:
                    dev.volume = 1.0  # громкость выхода применяется в микшере (до пересылок)
                    dev.latency = self.latency_frames
            for oid, o in wanted.items():
                if oid not in self.outputs:
                    dev = OutputDevice(oid, o.device, o.hostapi, SR)
                    dev.volume = 1.0  # громкость выхода применяется в микшере (до пересылок)
                    dev.latency = self.latency_frames
                    to_start.append(dev)
        for d in to_stop:
            d.stop()
        for d in to_start:
            d.start()
            if d.error and self.on_error:
                self.on_error(f"Выход «{wanted[d.out_id].name}»: {d.error}")
        with self.lock:
            for d in to_start:
                self.outputs[d.out_id] = d

    # ----------------------------------------------------------- pads
    def pad_event(self, pad_id: str, kind: str):
        """kind: press | release | repeat."""
        if kind == "repeat" or self.project is None:
            return
        pad = self.project.pad(pad_id)
        if pad is None:
            return
        with self.lock:
            rt = self.channels.get(pad.channel)
            if rt is None:
                return
            active = [v for v in rt.voices if v.pad_id == pad_id and not v.stopping]
            fade_out = int(pad.fade_out_ms * SR / 1000)
            if pad.mode == "hold":
                if kind == "press":
                    if not active:
                        self._start_voice(pad, rt)
                else:
                    for v in active:
                        v.stop(fade_out)
            elif pad.mode == "toggle":
                if kind == "press":
                    if active:
                        for v in active:
                            v.stop(fade_out)
                    else:
                        self._start_voice(pad, rt)
            else:  # oneshot
                if kind != "press":
                    return
                if not active:
                    self._start_voice(pad, rt)
                elif pad.retrigger == "restart":
                    for v in active:
                        v.stop(int(0.012 * SR))
                    self._start_voice(pad, rt, from_restart=True)
                elif pad.retrigger == "overlap":
                    self._start_voice(pad, rt, from_restart=True)
                elif pad.retrigger == "stop":
                    for v in active:
                        v.stop(fade_out)
        self._changed()

    def _start_voice(self, pad, rt: ChannelRT, from_restart=False, fx=None):
        ch = rt.cfg
        exclude = frozenset(pad.exclude_outputs)
        fade_in = int(pad.fade_in_ms * SR / 1000)
        delay = 0
        others = [v for v in rt.voices if v.pad_id != pad.id and not v.stopping and v.pad_id != "__drums"]
        if ch.transition != "overlay" and others:
            t = int(ch.transition_ms * SR / 1000)
            if ch.transition == "cut":
                for v in others:
                    v.stop(int(0.015 * SR))
            elif ch.transition == "crossfade":
                for v in others:
                    v.stop(t)
                fade_in = max(fade_in, t)
            elif ch.transition == "fade_out_in":
                for v in others:
                    v.stop(t)
                delay = t
        gain = db_to_gain(pad.gain_db)
        if pad.kind == "live":
            src = make_source(pad.source, SR)
            voice = LiveVoice(pad.id, ch.id, gain, max(fade_in, int(0.02 * SR)), exclude, src, delay)

            def boot():
                if not src.start():
                    voice.error = src.error or "источник не запустился"
                    self.pad_errors[pad.id] = voice.error
                    if self.on_error:
                        self.on_error(f"«{pad.name}»: {voice.error}")
                    with self.lock:
                        voice.done = True
                    self._changed()
                else:
                    self.pad_errors.pop(pad.id, None)
                    if src.closed:          # пока запускался, звук успели выключить
                        src.stop()

            threading.Thread(target=boot, daemon=True).start()
        else:
            data = self.cache.get(pad.file)
            if data is None:
                err = self.cache.error(pad.file)
                if err:
                    self.pad_errors[pad.id] = err
                    if self.on_error:
                        self.on_error(f"«{pad.name}»: не удалось открыть файл ({err})")
                else:
                    self.cache.request(pad.file)
                return
            start = max(0, min(len(data) - 1, int(pad.trim_start * SR)))
            end = int(pad.trim_end * SR) if pad.trim_end > 0 else len(data)
            end = max(start + 1, min(len(data), end))
            if fade_in == 0 and start > 0:
                fade_in = int(0.004 * SR)  # убираем щелчок при старте с середины волны
            voice = SampleVoice(pad.id, ch.id, gain, fade_in, exclude, data, start, end, pad.loop, delay)
        if fx is not None:
            voice.fx, voice.fx_names = fx
        rt.voices.append(voice)
        return voice

    # ----------------------------------------------------------- случайные эффекты / задом наперёд
    def play_random_fx(self, pad_id, count=3, strength=0.7, pool="all"):
        """Сыграть звук через N случайных эффектов со случайными настройками. Возвращает их названия."""
        pad = self.project.pad(pad_id) if self.project else None
        if pad is None:
            return []
        chain, names = random_chain(int(count), float(strength), pool)
        with self.lock:
            rt = self.channels.get(pad.channel)
            if rt is None:
                return []
            active = [v for v in rt.voices if v.pad_id == pad_id and not v.stopping]
            if pad.kind == "live" and active:          # живой источник: повторное нажатие выключает
                for v in active:
                    v.stop(int(pad.fade_out_ms * SR / 1000))
                self._changed()
                return []
            if pad.retrigger != "overlap":
                for v in active:
                    v.stop(int(0.012 * SR))
            self._start_voice(pad, rt, from_restart=bool(active), fx=(chain, names))
        self._changed()
        if names and self.on_info:
            self.on_info(f"«{pad.name}» + " + ", ".join(names))
        return names

    def _reverse_targets(self, scope, target):
        res = []
        for rt in self.channels.values():
            if scope == "channel" and rt.cfg.id != target:
                continue
            for v in rt.voices:
                if isinstance(v, SampleVoice) and not v.stopping and not v.src_done:
                    if scope == "pad" and v.pad_id != target:
                        continue
                    res.append(v)
        if scope in ("all", "") and self.preview_voice is not None:
            res.append(self.preview_voice)
        return res

    def reverse(self, scope, target, on):
        """Играющие сейчас звуки — задом наперёд (on=True) / снова вперёд (on=False)."""
        key = (scope, target)
        with self.lock:
            if on:
                vs = self._reverse_targets(scope, target)
                for v in vs:
                    v.dir = -1
                self._reversed[key] = vs
            else:
                for v in self._reversed.pop(key, []):
                    v.dir = 1
        self._changed()

    def reverse_active(self, scope, target) -> bool:
        return (scope, target) in self._reversed

    def set_out_muted(self, out_id, muted):
        o = self.project.output(out_id) if self.project else None
        if o is not None:
            o.muted = bool(muted)
            self._changed()

    def stop_pad(self, pad_id, fade_ms=None):
        pad = self.project.pad(pad_id) if self.project else None
        fade = int((pad.fade_out_ms if (pad and fade_ms is None) else (fade_ms or 30)) * SR / 1000)
        with self.lock:
            for rt in self.channels.values():
                for v in rt.voices:
                    if v.pad_id == pad_id:
                        v.stop(fade)
        self._changed()

    def stop_channel(self, channel_id, fade_ms=150):
        with self.lock:
            rt = self.channels.get(channel_id)
            if rt:
                for v in rt.voices:
                    v.stop(int(fade_ms * SR / 1000))
        self._changed()

    def stop_all(self, fade_ms=150, keep_autostart=True):
        self.drums.stop()
        auto = {p.id for p in self.project.pads if p.autostart} if (self.project and keep_autostart) else set()
        with self.lock:
            for rt in self.channels.values():
                for v in rt.voices:
                    if v.pad_id not in auto:
                        v.stop(int(fade_ms * SR / 1000))
            if self.preview_voice:
                self.preview_voice.stop(int(0.02 * SR))
        self._changed()

    # ----------------------------------------------------------- mixer controls
    def set_volume(self, channel_id, value):
        ch = self.project.channel(channel_id) if self.project else None
        if ch:
            ch.volume = max(0.0, min(1.0, float(value)))
            self._changed()

    def step_volume(self, channel_id, step):
        ch = self.project.channel(channel_id) if self.project else None
        if ch:
            ch.volume = max(0.0, min(1.0, round(ch.volume + step, 3)))
            if step > 0 and ch.muted:
                ch.muted = False
            self._changed()

    def set_muted(self, channel_id, muted):
        ch = self.project.channel(channel_id) if self.project else None
        if ch:
            ch.muted = bool(muted)
            self._changed()

    def fx_owner(self, owner_id):
        """Канал или выход, у которого есть цепочка эффектов."""
        if not self.project:
            return None
        return self.project.channel(owner_id) or self.project.output(owner_id)

    def set_effect(self, owner_id, slot_id, enabled):
        owner = self.fx_owner(owner_id)
        if not owner:
            return
        for s in owner.effects:
            if s.id == slot_id:
                s.enabled = bool(enabled)
        with self.lock:
            rt = self.channels.get(owner_id)
            if rt:
                for chain in rt.chains.values():
                    chain.set_enabled(slot_id, bool(enabled))
            oc = self.out_chains.get(owner_id)
            if oc:
                oc.set_enabled(slot_id, bool(enabled))
        self._changed()

    def sync_effects(self, owner_id):
        """После правки параметров эффектов в интерфейсе."""
        with self.lock:
            rt = self.channels.get(owner_id)
            if rt:
                rt.sync(self.project.channel(owner_id))
            o = self.project.output(owner_id) if self.project else None
            if o is not None:
                self.out_chains.setdefault(owner_id, EffectChain()).sync(o.effects)

    # ----------------------------------------------------------- переключатели
    def switch_set(self, sw_id, index):
        sw = self.project.output(sw_id) if self.project else None
        if not sw or not sw.switch:
            return
        n = len(self.project.switch_ports(sw))
        sw.active = -1 if index is None or index < 0 or index >= n else int(index)
        self._changed()

    def switch_step(self, sw_id, delta):
        sw = self.project.output(sw_id) if self.project else None
        if not sw or not sw.switch:
            return
        n = len(self.project.switch_ports(sw))
        if n:
            sw.active = (max(sw.active, 0 if delta > 0 else n) + delta) % n if sw.active >= 0 else (0 if delta > 0 else n - 1)
            self._changed()

    def _gate(self, sw, key, n, cache):
        """Уровень положения переключателя на этот блок: число или плавная рампа."""
        ck = (sw.id, key)
        g = cache.get(ck)
        if g is not None:
            return g
        ports = self.project.switch_ports(sw)
        active_key = ports[sw.active] if 0 <= sw.active < len(ports) else None
        target = 1.0 if key == active_key else 0.0
        cur = self.sw_gain.get(ck, target)
        if cur == target:
            g = target
        else:
            step = n / max(1.0, sw.fade_ms * SR / 1000)
            new = min(target, cur + step) if target > cur else max(target, cur - step)
            g = np.linspace(cur, new, n, dtype=np.float32)[:, None]
            self.sw_gain[ck] = new
            cur = new
        if isinstance(g, float):
            self.sw_gain[ck] = g
        cache[ck] = g
        return g

    # ----------------------------------------------------------- запись
    def _record_done(self, ev):
        if self.on_record:
            self.on_record(ev)

    def rec_command(self, rec_id, op):
        """op: start | stop | toggle."""
        with self.lock:
            r = self.recorders.get(rec_id)
            if r is None:
                return
            if op in ("start", "save"):
                r.start()
            elif op == "stop":
                r.stop()
            else:
                r.toggle()
        self._changed()

    def rec_states(self) -> dict:
        with self.lock:
            return {rid: {"on": r.on, "t": r.seconds(), "busy": r.busy, "level": r.level, "error": r.error,
                          "mode": r.mode, "buf": r.buffered(), "cap": r.capacity()}
                    for rid, r in self.recorders.items()}

    def start_editor_recording(self, source_id, folder, on_done):
        with self.lock:
            channel = self.project.channel(source_id)
            pad = self.project.pad(source_id)
            output = self.project.output(source_id)
            if channel is None and pad is None and (output is None or not output.enabled):
                raise ValueError("Источник записи больше недоступен.")
            if self.editor_recording:
                _, previous = self.editor_recording
                if previous.on or previous.busy:
                    raise ValueError("Предыдущая запись ещё не завершилась.")
            source = channel or pad or output
            take = Recorder(source_id, SR)
            take.configure(f"Запись — {source.name}", "file", {
                "folder": str(folder), "preroll_ms": 0, "add_pad": False,
            })
            take.on_done = on_done
            if take.start():
                self.editor_recording = (source_id, take)

    def stop_editor_recording(self):
        with self.lock:
            if self.editor_recording:
                self.editor_recording[1].stop()

    def editor_recording_state(self):
        with self.lock:
            if self.editor_recording:
                take = self.editor_recording[1]
                return take.seconds(), take.level
            return 0.0, 0.0

    # ----------------------------------------------------------- preview
    def preview_hardware_output(self):
        with self.lock:
            candidates = [o.id for o in self.project.outputs
                          if o.enabled and o.id in self.outputs and not self.outputs[o.id].error]
            preferred = self.project.settings.preview_output
            return preferred if preferred in candidates else next(iter(candidates), None)

    def preview(self, data, start, end, gain_db=0.0, loop=False, output_id=None):
        with self.lock:
            if self.preview_voice:
                self.preview_voice.stop(int(0.01 * SR))
            self.preview_voice = SampleVoice("__preview", output_id or "", db_to_gain(gain_db), int(0.004 * SR),
                                             frozenset(), data, start, end, loop)
            return self.preview_voice

    def stop_preview(self, voice=None):
        with self.lock:
            if self.preview_voice and (voice is None or self.preview_voice is voice):
                self.preview_voice.stop(int(0.02 * SR))

    def preview_pos(self, voice=None):
        v = self.preview_voice
        if v is None or v.done or (voice is not None and v is not voice):
            return None
        return v.pos

    # ----------------------------------------------------------- state for UI
    def pad_states(self) -> dict:
        res = {}
        with self.lock:
            for rt in self.channels.values():
                for v in rt.voices:
                    if v.stopping and v.env < 0.05:
                        continue
                    prog = v.progress()
                    cur = res.get(v.pad_id)
                    if cur is None or (prog is not None and (cur is True or prog < cur)):
                        res[v.pad_id] = prog if prog is not None else True
        return res

    def channel_peaks(self) -> dict:
        with self.lock:
            return {cid: rt.peak for cid, rt in self.channels.items()}

    def output_peaks(self) -> dict:
        with self.lock:
            return dict(self.out_peaks)

    def device_status(self) -> dict:
        with self.lock:
            return {oid: d.error for oid, d in self.outputs.items()}

    def _changed(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    # ----------------------------------------------------------- mixer thread
    def render_block(self, n=BLOCK) -> dict:
        """Рендерит один блок и раскладывает по выходам. Возвращает {out_id: mix} (для тестов)."""
        with self.lock:
            mixes = {}
            if self.project:
                try:
                    self.player.tick(n)
                except Exception as e:  # noqa: BLE001
                    self.player.error = str(e)
                dclick = None
                try:
                    dbuf, dclick = self.drums.render(n)
                except Exception as e:  # noqa: BLE001
                    dbuf = None
                    if self.on_error:
                        self.on_error(f"Дрампад: {e}")
                if dbuf is not None:
                    rt = self.channels.get(self.drums.cfg.get("channel", ""))
                    if rt is None and self.project.channels:
                        rt = self.channels.get(self.project.channels[0].id)
                    if rt is not None:
                        dv = self._drum_voice
                        if dv is None or dv.done or dv not in rt.voices:
                            dv = self._drum_voice = DrumVoice(rt.cfg.id)
                            rt.voices.append(dv)
                        dv.buf = dbuf
                order = [o for o in self.project.output_order() if o.enabled]
                mixes = {o.id: np.zeros((n, 2), np.float32) for o in order}
                merges = {o.id: o for o in order if o.switch == "merge"}
                gates: dict = {}
                pads = {p.id: p for p in self.project.pads}
                take_id, take = self.editor_recording or (None, None)
                tap_pad = None
                if take is not None and take.on:
                    tap_pad = pads.get(take_id)
                    if tap_pad is None and take_id not in mixes and take_id not in self.channels:
                        take.stop()
                tap_signal = np.zeros((n, 2), np.float32) if tap_pad is not None else None

                def route_key(v, pads=pads):
                    pad = pads.get(v.pad_id)
                    if pad is None:
                        return ("ex", frozenset())
                    if pad.route_own:
                        return ("own", frozenset(pad.outputs), pad.id)
                    return ("ex", frozenset(pad.exclude_outputs))
                for c in self.project.channels:
                    rt = self.channels.get(c.id)
                    if rt is None:
                        continue
                    capture = (tap_pad.id, tap_signal) if tap_pad is not None and tap_pad.channel == c.id else None
                    parts = rt.render(n, route_key, capture)
                    if take is not None and take.on and take_id == c.id:
                        signal = np.zeros((n, 2), np.float32)
                        for _, part in parts:
                            signal += part
                        take.feed(signal)
                    if not parts:
                        continue
                    for key, buf in parts:
                        kind, ids = key[0], key[1]
                        if kind == "own":
                            targets, src_key = ids, key[2]
                        else:
                            targets, src_key = [o for o in c.outputs if o not in ids], c.id
                        for oid in targets:
                            mix = mixes.get(oid)
                            if mix is None:
                                continue
                            sw = merges.get(oid)
                            if sw is not None:                 # переключатель N→1: пропускает только активный вход
                                g = self._gate(sw, src_key, n, gates)
                                if isinstance(g, float):
                                    if g > 0:
                                        mix += buf * g if g != 1.0 else buf
                                else:
                                    mix += buf * g
                            else:
                                mix += buf
                if tap_signal is not None:
                    take.feed(tap_signal)
                # громкость выхода + пересылки «выход → выход» (по порядку, без петель)
                peaks = self.out_peaks
                for o in order:
                    mix = mixes[o.id]
                    oc = self.out_chains.get(o.id)
                    if oc is not None and oc.active():
                        mix = oc.process(mix)
                        mixes[o.id] = mix
                    tgt = 0.0 if getattr(o, "muted", False) else o.volume
                    cur = self.out_gain.get(o.id, tgt)
                    if cur != tgt:                         # заглушение без щелчка (~10 мс)
                        mix *= np.linspace(cur, tgt, n, dtype=np.float32)[:, None]
                        self.out_gain[o.id] = tgt
                    elif tgt != 1.0:
                        mix *= tgt
                    self.out_gain[o.id] = tgt
                    p = float(np.abs(mix).max())
                    peaks[o.id] = max(p, peaks.get(o.id, 0.0) * 0.85)
                    if take is not None and take.on and take_id == o.id:
                        take.feed(mix)
                    if o.recorder:
                        rec = self.recorders.get(o.id)
                        if rec is not None:
                            rec.feed(mix)
                        continue
                    for t in o.sends:
                        dst = mixes.get(t)
                        if dst is None:
                            continue
                        g = 1.0
                        if o.switch == "split":                # переключатель 1→N: только в активный выход
                            g = self._gate(o, t, n, gates)
                        tsw = merges.get(t)
                        if tsw is not None:
                            g2 = self._gate(tsw, o.id, n, gates)
                            g = g * g2
                        if isinstance(g, float):
                            if g > 0:
                                dst += mix if g == 1.0 else mix * g
                        else:
                            dst += mix * g
                if dclick is not None:                     # метроном — только себе в наушники
                    target = mixes.get(self.project.settings.preview_output)
                    if target is not None:
                        target += dclick
                pv = self.preview_voice
                if pv is not None:
                    target = mixes.get(pv.channel_id or self.project.settings.preview_output)
                    if pv.channel_id and (target is None or pv.channel_id not in self.outputs):
                        pv.done = True
                    elif target is None and mixes:
                        target = next(iter(mixes.values()))
                    if target is not None:
                        pv.render_into(target, n)
                    if pv.done:
                        self.preview_voice = None
            devs = list(self.outputs.values())
        for d in devs:
            mix = mixes.get(d.out_id)
            if mix is None:
                mix = np.zeros((n, 2), np.float32)
            np.clip(mix, -1.0, 1.0, out=mix)
            push = getattr(d, "push", None)
            if push is not None:
                push(mix)
            else:
                d.ring.write(mix)
        return mixes

    def _mixer_loop(self):
        """Такт микшера — системные часы, ровно каждые 10 мс. Каждое устройство само подстраивается
        под этот такт (devices.OutputDevice.push), поэтому устройства с разными часами и разными
        размерами блоков больше не мешают друг другу."""
        clock = time.perf_counter
        _enable_timer_resolution()
        period = BLOCK / SR
        next_t = clock()
        busy = 0.0
        window_start = clock()
        while self._running:
            now = clock()
            if now >= next_t:
                t0 = clock()
                try:
                    self.render_block()
                except Exception as e:  # noqa: BLE001
                    if self.on_error:
                        self.on_error(f"Ошибка микшера: {e}")
                    time.sleep(0.05)
                next_t += period
                if clock() - next_t > 0.2:      # система подвисла — не догоняем рывком, начинаем заново
                    next_t = clock()
                busy += clock() - t0
            else:
                time.sleep(min(0.002, max(0.0005, next_t - now - 0.0005)))
            if now - window_start >= 1.0:
                self.render_time = busy / (now - window_start)
                busy = 0.0
                window_start = now

    def _maintenance(self):
        """Переподключает отвалившиеся устройства."""
        while self._running:
            time.sleep(3)
            with self.lock:
                broken = [d for d in self.outputs.values() if d.error and time.monotonic() - d.last_try > 5]
            for d in broken:
                d.start()
            if broken:
                self._changed()


def _enable_timer_resolution():
    try:
        import sys
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass
