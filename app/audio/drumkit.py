"""Встроенные наборы ударных — синтезируются программой (никаких чужих сэмплов и лицензий).

Каждый набор — 16 звуков в раскладке как у MPC: пэд 1 (левый нижний) — бочка, дальше малый,
хэты, клэп, томы, перкуссия, тарелки. Звук — float32 стерео 48 кГц.
"""
from __future__ import annotations

import numpy as np

try:
    import pedalboard as pb
except Exception:  # pragma: no cover
    pb = None

SR = 48000
_CACHE: dict = {}


# ------------------------------------------------------------------ кирпичики
def _t(sec):
    return np.arange(int(sec * SR)) / SR


def _env(sec, decay, attack=0.0015):
    t = _t(sec)
    e = np.exp(-t / max(decay, 1e-4))
    if attack > 0:
        e *= np.minimum(1.0, t / attack)
    return e


def _sweep(sec, f0, f1, tau):
    t = _t(sec)
    f = f1 + (f0 - f1) * np.exp(-t / tau)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def _noise(sec, seed):
    return np.random.default_rng(seed).uniform(-1, 1, int(sec * SR))


def _fx(x, *plugins):
    if pb is None:
        return x
    y = pb.Pedalboard(list(plugins))(x.astype(np.float32)[None, :], SR)[0]
    return y.astype(np.float64)


def _hp(x, f):
    return _fx(x, pb.HighpassFilter(cutoff_frequency_hz=f)) if pb else x


def _lp(x, f):
    return _fx(x, pb.LowpassFilter(cutoff_frequency_hz=f)) if pb else x


def _bp(x, f, res=0.5):
    if pb is None:
        return x
    return _fx(x, pb.LadderFilter(mode=pb.LadderFilter.Mode.BPF12, cutoff_hz=f, resonance=res, drive=1.0))


def _metal(sec, freqs=(205.3, 304.4, 369.6, 522.7, 540.0, 800.0)):
    """Металл как у 808: шесть «квадратов» с нечётными соотношениями."""
    t = _t(sec)
    return sum(np.sign(np.sin(2 * np.pi * f * t)) for f in freqs) / len(freqs)


def _norm(x, peak=0.9):
    m = float(np.abs(x).max()) or 1.0
    y = x / m * peak
    n = min(len(y), int(0.004 * SR))
    if n:
        y[-n:] *= np.linspace(1, 0, n)
    return y


def _sat(x, drive=2.0):
    return np.tanh(x * drive) / np.tanh(drive)


# ------------------------------------------------------------------ звуки
def kick808(tune=48.0, length=0.9):
    body = _sweep(length, tune * 3.2, tune, 0.035) * _env(length, length * 0.38)
    click = _hp(_noise(0.01, 1), 1500) * _env(0.01, 0.002) * 0.4
    body[:len(click)] += click
    return _norm(_sat(body, 1.4), 0.95)


def kick_studio():
    x = _sweep(0.5, 180, 55, 0.025) * _env(0.5, 0.16)
    beater = _bp(_noise(0.03, 2), 3000, 0.3) * _env(0.03, 0.006) * 0.9
    x[:len(beater)] += beater
    return _norm(_sat(x, 2.2), 0.95)


def snare808():
    tone = (np.sin(2 * np.pi * 238 * _t(0.3)) * 0.6 + np.sin(2 * np.pi * 476 * _t(0.3)) * 0.3) * _env(0.3, 0.05)
    nz = _hp(_lp(_noise(0.3, 3), 9000), 1800) * _env(0.3, 0.07)
    return _norm(tone * 0.7 + nz)


def snare_studio():
    tone = _sweep(0.45, 330, 185, 0.02) * _env(0.45, 0.06)
    nz = _bp(_noise(0.45, 4), 5200, 0.2) * _env(0.45, 0.13)
    crack = _hp(_noise(0.02, 5), 2000) * _env(0.02, 0.004)
    x = tone * 0.65 + nz
    x[:len(crack)] += crack
    return _norm(_sat(x, 1.6))


def clap(seed=6, tail=0.18):
    n = int(0.35 * SR)
    x = np.zeros(n)
    nz = _bp(_noise(0.35, seed), 1300, 0.45)
    for k, d in enumerate((0.0, 0.011, 0.022)):
        a = int(d * SR)
        e = _env(0.012, 0.004)
        x[a:a + len(e)] += nz[a:a + len(e)] * e
    a = int(0.031 * SR)
    e = _env(0.35 - 0.031, tail)
    x[a:a + len(e)] += nz[a:a + len(e)] * e[:n - a]
    return _norm(x)


