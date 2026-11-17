"""Монтаж с неразрушающими клипами и блочным сведением стерео в 48 кГц."""
from __future__ import annotations

import os
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field, replace
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf

from app.model import EffectSlot, new_id
from .decoder import SR, decode_file
from .effects import EffectChain
from .recorder import FORMATS

EXPORT_FORMATS = {
    "wav": ("WAV", *FORMATS["wav"][:2]),
    "mp3": ("MP3", *FORMATS["mp3"][:2]),
    "flac": ("FLAC", "FLAC", "PCM_24"),
    "ogg": ("Ogg Vorbis", "OGG", "VORBIS"),
    "opus": ("Opus", "OGG", "OPUS"),
    "m4a": ("M4A / AAC", "mp4", "aac"),
    "aiff": ("AIFF", "AIFF", "PCM_16"),
}


@dataclass
class Clip:
    path: str
    name: str
    start: int
    source_in: int
    source_out: int
    span: int
    length: int
    offset: int = 0
    reverse: bool = False
    id: str = field(default_factory=lambda: new_id("clip"))

    @property
    def end(self):
        return self.start + self.length

    @property
    def speed(self):
        return (self.source_out - self.source_in) / self.span

    @property
    def stretch_limits(self):
        return max(1, self.length // 4), self.length * 4

    def crop(self, start, end):
        """Оставить полуоткрытый интервал времени, не пересчитывая звук."""
        return replace(self, start=start, offset=self.offset + start - self.start, length=end - start)

    def flipped(self):
        return replace(self, offset=self.span - self.offset - self.length, reverse=not self.reverse)

    def stretched(self, length):
        source_len = self.source_out - self.source_in
        first = self.span - self.offset - self.length if self.reverse else self.offset
        a = self.source_in + min(source_len - 1, round(first * source_len / self.span))
        b = self.source_in + round((first + self.length) * source_len / self.span)
        return replace(self, source_in=a, source_out=max(a + 1, b), span=length, length=length, offset=0)


@dataclass
class EffectWindow:
    start: int
    end: int
    effects: list[EffectSlot] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id("window"))


@dataclass
class Track:
    name: str
    id: str = field(default_factory=lambda: new_id("track"))
    muted: bool = False
    solo: bool = False
    clips: list[Clip] = field(default_factory=list)
    points: list[tuple[int, float]] = field(default_factory=list)
    effects: list[EffectSlot] = field(default_factory=list)
    windows: list[EffectWindow] = field(default_factory=list)

    def set_point(self, frame, db, previous=None):
        self.points = sorted([(t, v) for t, v in self.points if t not in (frame, previous)] + [(frame, db)])

    @property
    def envelope(self):
        return [(0, 0.0), *self.points] if self.points and self.points[0][0] > 0 else self.points

    @property
    def end(self):
        return max([0, *(c.end for c in self.clips), *(w.end for w in self.windows),
                    *(t for t, _ in self.points)])


@dataclass
class EditorProject:
    tracks: list[Track] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data):
        def effects(items):
            return [EffectSlot.from_dict(item) for item in items]

        tracks = []
        for row in data.get("tracks", []):
            track = Track(row["name"], row["id"], row.get("muted", False), row.get("solo", False))
            track.clips = [Clip(**clip) for clip in row.get("clips", [])]
            track.points = sorted((int(t), float(v)) for t, v in row.get("points", []))
            track.effects = effects(row.get("effects", []))
            track.windows = [EffectWindow(w["start"], w["end"], effects(w["effects"]), w["id"])
                             for w in row.get("windows", [])]
            tracks.append(track)
        return cls(tracks)

    def to_dict(self):
        return asdict(self)

    @property
    def end(self):
        return max((track.end for track in self.tracks), default=0)


