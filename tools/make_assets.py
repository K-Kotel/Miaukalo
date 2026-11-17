"""Генерирует стартовые звуки и иконку. Запускать один раз: python tools/make_assets.py"""
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 48000
OUT = Path(__file__).resolve().parent.parent / "assets"
(OUT / "sounds").mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(7)


def env(n, a=0.005, r=0.2):
    t = np.arange(n) / SR
    e = np.minimum(1, t / a) * np.exp(-t / r)
    return e


def saw(f, n, vib=0.0):
    t = np.arange(n) / SR
    ph = 2 * np.pi * f * t + vib * np.sin(2 * np.pi * 5.5 * t)
    s = np.zeros(n)
    for k in range(1, 14):
        s += np.sin(k * ph) / k * (0.85 ** k)
    return s


def lowpass(x, a=0.15):
    y = np.zeros_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += a * (v - acc)
        y[i] = acc
    return y


def highpass(x, a=0.9):
    return x - lowpass(x, 1 - a)


def norm(x, peak=0.85):
    return x / (np.abs(x).max() + 1e-9) * peak


def stereo(x, width=0.0):
    d = int(width * SR / 1000)
    l = x
    r = np.concatenate([np.zeros(d), x[:len(x) - d]]) if d else x
    return np.stack([l, r], axis=1).astype(np.float32)


def tadam():
    def chord(freqs, dur, attack=0.02):
        n = int(dur * SR)
        s = sum(saw(f, n, vib=0.08) for f in freqs)
        t = np.arange(n) / SR
        e = np.minimum(1, t / attack) * np.minimum(1, (dur - t) / 0.25)
        return s * e
    a = chord([392, 493.9, 587.3], 0.18)
    b = chord([523.3, 659.3, 784, 1046.5], 1.4, 0.03)
    gap = np.zeros(int(0.04 * SR))
    return stereo(norm(np.concatenate([a, gap, b])), 8)


def badumtss():
    def kick(dur=0.35):
        n = int(dur * SR)
        t = np.arange(n) / SR
        f = 50 + 90 * np.exp(-t * 30)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 0.002, 0.12)

    def tom(dur=0.25, f0=180):
        n = int(dur * SR)
        t = np.arange(n) / SR
        f = f0 * (1 + 0.4 * np.exp(-t * 40))
        return 0.8 * np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 0.002, 0.09) + 0.25 * rng.normal(size=n) * env(n, 0.001, 0.03)

    def cymbal(dur=1.6):
        n = int(dur * SR)
        nz = highpass(rng.normal(size=n), 0.97)
        return 0.9 * nz * env(n, 0.002, 0.45)

    beat = int(0.22 * SR)
    out = np.zeros(int(2.4 * SR))
    for start, s in ((0, tom(f0=200)), (beat, tom(f0=150)), (int(beat * 2.1), cymbal()), (int(beat * 2.1), 0.8 * kick())):
        out[start:start + len(s)] += s
    return stereo(norm(out), 0)


def drumroll():
    dur = 2.4
    n = int(dur * SR)
    out = np.zeros(n + SR * 2)
    t = 0.0
    while t < dur:
        k = int(0.05 * SR)
        hit = (rng.normal(size=k) * 0.8 + 0.4 * np.sin(2 * np.pi * 190 * np.arange(k) / SR)) * env(k, 0.001, 0.025)
        amp = 0.25 + 0.75 * (t / dur) ** 1.5
        i = int(t * SR)
        out[i:i + k] += hit * amp
        t += 0.045 - 0.012 * (t / dur)
    crash_n = int(1.8 * SR)
    crash = highpass(rng.normal(size=crash_n), 0.95) * env(crash_n, 0.002, 0.6)
    kick_n = int(0.4 * SR)
    tt = np.arange(kick_n) / SR
    kick = np.sin(2 * np.pi * np.cumsum(50 + 80 * np.exp(-tt * 25)) / SR) * env(kick_n, 0.002, 0.15)
    i = int(dur * SR)
    out[i:i + crash_n] += crash * 1.2
    out[i:i + kick_n] += kick
    return stereo(norm(out[:i + crash_n]), 6)


def airhorn():
    parts = []
    for dur in (0.22, 0.22, 0.9):
        n = int(dur * SR)
        s = saw(466, n) + 0.8 * saw(470, n) + 0.6 * saw(698, n)
        s = np.tanh(s * 2.5)
        t = np.arange(n) / SR
        e = np.minimum(1, t / 0.01) * np.minimum(1, (dur - t) / 0.04)
        parts.append(s * e)
        parts.append(np.zeros(int(0.07 * SR)))
    return stereo(norm(lowpass(np.concatenate(parts), 0.35)), 4)


SOUNDS = {
    "Та-дам!": tadam,
    "Ба-дум-тсс": badumtss,
    "Барабанная дробь": drumroll,
    "Горн": airhorn,
}


def make_icon():
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Pillow не установлен — иконка не создана")
        return
    S = 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, S - 8, S - 8), radius=56, fill=(26, 25, 37, 255))
    cols = [(139, 92, 246), (236, 72, 153), (34, 211, 238), (251, 191, 36)]
    pad = 40
    cell = (S - 2 * pad - 16) // 2
    for i, c in enumerate(cols):
        x = pad + (i % 2) * (cell + 16)
        y = pad + (i // 2) * (cell + 16)
        d.rounded_rectangle((x, y, x + cell, y + cell), radius=20, fill=c + (255,))
    img.save(OUT / "icon.png")
    img.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    for name, fn in SOUNDS.items():
        sf.write(OUT / "sounds" / f"{name}.wav", fn(), SR, subtype="PCM_16")
        print("ok", name)
    make_icon()