def hat_closed808():
    return _norm(_hp(_bp(_metal(0.12), 9000, 0.3), 7000) * _env(0.12, 0.018), 0.7)


def hat_open808():
    return _norm(_hp(_bp(_metal(0.7), 9000, 0.3), 7000) * _env(0.7, 0.22), 0.65)


def hat_closed_studio():
    x = _hp(_noise(0.1, 7) * 0.6 + _metal(0.1, (317, 431, 587, 811, 1007, 1347)) * 0.6, 7500)
    return _norm(x * _env(0.1, 0.014), 0.65)


def hat_open_studio():
    x = _hp(_noise(0.9, 8) * 0.6 + _metal(0.9, (317, 431, 587, 811, 1007, 1347)) * 0.6, 6500)
    return _norm(x * _env(0.9, 0.3), 0.6)


def hat_pedal():
    x = _hp(_noise(0.08, 9), 5000) * _env(0.08, 0.012, attack=0.004)
    return _norm(x, 0.5)


def tom(f, length=0.6, seed=10):
    x = _sweep(length, f * 1.6, f, 0.06) * _env(length, length * 0.35)
    x += _bp(_noise(length, seed), f * 4, 0.2) * _env(length, 0.02) * 0.25
    return _norm(_sat(x, 1.3), 0.85)


def rim():
    t = _t(0.06)
    x = (np.sin(2 * np.pi * 1700 * t) * 0.6 + np.sin(2 * np.pi * 480 * t)) * _env(0.06, 0.008)
    return _norm(_hp(x, 300), 0.7)


def cowbell():
    x = (np.sign(np.sin(2 * np.pi * 540 * _t(0.5))) + np.sign(np.sin(2 * np.pi * 800 * _t(0.5)))) * 0.5
    x = _bp(x, 900, 0.5) * (_env(0.5, 0.03) * 0.6 + _env(0.5, 0.18) * 0.4)
    return _norm(x, 0.6)


def cymbal(length=2.0, seed=11, bright=6000):
    x = _hp(_noise(length, seed) * 0.5 + _metal(length, (271, 389, 503, 677, 913, 1201)) * 0.5, bright)
    return _norm(x * _env(length, length * 0.35, attack=0.002), 0.6)


def ride():
    x = cymbal(2.4, 12, 4500) * 0.7
    ping = np.sin(2 * np.pi * 2900 * _t(2.4)) * _env(2.4, 0.5) * 0.25
    return _norm(x + ping, 0.55)


def shaker(seed=13):
    x = _hp(_noise(0.14, seed), 5000) * np.sin(np.pi * np.linspace(0, 1, int(0.14 * SR))) ** 2
    return _norm(x, 0.45)


def tambourine():
    x = _hp(_noise(0.4, 14), 6000) * _env(0.4, 0.09)
    jingles = _metal(0.4, (2300, 3170, 4110, 5630, 6890, 7700)) * _env(0.4, 0.07) * 0.5
    return _norm(_hp(x + jingles, 4000), 0.5)


def clave(f=2500):
    return _norm(np.sin(2 * np.pi * f * _t(0.08)) * _env(0.08, 0.012), 0.6)


def conga(f, seed=15):
    x = _sweep(0.4, f * 1.25, f, 0.015) * _env(0.4, 0.12)
    x += _bp(_noise(0.4, seed), f * 3, 0.3) * _env(0.4, 0.01) * 0.3
    return _norm(x, 0.75)


def woodblock(f=1100):
    t = _t(0.12)
    x = (np.sin(2 * np.pi * f * t) + 0.4 * np.sin(2 * np.pi * f * 2.7 * t)) * _env(0.12, 0.02)
    return _norm(x, 0.6)


def triangle():
    t = _t(1.6)
    x = sum(np.sin(2 * np.pi * f * t) * a for f, a in ((1900, 1.0), (5300, 0.5), (8600, 0.3))) * _env(1.6, 0.5)
    return _norm(x, 0.45)


def snap():
    x = _bp(_noise(0.15, 16), 2200, 0.5) * _env(0.15, 0.025, attack=0.0008)
    return _norm(x, 0.7)


def bass808(note=36, length=1.4):
    f = 440 * 2 ** ((note - 69) / 12)
    x = _sweep(length, f * 2.2, f, 0.02) * _env(length, length * 0.5)
    return _norm(_sat(x, 2.0), 0.9)


