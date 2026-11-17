"""Загрузка аудиофайлов в общий формат движка: 48 кГц, стерео, int16.

int16 вдвое экономнее float32 — пятиминутный трек занимает ~55 МБ вместо 110.
"""
from __future__ import annotations

import threading

import numpy as np

SR = 48000

AUDIO_FILTER = "Аудио (*.mp3 *.wav *.ogg *.flac *.m4a *.aac *.opus *.webm *.mp4 *.wma *.aiff *.aif);;Все файлы (*)"


def _to_stereo(a: np.ndarray) -> np.ndarray:
    if a.ndim == 1:
        a = a[:, None]
    if a.shape[1] == 1:
        return np.repeat(a, 2, axis=1)
    if a.shape[1] > 2:
        return a[:, :2]
    return a


def _decode_soundfile(path: str):
    import soundfile as sf
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return data, sr


def _decode_av(path: str):
    import av
    chunks = []
    with av.open(path) as container:
        stream = next(s for s in container.streams if s.type == "audio")
        resampler = av.AudioResampler(format="flt", layout="stereo", rate=SR)
        for frame in container.decode(stream):
            for f in resampler.resample(frame):
                chunks.append(f.to_ndarray().reshape(-1, 2))
        for f in resampler.resample(None):
            chunks.append(f.to_ndarray().reshape(-1, 2))
    if not chunks:
        raise ValueError("в файле нет звука")
    return np.concatenate(chunks).astype(np.float32), SR


def decode_file(path: str) -> np.ndarray:
    """Возвращает массив int16 формы (N, 2) с частотой SR."""
    try:
        data, sr = _decode_soundfile(path)
    except Exception:
        data, sr = _decode_av(path)
    data = _to_stereo(data)
    if sr != SR:
        import soxr
        data = soxr.resample(data, sr, SR, quality="HQ")
    data = np.clip(data, -1.0, 1.0)
    return np.ascontiguousarray((data * 32767.0).astype(np.int16))


class SampleCache:
    """Кэш декодированных файлов. Загружает в фоне, чтобы интерфейс не подвисал."""

    def __init__(self):
        self._data: dict[str, np.ndarray] = {}
        self._errors: dict[str, str] = {}
        self._loading: set[str] = set()
        self._lock = threading.Lock()
        self.on_loaded = None  # callback(path)

    def get(self, path: str):
        with self._lock:
            return self._data.get(path)

    def error(self, path: str):
        with self._lock:
            return self._errors.get(path)

    def is_loading(self, path: str) -> bool:
        with self._lock:
            return path in self._loading

    def request(self, path: str):
        if not path:
            return
        with self._lock:
            if path in self._data or path in self._loading:
                return
            self._loading.add(path)
            self._errors.pop(path, None)
        threading.Thread(target=self._load, args=(path,), daemon=True).start()

    def load_now(self, path: str) -> np.ndarray:
        d = self.get(path)
        if d is not None:
            return d
        d = decode_file(path)
        with self._lock:
            self._data[path] = d
        return d

    def _load(self, path: str):
        try:
            d = decode_file(path)
            with self._lock:
                self._data[path] = d
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._errors[path] = str(e) or e.__class__.__name__
        finally:
            with self._lock:
                self._loading.discard(path)
            if self.on_loaded:
                try:
                    self.on_loaded(path)
                except Exception:
                    pass

    def forget(self, path: str):
        with self._lock:
            self._data.pop(path, None)
            self._errors.pop(path, None)


def peaks(data: np.ndarray, buckets: int) -> np.ndarray:
    """Пики для отрисовки волны: массив (buckets, 2) с min/max в диапазоне -1..1."""
    if data is None or len(data) == 0 or buckets <= 0:
        return np.zeros((max(buckets, 1), 2), np.float32)
    mono = data.max(axis=1).astype(np.float32) / 32768.0
    mono_min = data.min(axis=1).astype(np.float32) / 32768.0
    n = len(mono)
    edges = np.linspace(0, n, buckets + 1).astype(np.int64)
    out = np.zeros((buckets, 2), np.float32)
    for i in range(buckets):
        a, b = edges[i], max(edges[i + 1], edges[i] + 1)
        out[i, 0] = mono_min[a:b].min()
        out[i, 1] = mono[a:b].max()
    return out