def waveform(path):
    """Пики нормализованного исходника, без загрузки всего файла в память."""
    with sf.SoundFile(path) as src:
        count = len(src)
        size = max(1, (count + 4095) // 4096)
        result = np.empty(((count + size - 1) // size, 2), dtype=np.int16)
        for i in range(0, len(result), 128):
            chunks = min(128, len(result) - i)
            data = src.read(chunks * size, dtype="int16", always_2d=True, fill_value=0)
            data = data.reshape(chunks, size * 2)
            result[i:i + chunks, 0] = data.min(axis=1)
            result[i:i + chunks, 1] = data.max(axis=1)
        return count, result


def import_clip(path, folder, start=0):
    audio = decode_file(str(path))
    if not len(audio):
        raise ValueError("В файле нет звука.")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (new_id("audio") + ".wav")
    with _atomic_output(target) as temp:
        sf.write(temp, audio, SR, format="WAV", subtype="PCM_16")
    return Clip(str(target), Path(path).stem, start, 0, len(audio), len(audio), len(audio))


def stretch_audio(audio, length, cancelled):
    """Меняет длительность с сохранением высоты тона (FFmpeg atempo)."""
    import av

    if len(audio) < 2048:
        # Слишком короткий фрагмент для окна atempo.
        indices = np.arange(length) * len(audio) / length
        return np.column_stack([np.interp(indices, np.arange(len(audio)), channel)
                                for channel in audio.T]).astype(np.float32)
    ratio = len(audio) / length
    time_base = Fraction(1, SR)
    graph = av.filter.Graph()
    node = graph.add_abuffer(sample_rate=SR, format="fltp", layout="stereo", time_base=time_base)
    while not 0.5 <= ratio <= 2.0:
        step = 0.5 if ratio < 0.5 else 2.0
        effect = graph.add("atempo", str(step))
        node.link_to(effect)
        node = effect
        ratio /= step
    effect = graph.add("atempo", str(ratio))
    node.link_to(effect)
    planar = graph.add("aformat", "sample_fmts=fltp:channel_layouts=stereo")
    effect.link_to(planar)
    planar.link_to(graph.add("abuffersink"))
    graph.configure()

    result = np.zeros((length, 2), dtype=np.float32)
    written = 0

    def drain():
        nonlocal written
        while written < length:
            try:
                block = graph.pull().to_ndarray().T
            except (av.error.BlockingIOError, av.error.EOFError):
                break
            count = min(length - written, len(block))
            result[written:written + count] = block[:count]
            written += count

    def push(block, pts):
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(block.T), format="fltp", layout="stereo")
        frame.sample_rate, frame.time_base, frame.pts = SR, time_base, pts
        graph.push(frame)
        drain()

    # Последний буфер тишины даёт фильтру вывести звук до требуемой границы.
    for position in range(0, len(audio), 8192):
        _check_cancel(cancelled)
        push(audio[position:position + 8192], position)
    push(np.zeros((8192, 2), np.float32), len(audio))
    graph.push(None)
    drain()
    return result


class RenderCancelled(Exception):
    pass


def _check_cancel(cancelled):
    if cancelled and cancelled():
        raise RenderCancelled()


def _chain(effects):
    chain = EffectChain()
    chain.sync(effects)
    return chain


def mix_blocks(project, start=0, end=None, cancelled=None):
    """Сводит дорожки последовательно 10-мс блоками, удерживая состояние эффектов."""
    end = project.end if end is None else end
    solo = any(t.solo for t in project.tracks)
    tracks = [t for t in project.tracks if not t.muted and (not solo or t.solo) and t.clips]
    with ExitStack() as files:
        sources = {}
        stretched = {}
        track_states = []
        for track in tracks:
            track_states.append((track, _chain(track.effects),
                                 [(w, _chain(w.effects)) for w in track.windows]))
            for clip in track.clips:
                _check_cancel(cancelled)
                if clip.start >= end:
                    continue
                if clip.path not in sources:
                    try:
                        sources[clip.path] = files.enter_context(sf.SoundFile(clip.path))
                    except (OSError, RuntimeError) as exc:
                        raise ValueError(f"Не открывается исходник «{clip.name}»: {exc}") from exc
                source = sources[clip.path]
                if clip.source_out > len(source) or source.samplerate != SR or source.channels != 2:
                    raise ValueError(f"Исходник «{clip.name}» изменился.")
                span = clip.source_out - clip.source_in
                key = clip.path, clip.source_in, clip.source_out, clip.span
                if clip.span != span and key not in stretched:
                    source.seek(clip.source_in)
                    stretched[key] = stretch_audio(source.read(span, dtype="float32", always_2d=True),
                                                   clip.span, cancelled)
        for pos in range(0, end, 480):
            _check_cancel(cancelled)
            stop = min(end, pos + 480)
            mix = np.zeros((stop - pos, 2), np.float32)
            for track, fx, windows in track_states:
                signal = np.zeros_like(mix)
                for clip in track.clips:
                    a, b = max(pos, clip.start), min(stop, clip.end)
                    if b <= a:
                        continue
                    offset = clip.offset + a - clip.start
                    count = b - a
                    if clip.reverse:
                        offset = clip.span - offset - count
                    key = clip.path, clip.source_in, clip.source_out, clip.span
                    data = stretched.get(key)
                    if data is None:
                        source = sources[clip.path]
                        source.seek(clip.source_in + offset)
                        part = source.read(count, dtype="float32", always_2d=True)
                    else:
                        part = data[offset:offset + count]
                    signal[a - pos:b - pos] += part[::-1] if clip.reverse else part
                signal = fx.process(signal)
                for window, window_fx in windows:
                    a, b = max(pos, window.start), min(stop, window.end)
                    if b > a and window_fx.active():
                        part = signal[a - pos:b - pos]
                        wet = window_fx.process(part.copy())
                        t = np.arange(a, b)
                        distance = np.minimum(t - window.start, window.end - 1 - t)
                        fade = max(1, min(SR // 200, (window.end - window.start - 1) // 2))
                        weight = np.minimum(1.0, distance / fade).astype(np.float32)[:, None]
                        part += (wet - part) * weight
                if track.points:
                    frames, levels = zip(*track.envelope)
                    db = np.interp(np.arange(pos, stop), frames, levels)
                    signal *= np.power(10.0, db / 20.0).astype(np.float32)[:, None]
                mix += signal
            if stop > start:
                yield mix[max(0, start - pos):]


@contextmanager
def _atomic_output(path):
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=path.suffix)
    os.close(fd)
    try:
        yield name
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def export_formats():
    import av

    formats = {}
    for suffix, (label, container, codec) in EXPORT_FORMATS.items():
        if suffix == "m4a":
            try:
                av.Codec(codec, "w")
            except (ValueError, av.FFmpegError):
                continue
        elif not sf.check_format(container, codec):
            continue
        formats[suffix] = label
    return formats


def _write_aac(path, blocks, container, codec):
    import av

    with av.open(path, "w", format=container) as out:
        stream = out.add_stream(codec, rate=SR)
        stream.layout = "stereo"
        stream.bit_rate = 192_000
        position = 0
        for block in blocks:
            frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(block.T), format="fltp", layout="stereo")
            frame.sample_rate, frame.time_base, frame.pts = SR, Fraction(1, SR), position
            position += len(block)
            out.mux(stream.encode(frame))
        out.mux(stream.encode(None))


def export_audio(project, path, cancelled=None):
    path = Path(path)
    suffix = path.suffix.lower().lstrip(".")
    if suffix not in export_formats():
        raise ValueError(f"Этот формат экспорта недоступен: {path.suffix}")
    _, container, codec = EXPORT_FORMATS[suffix]
    target = path.resolve()
    if any(Path(clip.path).resolve() == target for track in project.tracks for clip in track.clips):
        raise ValueError("Выберите другой файл, чтобы сохранить исходник монтажа.")
    with _atomic_output(path) as temp:
        blocks = (np.clip(block, -1.0, 1.0) for block in mix_blocks(project, cancelled=cancelled))
        if suffix == "m4a":
            _write_aac(temp, blocks, container, codec)
        else:
            with sf.SoundFile(temp, "w", samplerate=SR, channels=2, format=container, subtype=codec) as out:
                for block in blocks:
                    out.write(block)
        _check_cancel(cancelled)


def preview_audio(project, start, end, cancelled=None):
    with tempfile.TemporaryFile() as raw:
        for block in mix_blocks(project, start, end, cancelled):
            pcm = (np.clip(block, -1.0, 1.0) * 32767.0).astype(np.int16)
            raw.write(pcm.tobytes())
        _check_cancel(cancelled)
        raw.flush()
        return np.memmap(raw, np.int16, mode="r", shape=(end - start, 2))