def guiro():
    n = int(0.35 * SR)
    x = np.zeros(n)
    for k in range(14):
        a = int(k * 0.022 * SR)
        e = _env(0.015, 0.004)
        b = min(n, a + len(e))
        x[a:b] += _noise(0.015, 30 + k)[:b - a] * e[:b - a]
    return _norm(_bp(x, 3500, 0.4), 0.5)


def lofi(x):
    if pb is None:
        return x
    y = _fx(x, pb.Resample(target_sample_rate=12000), pb.Bitcrush(bit_depth=10),
            pb.LowpassFilter(cutoff_frequency_hz=7000), pb.Distortion(drive_db=6))
    return _norm(y, float(np.abs(x).max()) * 0.95)


# ------------------------------------------------------------------ наборы
# (название, функция). Порядок = пэды 1…16 (1 — левый нижний).
def _kit_808():
    return [("Кик 808", kick808), ("Снейр 808", snare808), ("Закр. хэт", hat_closed808), ("Откр. хэт", hat_open808),
            ("Клэп", clap), ("Римшот", rim), ("Том низкий", lambda: tom(90, 0.7)), ("Том высокий", lambda: tom(160, 0.5)),
            ("Ковбелл", cowbell), ("Клава", clave), ("Конга", lambda: conga(330)), ("Маракас", shaker),
            ("808-бас C", lambda: bass808(36)), ("808-бас G", lambda: bass808(43)), ("Тарелка", cymbal),
            ("Кик длинный", lambda: kick808(44, 1.6))]


def _kit_studio():
    return [("Бочка", kick_studio), ("Малый", snare_studio), ("Закр. хэт", hat_closed_studio),
            ("Откр. хэт", hat_open_studio), ("Клэп", lambda: clap(17, 0.12)), ("Римшот", rim),
            ("Том 1", lambda: tom(180, 0.45, 18)), ("Том 2", lambda: tom(125, 0.55, 19)),
            ("Напольный том", lambda: tom(85, 0.7, 20)), ("Педаль хэта", hat_pedal), ("Тамбурин", tambourine),
            ("Щелчок", snap), ("Шейкер", shaker), ("Ковбелл", cowbell), ("Крэш", lambda: cymbal(2.2, 21, 5000)),
            ("Райд", ride)]


def _kit_lofi():
    base = _kit_studio()
    swap = {"Щелчок": ("Виниловый треск", lambda: _norm(_hp(_noise(0.3, 40) ** 9, 1500), 0.3)),
            "Ковбелл": ("Бас-нота", lambda: bass808(40, 1.0))}
    out = []
    for name, fn in base:
        name, fn = swap.get(name, (name, fn))
        out.append((name, (lambda fn=fn: lofi(fn()))))
    return out


def _kit_perc():
    return [("Конга низ", lambda: conga(200, 22)), ("Конга верх", lambda: conga(300, 23)),
            ("Бонго низ", lambda: conga(380, 24)), ("Бонго верх", lambda: conga(520, 25)),
            ("Клава", clave), ("Вудблок", woodblock), ("Ковбелл", cowbell), ("Треугольник", triangle),
            ("Шейкер", shaker), ("Тамбурин", tambourine), ("Агого низ", lambda: woodblock(700)),
            ("Агого верх", lambda: woodblock(1050)), ("Кабаса", lambda: shaker(26)), ("Гуиро", guiro),
            ("Тимбалес", lambda: tom(420, 0.4, 27)), ("Хлопок", lambda: clap(28, 0.1))]


KITS = {"808": ("808 — электронный", _kit_808), "studio": ("Студийный", _kit_studio),
        "lofi": ("Lo-fi", _kit_lofi), "perc": ("Перкуссия", _kit_perc)}


def kit_names(kit: str) -> list[str]:
    return [n for n, _ in KITS[kit][1]()]


def sound(kit: str, slot: int) -> np.ndarray:
    """Стерео float32 звук набора (генерируется один раз и кешируется)."""
    key = (kit, slot)
    x = _CACHE.get(key)
    if x is None:
        items = KITS.get(kit, KITS["808"])[1]()
        name, fn = items[slot % len(items)]
        mono = np.asarray(fn(), dtype=np.float32)
        x = np.stack([mono, mono], 1)
        _CACHE[key] = x
    return x


def all_sources() -> list[tuple[str, str]]:
    """[(код, подпись)] для выбора звука пэда: kit:<набор>:<номер>."""
    res = []
    for k, (title, fn) in KITS.items():
        for i, (name, _f) in enumerate(fn()):
            res.append((f"kit:{k}:{i}", f"{title} · {name}"))
    return res
