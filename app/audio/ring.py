"""Простой потокобезопасный кольцевой буфер для стерео float32."""
from __future__ import annotations

import threading

import numpy as np


class Ring:
    def __init__(self, capacity: int, channels: int = 2):
        self.cap = int(capacity)
        self.buf = np.zeros((self.cap, channels), np.float32)
        self.r = 0
        self.w = 0
        self.fill = 0
        self.lock = threading.Lock()
        self.underruns = 0
        self.overruns = 0
        self.chunk = 0          # самый крупный кусок, которым сюда пишут (для расчёта запаса)

    def available(self) -> int:
        return self.fill

    def write(self, data: np.ndarray):
        n = len(data)
        if n == 0:
            return
        if n > self.chunk:
            self.chunk = min(n, self.cap // 2)
        with self.lock:
            if n >= self.cap:
                data = data[-self.cap:]
                n = self.cap
            overflow = self.fill + n - self.cap
            if overflow > 0:  # выкидываем самое старое
                self.r = (self.r + overflow) % self.cap
                self.fill -= overflow
                self.overruns += 1
            end = self.w + n
            if end <= self.cap:
                self.buf[self.w:end] = data
            else:
                k = self.cap - self.w
                self.buf[self.w:] = data[:k]
                self.buf[:n - k] = data[k:]
            self.w = end % self.cap
            self.fill += n

    def read_into(self, out: np.ndarray) -> int:
        """Читает до len(out) кадров в out, остаток заполняет нулями. Возвращает прочитанное."""
        n = len(out)
        with self.lock:
            m = min(n, self.fill)
            end = self.r + m
            if end <= self.cap:
                out[:m] = self.buf[self.r:end]
            else:
                k = self.cap - self.r
                out[:k] = self.buf[self.r:]
                out[k:m] = self.buf[:m - k]
            self.r = end % self.cap
            self.fill -= m
        if m < n:
            out[m:] = 0
            self.underruns += 1
        return m

    def drop(self, n: int):
        with self.lock:
            n = min(n, self.fill)
            self.r = (self.r + n) % self.cap
            self.fill -= n

    def clear(self):
        with self.lock:
            self.r = self.w = self.fill = 0
