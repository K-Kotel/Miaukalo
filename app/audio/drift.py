"""Компенсация расхождения часов между устройствами.

У каждой звуковой карты (и у VB-Cable, и у Bluetooth-наушников) свой кварц: «48 000 Гц» у одной — это
48 010 у другой. Если писать в них один и тот же поток, буфер одной постепенно пустеет, другой —
переполняется. Раньше это лечилось подрезкой буферов, и устройства с крупными блоками (Bluetooth,
часть USB) рвали звук всем остальным. Теперь каждый поток чуть-чуть (до ±0,5 %) подстраивает
скорость под своё устройство — незаметно на слух, без разрывов.
"""
from __future__ import annotations

import numpy as np

MAX_DEV = 0.005          # предел подстройки скорости (0,5 % ≈ 8 центов)
GAIN = 5e-6              # насколько сильно реагируем на отклонение заполнения буфера (на кадр)


def _hermite(buf: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """4-точечная интерполяция Эрмита. buf: (N, ch), pos: дробные индексы, 1 <= pos <= N-3."""
    i = pos.astype(np.int64)
    t = (pos - i).astype(np.float32)[:, None]
    xm1, x0, x1, x2 = buf[i - 1], buf[i], buf[i + 1], buf[i + 2]
    c1 = 0.5 * (x1 - xm1)
    c2 = xm1 - 2.5 * x0 + 2.0 * x1 - 0.5 * x2
    c3 = 0.5 * (x2 - xm1) + 1.5 * (x0 - x1)
    return ((c3 * t + c2) * t + c1) * t + x0


class Resampler:
    """Потоковый ресэмплер с переменным шагом (шаг = сколько входных кадров на один выходной)."""

    def __init__(self, channels: int = 2):
        self.ch = channels
        self.reset()

    def reset(self):
        self.buf = np.zeros((1, self.ch), np.float32)   # один кадр истории слева
        self.pos = 1.0

    def pending(self) -> int:
        """Сколько входных кадров ещё не использовано."""
        return max(0, len(self.buf) - int(self.pos))

    def push(self, x: np.ndarray):
        if len(x):
            self.buf = np.concatenate([self.buf, x.astype(np.float32, copy=False)])

    def available_out(self, step: float) -> int:
        # нужны соседи i-1..i+2, т.е. позиция p годится, пока p < len - 2
        span = len(self.buf) - 2 - self.pos
        if span <= 0:
            return 0
        return int(np.ceil(span / step - 1e-9))

    def need_in(self, n: int, step: float) -> int:
        """Сколько ещё входных кадров нужно, чтобы выдать n выходных."""
        if n <= 0:
            return 0
        last = int(self.pos + (n - 1) * step)
        return max(0, last + 3 - len(self.buf))

    def pull(self, n: int, step: float) -> np.ndarray:
        n = min(n, self.available_out(step))
        if n <= 0:
            return np.zeros((0, self.ch), np.float32)
        pos = self.pos + step * np.arange(n, dtype=np.float64)
        out = _hermite(self.buf, pos)
        self.pos += step * n
        drop = int(self.pos) - 1
        if drop > 0:
            self.buf = self.buf[drop:]
            self.pos -= drop
        return out


class DriftControl:
    """Держит заполнение буфера около цели, плавно подстраивая шаг ресэмплера."""

    def __init__(self, target: int):
        self.target = int(target)
        self.avg = float(target)
        self.step = 1.0

    def reset(self, fill: float):
        self.avg = float(fill)
        self.step = 1.0

    def update(self, fill: int, sign: float = 1.0) -> float:
        """fill — сколько кадров сейчас в буфере. sign=+1: буфер на стороне потребителя
        (выход — лишнее заполнение значит «писать медленнее»), -1: на стороне источника."""
        self.avg += (fill - self.avg) * 0.01          # сглаживание ~1 с: пилообразные блоки не обманут
        err = self.avg - self.target
        dev = max(-MAX_DEV, min(MAX_DEV, err * GAIN))
        self.step = 1.0 + sign * dev
        return self.step
