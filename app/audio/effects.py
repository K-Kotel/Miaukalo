"""Каталог эффектов: базовые (pedalboard от Spotify), свои DSP-блоки и готовые «войсчейнджеры».

Цепочка обрабатывает эффекты по одному, каждый со своим состоянием между блоками,
поэтому можно смешивать плагины pedalboard и обработку на numpy.
"""
from __future__ import annotations

import numpy as np

try:
    import pedalboard as pb
except Exception:  # pragma: no cover
    pb = None

SR = 48000

VOICE = "Голос (войсчейнджеры)"
SPACE = "Пространство"
MOD = "Модуляция"
DIRT = "Искажение и lo-fi"
FILT = "Фильтры и эквалайзер"
DYN = "Динамика"
CATEGORIES = [VOICE, SPACE, MOD, DIRT, FILT, DYN]

# type -> (название, категория, [(param, подпись, min, max, default, шаг, единица)])
CATALOG: dict[str, tuple] = {
    # ---------------------------------------------------------------- войсчейнджеры
    "vc_chipmunk": ("Бурундук", VOICE, [
        ("semitones", "Выше на", 3.0, 12.0, 8.0, 0.5, "пт"),
    ]),
    "vc_monster": ("Монстр", VOICE, [
        ("semitones", "Ниже на", -12.0, -3.0, -7.0, 0.5, "пт"),
        ("growl", "Рык", 0.0, 24.0, 8.0, 0.5, "дБ"),
    ]),
    "vc_giant": ("Великан", VOICE, [
        ("semitones", "Ниже на", -8.0, -2.0, -4.0, 0.5, "пт"),
        ("room", "Зал", 0.0, 1.0, 0.75, 0.01, ""),
    ]),
    "vc_robot": ("Робот", VOICE, [
        ("freq", "Частота", 20.0, 250.0, 70.0, 1.0, "Гц"),
        ("mix", "Сила", 0.0, 1.0, 0.85, 0.01, ""),
        ("bits", "Цифровость", 4.0, 16.0, 10.0, 0.5, "бит"),
    ]),
    "vc_alien": ("Пришелец", VOICE, [
        ("semitones", "Высота", -6.0, 8.0, 3.0, 0.5, "пт"),
        ("freq", "Модуляция", 100.0, 1200.0, 420.0, 10.0, "Гц"),
    ]),
    "vc_phone": ("Телефон", VOICE, [
        ("codec", "Цифровая линия", 0.0, 1.0, 1.0, 1.0, ""),
        ("drive", "Хрип", 0.0, 20.0, 4.0, 0.5, "дБ"),
    ]),
    "vc_megaphone": ("Мегафон", VOICE, [
        ("drive", "Перегруз", 0.0, 36.0, 18.0, 0.5, "дБ"),
    ]),
    "vc_oldradio": ("Старое радио", VOICE, [
        ("rate", "Качество", 4000.0, 16000.0, 8000.0, 100.0, "Гц"),
        ("drive", "Хрип", 0.0, 20.0, 6.0, 0.5, "дБ"),
    ]),
    "vc_underwater": ("Под водой", VOICE, [
        ("cutoff", "Глубина (срез)", 200.0, 1500.0, 500.0, 10.0, "Гц"),
    ]),
    "vc_ghost": ("Призрак", VOICE, [
        ("wet", "Призрачность", 0.1, 1.0, 0.6, 0.01, ""),
    ]),
    "vc_cave": ("Пещера", VOICE, [
        ("size", "Размер", 0.3, 1.0, 0.9, 0.01, ""),
    ]),
    "vc_duet": ("Дуэт / октава", VOICE, [
        ("semitones", "Второй голос", -12.0, 12.0, -12.0, 0.5, "пт"),
        ("mix", "Громкость второго", 0.0, 1.0, 0.5, 0.01, ""),
    ]),
    "pitch": ("Высота голоса", VOICE, [
        ("semitones", "Полутоны", -12.0, 12.0, 5.0, 0.5, "пт"),
    ]),
    "vc_choir": ("Многоголосие (толпа)", VOICE, [
        ("voices", "Голосов", 2.0, 8.0, 4.0, 1.0, ""),
        ("detune", "Разброс высоты", 0.0, 80.0, 22.0, 1.0, "цент"),
        ("spread", "Ширина стерео", 0.0, 1.0, 0.8, 0.01, ""),
        ("mix", "Громкость хора", 0.0, 1.0, 0.7, 0.01, ""),
    ]),
    "vc_harmony": ("Гармонайзер (аккорд)", VOICE, [
        ("chord", "Аккорд", 0.0, 7.0, 3.0, 1.0, ["терция ↑", "малая терция ↑", "кварта ↑", "квинта ↑",
                                                  "мажор", "минор", "октавы ↑↓", "квинта ↓ + октава ↓"]),
        ("mix", "Громкость голосов", 0.0, 1.0, 0.6, 0.01, ""),
    ]),
    "vc_vocoder": ("Вокодер (робо-хор)", VOICE, [
        ("note", "Нота несущей", 50.0, 400.0, 110.0, 1.0, "Гц"),
        ("chord", "Несущая", 0.0, 3.0, 1.0, 1.0, ["одна нота", "квинта", "мажорный аккорд", "минорный аккорд"]),
        ("noise", "Шипящие", 0.0, 1.0, 0.25, 0.01, ""),
        ("mix", "Смесь", 0.0, 1.0, 1.0, 0.01, ""),
    ]),
    "vc_whisper": ("Шёпот", VOICE, [
        ("mix", "Сила", 0.0, 1.0, 1.0, 0.01, ""),
        ("air", "Воздух", 0.0, 12.0, 4.0, 0.5, "дБ"),
    ]),
    "vc_stutter": ("Заикание / глитч", VOICE, [
        ("slice_ms", "Кусочек", 30.0, 300.0, 90.0, 5.0, "мс"),
        ("repeats", "Повторов", 1.0, 8.0, 3.0, 1.0, ""),
        ("gap", "Нормального звука между", 0.0, 8.0, 2.0, 1.0, "кус."),
    ]),
    # ---------------------------------------------------------------- пространство
    "delay": ("Эхо", SPACE, [
        ("delay_seconds", "Задержка", 0.02, 2.0, 0.32, 0.01, "с"),
        ("feedback", "Повторы", 0.0, 0.95, 0.45, 0.01, ""),
        ("mix", "Смесь", 0.0, 1.0, 0.35, 0.01, ""),
    ]),
    "pingpong": ("Пинг-понг эхо", SPACE, [
        ("delay_seconds", "Задержка", 0.05, 1.5, 0.28, 0.01, "с"),
        ("feedback", "Повторы", 0.0, 0.9, 0.5, 0.01, ""),
        ("mix", "Смесь", 0.0, 1.0, 0.4, 0.01, ""),
    ]),
    "widener": ("Расширение стерео", SPACE, [
        ("width", "Ширина", 0.0, 2.0, 1.5, 0.01, ""),
        ("haas_ms", "Сдвиг каналов", 0.0, 30.0, 12.0, 0.5, "мс"),
    ]),
    "reverb": ("Реверберация", SPACE, [
        ("room_size", "Размер комнаты", 0.0, 1.0, 0.6, 0.01, ""),
        ("damping", "Глушение", 0.0, 1.0, 0.5, 0.01, ""),
        ("wet_level", "Обработанный", 0.0, 1.0, 0.33, 0.01, ""),
        ("dry_level", "Чистый", 0.0, 1.0, 0.6, 0.01, ""),
        ("width", "Ширина", 0.0, 1.0, 1.0, 0.01, ""),
    ]),
    # ---------------------------------------------------------------- модуляция
    "chorus": ("Хорус", MOD, [
        ("rate_hz", "Скорость", 0.1, 10.0, 1.0, 0.1, "Гц"),
        ("depth", "Глубина", 0.0, 1.0, 0.4, 0.01, ""),
        ("centre_delay_ms", "Задержка", 1.0, 30.0, 7.0, 0.5, "мс"),
        ("feedback", "Обратная связь", 0.0, 0.9, 0.0, 0.01, ""),
        ("mix", "Смесь", 0.0, 1.0, 0.5, 0.01, ""),
    ]),
    "flanger": ("Флэнджер", MOD, [
        ("rate_hz", "Скорость", 0.05, 5.0, 0.4, 0.05, "Гц"),
        ("depth", "Глубина", 0.0, 1.0, 0.7, 0.01, ""),
        ("feedback", "Резкость", 0.0, 0.95, 0.7, 0.01, ""),
        ("mix", "Смесь", 0.0, 1.0, 0.5, 0.01, ""),
    ]),
    "phaser": ("Фэйзер", MOD, [
        ("rate_hz", "Скорость", 0.1, 10.0, 1.0, 0.1, "Гц"),
        ("depth", "Глубина", 0.0, 1.0, 0.6, 0.01, ""),
        ("centre_frequency_hz", "Частота", 200.0, 5000.0, 1300.0, 10.0, "Гц"),
        ("feedback", "Обратная связь", 0.0, 0.9, 0.3, 0.01, ""),
        ("mix", "Смесь", 0.0, 1.0, 0.5, 0.01, ""),
    ]),
    "vibrato": ("Вибрато", MOD, [
        ("rate", "Скорость", 0.5, 12.0, 5.5, 0.1, "Гц"),
        ("depth_ms", "Глубина", 0.2, 8.0, 2.5, 0.1, "мс"),
    ]),
    "tremolo": ("Тремоло", MOD, [
        ("rate", "Скорость", 0.5, 20.0, 6.0, 0.1, "Гц"),
        ("depth", "Глубина", 0.0, 1.0, 0.7, 0.01, ""),
    ]),
    "ringmod": ("Кольцевой модулятор", MOD, [
        ("freq", "Частота", 10.0, 2000.0, 120.0, 1.0, "Гц"),
        ("mix", "Смесь", 0.0, 1.0, 0.7, 0.01, ""),
    ]),
    "fm": ("Частотная модуляция (FM)", MOD, [
        ("freq", "Частота модуляции", 1.0, 1000.0, 180.0, 1.0, "Гц"),
        ("depth_ms", "Глубина", 0.01, 2.0, 0.35, 0.01, "мс"),
        ("mix", "Смесь", 0.0, 1.0, 1.0, 0.01, ""),
    ]),
    "autopan": ("Автопанорама", MOD, [
        ("rate", "Скорость", 0.05, 10.0, 0.6, 0.05, "Гц"),
        ("depth", "Глубина", 0.0, 1.0, 0.8, 0.01, ""),
    ]),
    # ---------------------------------------------------------------- грязь
    "distortion": ("Перегруз", DIRT, [
        ("drive_db", "Сила", 0.0, 60.0, 25.0, 0.5, "дБ"),
    ]),
    "bitcrush": ("Робот / 8-бит", DIRT, [
        ("bit_depth", "Битность", 2.0, 16.0, 6.0, 0.5, "бит"),
    ]),
    "lofi": ("Lo-fi", DIRT, [
        ("rate", "Частота дискретизации", 2000.0, 22000.0, 8000.0, 100.0, "Гц"),
        ("bits", "Битность", 4.0, 16.0, 10.0, 0.5, "бит"),
    ]),
    "radio": ("Рация", DIRT, [
        ("low_hz", "Низ", 100.0, 1500.0, 500.0, 10.0, "Гц"),
        ("high_hz", "Верх", 1500.0, 6000.0, 3000.0, 10.0, "Гц"),
        ("drive_db", "Хрип", 0.0, 40.0, 12.0, 0.5, "дБ"),
    ]),
    "clipping": ("Жёсткий клиппинг", DIRT, [
        ("threshold_db", "Порог", -30.0, 0.0, -10.0, 0.5, "дБ"),
    ]),
    "saturator": ("Ламповая сатурация", DIRT, [
        ("drive_db", "Нагрев", 0.0, 30.0, 10.0, 0.5, "дБ"),
        ("tone", "Яркость", 1500.0, 20000.0, 9000.0, 100.0, "Гц"),
        ("mix", "Смесь", 0.0, 1.0, 1.0, 0.01, ""),
    ]),
    # ---------------------------------------------------------------- фильтры
    "eq3": ("Эквалайзер (3 полосы)", FILT, [
        ("low_db", "Низы", -18.0, 18.0, 0.0, 0.5, "дБ"),
        ("mid_db", "Середина", -18.0, 18.0, 0.0, 0.5, "дБ"),
        ("mid_hz", "Частота середины", 300.0, 5000.0, 1200.0, 10.0, "Гц"),
        ("high_db", "Верхи", -18.0, 18.0, 0.0, 0.5, "дБ"),
    ]),
    "lowpass": ("Срез верхов (из-за стены)", FILT, [
        ("cutoff_frequency_hz", "Частота", 100.0, 20000.0, 900.0, 10.0, "Гц"),
    ]),
    "highpass": ("Срез низов", FILT, [
        ("cutoff_frequency_hz", "Частота", 20.0, 5000.0, 250.0, 10.0, "Гц"),
    ]),
    "wah": ("Резонансный фильтр", FILT, [
        ("cutoff_hz", "Частота", 100.0, 8000.0, 900.0, 10.0, "Гц"),
        ("resonance", "Резонанс", 0.0, 0.95, 0.6, 0.01, ""),
    ]),
    "autowah": ("Автовау (от громкости)", FILT, [
        ("sens_db", "Чувствительность", 0.0, 30.0, 12.0, 0.5, "дБ"),
        ("low_hz", "От", 150.0, 1500.0, 350.0, 10.0, "Гц"),
        ("high_hz", "До", 800.0, 6000.0, 2800.0, 10.0, "Гц"),
        ("resonance", "Резонанс", 0.0, 0.95, 0.7, 0.01, ""),
    ]),
    "vowel": ("Говорящий фильтр (гласные)", FILT, [
        ("vowel", "Гласная", 0.0, 4.0, 0.0, 0.05, "А–Э–И–О–У"),
        ("rate", "Сама меняется", 0.0, 5.0, 0.0, 0.05, "Гц"),
        ("mix", "Смесь", 0.0, 1.0, 1.0, 0.01, ""),
    ]),
    # ---------------------------------------------------------------- динамика
    "compressor": ("Компрессор", DYN, [
        ("threshold_db", "Порог", -60.0, 0.0, -20.0, 0.5, "дБ"),
        ("ratio", "Степень", 1.0, 20.0, 4.0, 0.1, ":1"),
        ("attack_ms", "Атака", 0.1, 100.0, 2.0, 0.1, "мс"),
        ("release_ms", "Восстановление", 10.0, 1000.0, 120.0, 1.0, "мс"),
    ]),
    "noisegate": ("Шумодав (гейт)", DYN, [
        ("threshold_db", "Порог", -90.0, 0.0, -45.0, 0.5, "дБ"),
        ("ratio", "Степень", 1.0, 20.0, 10.0, 0.1, ":1"),
        ("attack_ms", "Атака", 0.1, 100.0, 1.0, 0.1, "мс"),
        ("release_ms", "Восстановление", 10.0, 1000.0, 150.0, 1.0, "мс"),
    ]),
    "gain": ("Усиление", DYN, [
        ("gain_db", "Уровень", -24.0, 24.0, 6.0, 0.5, "дБ"),
    ]),
    "limiter": ("Лимитер", DYN, [
        ("threshold_db", "Порог", -24.0, 0.0, -1.0, 0.5, "дБ"),
        ("release_ms", "Восстановление", 10.0, 1000.0, 100.0, 1.0, "мс"),
    ]),
}

# эффекты, которые один-в-один плагин pedalboard и умеют менять параметры на лету
SIMPLE = {"delay": "Delay", "reverb": "Reverb", "chorus": "Chorus", "phaser": "Phaser",
          "distortion": "Distortion", "bitcrush": "Bitcrush", "lowpass": "LowpassFilter",
          "highpass": "HighpassFilter", "compressor": "Compressor", "noisegate": "NoiseGate", "gain": "Gain",
          "limiter": "Limiter", "clipping": "Clipping"}


def effect_title(t: str) -> str:
    return CATALOG.get(t, (t,))[0]


def default_params(t: str) -> dict:
    return {p[0]: p[4] for p in CATALOG.get(t, ("", "", []))[2]}


def _clamped(t: str, params: dict) -> dict:
    out = {}
    for name, _l, lo, hi, default, _s, _u in CATALOG[t][2]:
        v = params.get(name, default)
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = default
        out[name] = min(hi, max(lo, v))
    return out


# ===================================================================== процессоры

class Proc:
    def process(self, x: np.ndarray) -> np.ndarray:
        return x

    def reset(self):
        pass

    def update(self, params) -> bool:
        return False


class PbProc(Proc):
    """Плагин pedalboard + выравнивание по длине (у PitchShift/GSM/Resample выход приходит порциями)."""

    def __init__(self, plugin, kind=None):
        self.p = plugin
        self.kind = kind
        self.pending = np.zeros((0, 2), np.float32)

    def process(self, x):
        n = len(x)
        out = self.p.process(np.ascontiguousarray(x.T), SR, reset=False).T
        if out.shape[0] == n and not len(self.pending):
            return np.ascontiguousarray(out, dtype=np.float32)
        out = out.astype(np.float32)
        self.pending = np.concatenate([self.pending, out]) if len(self.pending) else out
        res = np.zeros((n, 2), np.float32)
        if len(self.pending) >= n:
            res[:] = self.pending[:n]
            self.pending = self.pending[n:]
        else:
            k = len(self.pending)
            if k:
                res[n - k:] = self.pending
            self.pending = np.zeros((0, 2), np.float32)
        return res

    def reset(self):
        self.pending = np.zeros((0, 2), np.float32)
        try:
            self.p.reset()
        except Exception:
            pass

    def update(self, params):
        if self.kind not in SIMPLE:
            return False
        for k, v in params.items():
            try:
                setattr(self.p, k, v)
            except Exception:
                return False
        return True


class Chain(Proc):
    def __init__(self, procs):
        self.procs = [p for p in procs if p is not None]

    def process(self, x):
        for p in self.procs:
            x = p.process(x)
        return x

    def reset(self):
        for p in self.procs:
            p.reset()


class RingMod(Proc):
    def __init__(self, freq, mix):
        self.freq, self.mix, self.phase = freq, mix, 0.0

    def process(self, x):
        n = len(x)
        ph = self.phase + 2 * np.pi * self.freq / SR * np.arange(n)
        self.phase = float((ph[-1] + 2 * np.pi * self.freq / SR) % (2 * np.pi)) if n else self.phase
        car = np.sin(ph).astype(np.float32)[:, None]
        return (x * (1 - self.mix) + x * car * self.mix).astype(np.float32)

    def update(self, p):
        self.freq, self.mix = p["freq"], p["mix"]
        return True


class Tremolo(Proc):
    def __init__(self, rate, depth):
        self.rate, self.depth, self.phase = rate, depth, 0.0

    def process(self, x):
        n = len(x)
        ph = self.phase + 2 * np.pi * self.rate / SR * np.arange(n)
        self.phase = float((ph[-1] + 2 * np.pi * self.rate / SR) % (2 * np.pi)) if n else self.phase
        g = (1 - self.depth * (0.5 + 0.5 * np.sin(ph))).astype(np.float32)[:, None]
        return (x * g).astype(np.float32)

    def update(self, p):
        self.rate, self.depth = p["rate"], p["depth"]
        return True


class Vibrato(Proc):
    """Модулированная задержка — «дрожащий» голос."""
    MAXD = int(0.02 * SR)

    def __init__(self, rate, depth_ms):
        self.rate, self.depth = rate, depth_ms
        self.buf = np.zeros((self.MAXD * 2, 2), np.float32)
        self.phase = 0.0

    def process(self, x):
        n = len(x)
        hist = self.buf
        data = np.concatenate([hist, x.astype(np.float32)])
        ph = self.phase + 2 * np.pi * self.rate / SR * np.arange(n)
        self.phase = float((ph[-1] + 2 * np.pi * self.rate / SR) % (2 * np.pi)) if n else self.phase
        d = (self.depth / 1000 * SR) * (0.5 + 0.5 * np.sin(ph)) + 1.0
        pos = len(hist) + np.arange(n) - d
        i0 = np.floor(pos).astype(np.int64)
        frac = (pos - i0).astype(np.float32)[:, None]
        out = data[i0] * (1 - frac) + data[np.minimum(i0 + 1, len(data) - 1)] * frac
        self.buf = data[-len(hist):]
        return out.astype(np.float32)

    def update(self, p):
        self.rate, self.depth = p["rate"], p["depth_ms"]
        return True


class FastPitch(Proc):
    """Сдвиг высоты с малой задержкой (~20 мс): две «бегущие» задержки с плавной перекрёсткой.
    Стандартный PitchShift (Rubber Band) копит около секунды — для живого голоса не годится."""

    def __init__(self, semitones, window_ms=42.0):
        self.W = int(window_ms / 1000 * SR)
        self.H = self.W + 4
        self.buf = np.zeros((self.H, 2), np.float32)
        self.phase = 0.0
        self.set(semitones)

    def set(self, semitones):
        self.ratio = 2.0 ** (semitones / 12.0)

    def process(self, x):
        n = len(x)
        data = np.concatenate([self.buf, x.astype(np.float32)])
        inc = (1.0 - self.ratio) / self.W
        ph = (self.phase + inc * np.arange(n)) % 1.0
        self.phase = float((self.phase + inc * n) % 1.0)
        base = self.H + np.arange(n)
        out = np.zeros((n, 2), np.float32)
        for p in (ph, (ph + 0.5) % 1.0):
            d = p * self.W + 1.0
            pos = base - d
            i0 = np.floor(pos).astype(np.int64)
            fr = (pos - i0).astype(np.float32)[:, None]
            g = (np.sin(np.pi * p) ** 2).astype(np.float32)[:, None]
            out += g * (data[i0] * (1 - fr) + data[i0 + 1] * fr)
        self.buf = data[-self.H:]
        return out

    def reset(self):
        self.buf[:] = 0
        self.phase = 0.0

    def update(self, p):
        self.set(p["semitones"])
        return True


class Parallel(Proc):
    """Сухой сигнал + обработанная копия (для дуэта / октавы)."""

    def __init__(self, wet_proc, mix):
        self.wet, self.mix = wet_proc, mix

    def process(self, x):
        return (x * (1 - self.mix * 0.5) + self.wet.process(x) * self.mix).astype(np.float32)

    def reset(self):
        self.wet.reset()


# ----------------------------------------------------------------------- новые процессоры

class Voices(Proc):
    """Несколько копий голоса со своей высотой, задержкой и панорамой (многоголосие, гармонайзер)."""

    def __init__(self, specs, mix, dry=1.0):
        # specs: [(полутоны, задержка мс, панорама -1..1)]
        self.mix, self.dry = mix, dry
        self.set_specs(specs)

    def set_specs(self, specs):
        self.voices = []
        for k, (semi, delay_ms, pan) in enumerate(specs):
            fp = FastPitch(semi, window_ms=36.0 + 7.0 * (k % 4))
            fp.phase = (k * 0.37) % 1.0                    # разные фазы — голоса не «склеиваются»
            d = int(delay_ms / 1000 * SR)
            gl, gr = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
            self.voices.append([fp, d, np.zeros((d, 2), np.float32), np.float32(gl * 1.41), np.float32(gr * 1.41)])
        self.norm = np.float32(1.0 / max(1.0, np.sqrt(len(self.voices))))

    def process(self, x):
        n = len(x)
        mono = x.mean(axis=1, keepdims=True).repeat(2, axis=1).astype(np.float32)
        acc = np.zeros((n, 2), np.float32)
        for v in self.voices:
            fp, d, buf, gl, gr = v
            y = fp.process(mono)
            if d:
                data = np.concatenate([buf, y])
                v[2] = data[-d:]
                y = data[:n]
            acc[:, 0] += y[:, 0] * gl
            acc[:, 1] += y[:, 1] * gr
        return (x * self.dry + acc * (self.norm * self.mix)).astype(np.float32)

    def reset(self):
        for v in self.voices:
            v[0].reset()
            v[2][:] = 0


def choir_specs(n, detune, spread):
    n = int(round(n))
    specs = []
    for k in range(n):
        pos = (k / (n - 1) * 2 - 1) if n > 1 else 0.0          # -1..1
        # высота по порядку, задержки вразнобой — чтобы соседние по высоте голоса не совпадали по времени
        specs.append((pos * detune / 100.0, 8 + 21 * ((k * 5) % n) / max(1, n - 1), pos * spread))
    return specs


CHORDS = [[4], [3], [5], [7], [4, 7], [3, 7], [-12, 12], [-7, -12]]


def harmony_specs(chord):
    iv = CHORDS[int(round(chord)) % len(CHORDS)]
    pans = [-0.5, 0.5, 0.0]
    return [(semi, 4 + 6 * i, pans[i % 3]) for i, semi in enumerate(iv)]


class FM(Proc):
    """Частотная модуляция голоса: задержка «качается» со звуковой частотой — металлические призвуки."""
    MAXD = int(0.006 * SR)

    def __init__(self, freq, depth_ms, mix):
        self.freq, self.depth, self.mix = freq, depth_ms, mix
        self.buf = np.zeros((self.MAXD, 2), np.float32)
        self.phase = 0.0

    def process(self, x):
        n = len(x)
        data = np.concatenate([self.buf, x.astype(np.float32)])
        w = 2 * np.pi * self.freq / SR
        ph = self.phase + w * np.arange(n)
        self.phase = float((self.phase + w * n) % (2 * np.pi))
        dmax = min(self.depth / 1000 * SR, self.MAXD / 2 - 2)
        d = dmax * (1 + np.sin(ph)) + 1.0
        pos = len(self.buf) + np.arange(n) - d
        i0 = np.floor(pos).astype(np.int64)
        fr = (pos - i0).astype(np.float32)[:, None]
        wet = data[i0] * (1 - fr) + data[i0 + 1] * fr
        self.buf = data[-self.MAXD:]
        return (x * (1 - self.mix) + wet * self.mix).astype(np.float32)

    def update(self, p):
        self.freq, self.depth, self.mix = p["freq"], p["depth_ms"], p["mix"]
        return True


class AutoPan(Proc):
    def __init__(self, rate, depth):
        self.rate, self.depth, self.phase = rate, depth, 0.0

    def process(self, x):
        n = len(x)
        w = 2 * np.pi * self.rate / SR
        ph = self.phase + w * np.arange(n)
        self.phase = float((self.phase + w * n) % (2 * np.pi))
        pan = self.depth * np.sin(ph)                       # -1..1
        a = (pan + 1) * np.pi / 4
        g = np.stack([np.cos(a), np.sin(a)], 1).astype(np.float32) * np.float32(1.41)
        mono = x.mean(axis=1, keepdims=True)
        side = (x[:, :1] - x[:, 1:]) * 0.5
        return (mono * g + np.concatenate([side, -side], 1) * (1 - self.depth)).astype(np.float32)

    def update(self, p):
        self.rate, self.depth = p["rate"], p["depth"]
        return True


class PingPong(Proc):
    """Эхо, которое прыгает слева направо. Задержка ≥ 50 мс > блока, поэтому без цикла по сэмплам."""

    def __init__(self, delay, fb, mix):
        self.cap = int(1.6 * SR) + 1024
        self.l = np.zeros(self.cap, np.float32)
        self.r = np.zeros(self.cap, np.float32)
        self.w = 0
        self.update({"delay_seconds": delay, "feedback": fb, "mix": mix})

    def update(self, p):
        self.d = max(1024, int(p["delay_seconds"] * SR))
        self.fb, self.mix = p["feedback"], p["mix"]
        return True

    def _read(self, line, n):
        idx = (self.w - self.d + np.arange(n)) % self.cap
        return line[idx]

    def process(self, x):
        n = len(x)
        rl, rr = self._read(self.l, n), self._read(self.r, n)
        mono = x.mean(axis=1)
        idx = (self.w + np.arange(n)) % self.cap
        self.l[idx] = mono + self.fb * rr
        self.r[idx] = rl
        self.w = (self.w + n) % self.cap
        wet = np.stack([rl, rr], 1)
        return (x * (1 - self.mix * 0.5) + wet * self.mix).astype(np.float32)

    def reset(self):
        self.l[:] = 0
        self.r[:] = 0


class Widener(Proc):
    def __init__(self, width, haas_ms):
        self.buf = np.zeros((0,), np.float32)
        self.update({"width": width, "haas_ms": haas_ms})

    def update(self, p):
        self.width = p["width"]
        d = int(p["haas_ms"] / 1000 * SR)
        if d != len(self.buf):
            self.buf = np.zeros(d, np.float32)
        return True

    def process(self, x):
        x = x.astype(np.float32)
        if len(self.buf):
            data = np.concatenate([self.buf, x[:, 1]])
            self.buf = data[-len(self.buf):]
            x = np.stack([x[:, 0], data[:len(x)]], 1)
        m = (x[:, 0] + x[:, 1]) * 0.5
        sd = (x[:, 0] - x[:, 1]) * 0.5 * self.width
        return np.stack([m + sd, m - sd], 1).astype(np.float32)

    def reset(self):
        self.buf[:] = 0


class Saturate(Proc):
    """Мягкое «ламповое» ограничение с лёгкой асимметрией (чётные гармоники)."""

    def __init__(self, drive_db, mix):
        self.update({"drive_db": drive_db, "mix": mix})

    def update(self, p):
        self.g = float(10 ** (p["drive_db"] / 20))
        self.mix = p["mix"]
        return True

    def process(self, x):
        b = 0.15
        y = np.tanh(self.g * x + b) - np.tanh(b)
        y *= 1.0 / np.tanh(self.g + b) * 0.9
        return (x * (1 - self.mix) + y * self.mix).astype(np.float32)


class AutoWah(Proc):
    def __init__(self, sens_db, low, high, res):
        self.f = pb.LadderFilter(mode=pb.LadderFilter.Mode.BPF12, cutoff_hz=low, resonance=res, drive=1.0)
        self.env = 0.0
        self.update({"sens_db": sens_db, "low_hz": low, "high_hz": high, "resonance": res})

    def update(self, p):
        self.sens = float(10 ** (p["sens_db"] / 20))
        self.low, self.high = p["low_hz"], max(p["low_hz"] + 50, p["high_hz"])
        self.f.resonance = p["resonance"]
        return True

    def process(self, x):
        lvl = float(np.sqrt((x ** 2).mean())) * self.sens * 4
        k = 0.5 if lvl > self.env else 0.08                      # быстрая атака, плавный спад
        self.env += (min(1.0, lvl) - self.env) * k
        self.f.cutoff_hz = float(self.low * (self.high / self.low) ** self.env)
        y = self.f.process(np.ascontiguousarray(x.T), SR, reset=False).T
        return (y * 1.6).astype(np.float32)

    def reset(self):
        self.f.reset()


VOWELS = [  # форманты F1, F2, F3 (Гц) и их громкости
    ((800, 1150, 2900), (1.0, 0.5, 0.25)),   # А
    ((400, 1700, 2600), (1.0, 0.4, 0.25)),   # Э
    ((300, 2200, 3000), (1.0, 0.3, 0.2)),    # И
    ((450, 800, 2830), (1.0, 0.6, 0.15)),    # О
    ((325, 700, 2530), (1.0, 0.35, 0.1)),    # У
]


class Vowel(Proc):
    def __init__(self, vowel, rate, mix):
        self.fs = [pb.LadderFilter(mode=pb.LadderFilter.Mode.BPF12, cutoff_hz=f, resonance=0.8, drive=1.0)
                   for f in VOWELS[0][0]]
        self.phase = 0.0
        self.update({"vowel": vowel, "rate": rate, "mix": mix})

    def update(self, p):
        self.vowel, self.rate, self.mix = p["vowel"], p["rate"], p["mix"]
        return True

    def _at(self, v):
        v = max(0.0, min(4.0, v))
        i = min(3, int(v))
        t = v - i
        (fa, ga), (fb, gb) = VOWELS[i], VOWELS[i + 1]
        return [a + (b - a) * t for a, b in zip(fa, fb)], [a + (b - a) * t for a, b in zip(ga, gb)]

    def process(self, x):
        n = len(x)
        v = self.vowel
        if self.rate > 0:
            self.phase = (self.phase + self.rate * n / SR) % 1.0
            v = 2.0 - 2.0 * np.cos(2 * np.pi * self.phase)      # туда-обратно А→У→А
        freqs, gains = self._at(v)
        xt = np.ascontiguousarray(x.T)
        acc = np.zeros_like(xt)
        for f, fr, g in zip(self.fs, freqs, gains):
            f.cutoff_hz = float(fr)
            acc += f.process(xt, SR, reset=False) * g
        y = acc.T * 1.5
        return (x * (1 - self.mix) + y * self.mix).astype(np.float32)

    def reset(self):
        for f in self.fs:
            f.reset()


class Spectral(Proc):
    """Обработка в частотной области (окно 1024, шаг 256, задержка ≈ 27 мс): вокодер и шёпот."""
    N, HOP = 1024, 256

    def __init__(self, mode, p):
        self.mode = mode
        self.win = np.sqrt(np.hanning(self.N + 1)[:-1]).astype(np.float32)
        self.inbuf = np.zeros((self.N, 2), np.float32)
        self.fill = 0                      # сколько новых сэмплов накоплено с прошлого кадра
        self.ola = np.zeros((self.N, 2), np.float32)
        self.out_fifo = np.zeros((self.HOP, 2), np.float32)  # запас на шаг кадра — без провалов
        self.rng = np.random.default_rng(7)
        self.car_phase = np.zeros(3)
        self.car_buf = np.zeros(self.N, np.float32)
        nb = self.N // 2 + 1
        freqs = np.fft.rfftfreq(self.N, 1 / SR)
        edges = np.geomspace(90, 9000, 26)
        B = np.zeros((len(edges) - 2, nb), np.float32)
        for k in range(1, len(edges) - 1):
            lo, c, hi = edges[k - 1], edges[k], edges[k + 1]
            B[k - 1] = np.clip(np.minimum((freqs - lo) / (c - lo), (hi - freqs) / (hi - c)), 0, None)
        self.B = B
        self.Bnorm = np.maximum(B.sum(axis=0), 1e-6)
        self.update(p)

    def update(self, p):
        self.p = dict(p)
        return True

    def _carrier(self, n):
        p = self.p
        iv = [[0], [0, 7], [0, 4, 7], [0, 3, 7]][int(round(p.get("chord", 0))) % 4]
        out = np.zeros(n, np.float32)
        t = np.arange(n)
        for k, semi in enumerate(iv):
            f = p.get("note", 110.0) * 2 ** (semi / 12)
            ph = self.car_phase[k] + f / SR * t
            out += (2 * (ph % 1.0) - 1).astype(np.float32)        # пила — богатая гармониками
            self.car_phase[k] = (self.car_phase[k] + f / SR * n) % 1.0
        out /= len(iv)
        nz = p.get("noise", 0.0)
        if nz > 0:
            out += self.rng.normal(0, 0.5 * nz, n).astype(np.float32)
        return out

    def _frame(self, frame, car):
        X = np.fft.rfft(frame * self.win[:, None], axis=0)
        mag = np.abs(X)
        if self.mode == "whisper":
            ph = np.exp(1j * self.rng.uniform(0, 2 * np.pi, X.shape))
            air = 10 ** (self.p.get("air", 4.0) / 20)
            tilt = np.linspace(1.0, air, X.shape[0])[:, None]
            Y = mag * ph * tilt
        else:
            C = np.fft.rfft(car * self.win)
            env_m = np.sqrt((self.B.T @ (self.B @ (mag ** 2).mean(axis=1))) / self.Bnorm)
            env_c = np.sqrt((self.B.T @ (self.B @ (np.abs(C) ** 2))) / self.Bnorm) + 1e-4
            Y = (C * (env_m / env_c))[:, None].repeat(2, axis=1)
        return np.fft.irfft(Y, n=self.N, axis=0).astype(np.float32) * self.win[:, None]

    def process(self, x):
        n = len(x)
        x = x.astype(np.float32)
        car = self._carrier(n) if self.mode == "vocoder" else None
        produced = []
        pos = 0
        while pos < n:
            k = min(self.HOP - self.fill, n - pos)
            self.inbuf = np.concatenate([self.inbuf[k:], x[pos:pos + k]])
            if car is not None:
                self.car_buf = np.concatenate([self.car_buf[k:], car[pos:pos + k]])
            self.fill += k
            pos += k
            if self.fill == self.HOP:
                self.fill = 0
                y = self._frame(self.inbuf, self.car_buf)
                self.ola += y / 2.0                       # sqrt-Hann² с перекрытием 75 % даёт 2
                produced.append(self.ola[:self.HOP].copy())
                self.ola = np.concatenate([self.ola[self.HOP:], np.zeros((self.HOP, 2), np.float32)])
        if produced:
            self.out_fifo = np.concatenate([self.out_fifo] + produced)
        out = self.out_fifo[:n]
        self.out_fifo = self.out_fifo[n:]
        if len(out) < n:
            out = np.concatenate([out, np.zeros((n - len(out), 2), np.float32)])
        mix = self.p.get("mix", 1.0)
        return (x * (1 - mix) + out * mix).astype(np.float32)

    def reset(self):
        self.inbuf[:] = 0
        self.ola[:] = 0
        self.fill = 0
        self.out_fifo = np.zeros((self.HOP, 2), np.float32)


class Stutter(Proc):
    """Кусочек звука повторяется несколько раз, потом снова живой звук — «з-з-заикание»."""

    def __init__(self, slice_ms, repeats, gap):
        self.buf = np.zeros((int(0.31 * SR), 2), np.float32)
        self.t = 0
        self.update({"slice_ms": slice_ms, "repeats": repeats, "gap": gap})

    def update(self, p):
        self.L = max(480, int(p["slice_ms"] / 1000 * SR))
        self.R = int(round(p["repeats"]))
        self.G = int(round(p["gap"]))
        return True

    def process(self, x):
        n = len(x)
        out = np.empty_like(x, dtype=np.float32)
        L = self.L
        cycle = L * (1 + self.R + self.G)
        i = 0
        while i < n:
            ph = self.t % cycle
            seg, off = divmod(ph, L)
            k = min(n - i, L - off)
            if seg == 0:                                       # живой звук + запоминаем кусочек
                out[i:i + k] = x[i:i + k]
                self.buf[off:off + k] = x[i:i + k]
            elif seg <= self.R:                                # повторы кусочка
                fade = np.ones((k, 1), np.float32)
                if off < 96:
                    m = min(k, 96 - off)
                    fade[:m, 0] = np.linspace(off / 96, (off + m) / 96, m, dtype=np.float32)
                out[i:i + k] = self.buf[off:off + k] * fade
            else:                                              # снова живой звук
                out[i:i + k] = x[i:i + k]
            self.t += k
            i += k
        return out

    def reset(self):
        self.t = 0


def _pb(name, **kw):
    return PbProc(getattr(pb, name)(**kw), None)


def build(t: str, params: dict):
    """Создаёт процессор для эффекта (или None, если pedalboard недоступен)."""
    if pb is None or t not in CATALOG:
        return None
    p = _clamped(t, params)
    if t in SIMPLE:
        return PbProc(getattr(pb, SIMPLE[t])(**p), t)
    if t == "pitch":
        return FastPitch(p["semitones"])
    if t == "radio":
        return Chain([_pb("HighpassFilter", cutoff_frequency_hz=p["low_hz"]),
                      _pb("LowpassFilter", cutoff_frequency_hz=p["high_hz"]),
                      _pb("Distortion", drive_db=p["drive_db"]), _pb("Gain", gain_db=-p["drive_db"] * 0.5)])
    if t == "flanger":
        return _pb("Chorus", rate_hz=p["rate_hz"], depth=p["depth"], centre_delay_ms=1.5,
                   feedback=p["feedback"], mix=p["mix"])
    if t == "vibrato":
        return Vibrato(p["rate"], p["depth_ms"])
    if t == "tremolo":
        return Tremolo(p["rate"], p["depth"])
    if t == "ringmod":
        return RingMod(p["freq"], p["mix"])
    if t == "lofi":
        return Chain([_pb("Resample", target_sample_rate=p["rate"]), _pb("Bitcrush", bit_depth=p["bits"])])
    if t == "eq3":
        return Chain([_pb("LowShelfFilter", cutoff_frequency_hz=200, gain_db=p["low_db"]),
                      _pb("PeakFilter", cutoff_frequency_hz=p["mid_hz"], gain_db=p["mid_db"], q=0.9),
                      _pb("HighShelfFilter", cutoff_frequency_hz=5000, gain_db=p["high_db"])])
    if t == "wah":
        return _pb("LadderFilter", mode=pb.LadderFilter.Mode.BPF12, cutoff_hz=p["cutoff_hz"],
                   resonance=p["resonance"], drive=1.0)
    # --- войсчейнджеры
    if t == "vc_chipmunk":
        return Chain([FastPitch(p["semitones"]),
                      _pb("HighShelfFilter", cutoff_frequency_hz=4000, gain_db=3)])
    if t == "vc_monster":
        return Chain([FastPitch(p["semitones"]),
                      _pb("LowShelfFilter", cutoff_frequency_hz=200, gain_db=4),
                      _pb("Distortion", drive_db=p["growl"]), _pb("Gain", gain_db=-p["growl"] * 0.45),
                      _pb("Reverb", room_size=0.35, wet_level=0.15, dry_level=0.85)])
    if t == "vc_giant":
        return Chain([FastPitch(p["semitones"]),
                      _pb("LowShelfFilter", cutoff_frequency_hz=180, gain_db=3),
                      _pb("Reverb", room_size=p["room"], wet_level=0.28, dry_level=0.8)])
    if t == "vc_robot":
        return Chain([RingMod(p["freq"], p["mix"]), _pb("Bitcrush", bit_depth=p["bits"]),
                      _pb("Chorus", rate_hz=0.2, depth=0.15, centre_delay_ms=4, mix=0.25)])
    if t == "vc_alien":
        return Chain([FastPitch(p["semitones"]), RingMod(p["freq"], 0.45),
                      _pb("Chorus", rate_hz=3.0, depth=0.5, centre_delay_ms=3, feedback=0.55, mix=0.5)])
    if t == "vc_phone":
        procs = [_pb("HighpassFilter", cutoff_frequency_hz=320), _pb("LowpassFilter", cutoff_frequency_hz=3400)]
        if p["codec"] >= 0.5:     # линия 8 кГц с грубым квантованием (кодек GSM ломается на блоках по 10 мс)
            procs += [_pb("Resample", target_sample_rate=8000), _pb("Bitcrush", bit_depth=8)]
        procs += [_pb("Distortion", drive_db=p["drive"]), _pb("Gain", gain_db=3 - p["drive"] * 0.4)]
        return Chain(procs)
    if t == "vc_megaphone":
        return Chain([_pb("HighpassFilter", cutoff_frequency_hz=600), _pb("LowpassFilter", cutoff_frequency_hz=3500),
                      _pb("Distortion", drive_db=p["drive"]),
                      _pb("Compressor", threshold_db=-18, ratio=6), _pb("Gain", gain_db=-p["drive"] * 0.35)])
    if t == "vc_oldradio":
        return Chain([_pb("HighpassFilter", cutoff_frequency_hz=450), _pb("LowpassFilter", cutoff_frequency_hz=2800),
                      _pb("Resample", target_sample_rate=p["rate"]), _pb("Bitcrush", bit_depth=8),
                      _pb("Distortion", drive_db=p["drive"]), _pb("Gain", gain_db=-p["drive"] * 0.4)])
    if t == "vc_underwater":
        return Chain([_pb("LowpassFilter", cutoff_frequency_hz=p["cutoff"]),
                      _pb("Chorus", rate_hz=0.35, depth=0.9, centre_delay_ms=9, feedback=0.3, mix=0.6),
                      _pb("Reverb", room_size=0.6, wet_level=0.25, dry_level=0.8)])
    if t == "vc_ghost":
        return Chain([_pb("HighpassFilter", cutoff_frequency_hz=220),
                      _pb("Chorus", rate_hz=0.6, depth=0.8, centre_delay_ms=12, feedback=0.4, mix=0.5),
                      _pb("Reverb", room_size=0.95, damping=0.3, wet_level=p["wet"], dry_level=1 - p["wet"] * 0.6)])
    if t == "vc_cave":
        return Chain([_pb("Delay", delay_seconds=0.18 + 0.2 * p["size"], feedback=0.35, mix=0.3),
                      _pb("Reverb", room_size=p["size"], wet_level=0.4, dry_level=0.7)])
    if t == "vc_duet":
        return Parallel(FastPitch(p["semitones"]), p["mix"])
    if t == "vc_choir":
        return Voices(choir_specs(p["voices"], p["detune"], p["spread"]), p["mix"], dry=0.8)
    if t == "vc_harmony":
        return Voices(harmony_specs(p["chord"]), p["mix"], dry=1.0)
    if t == "vc_vocoder":
        return Spectral("vocoder", p)
    if t == "vc_whisper":
        return Chain([_pb("HighpassFilter", cutoff_frequency_hz=200), Spectral("whisper", p)])
    if t == "vc_stutter":
        return Stutter(p["slice_ms"], p["repeats"], p["gap"])
    if t == "fm":
        return FM(p["freq"], p["depth_ms"], p["mix"])
    if t == "autopan":
        return AutoPan(p["rate"], p["depth"])
    if t == "pingpong":
        return PingPong(p["delay_seconds"], p["feedback"], p["mix"])
    if t == "widener":
        return Widener(p["width"], p["haas_ms"])
    if t == "saturator":
        return Chain([Saturate(p["drive_db"], p["mix"]), _pb("HighpassFilter", cutoff_frequency_hz=25),
                      _pb("LowpassFilter", cutoff_frequency_hz=p["tone"])])
    if t == "autowah":
        return AutoWah(p["sens_db"], p["low_hz"], p["high_hz"], p["resonance"])
    if t == "vowel":
        return Vowel(p["vowel"], p["rate"], p["mix"])
    return None


def make_plugin(t, params):        # совместимость со старым кодом
    return build(t, params)


class EffectChain:
    """Цепочка эффектов канала или выхода. Состояние сохраняется между блоками."""

    def __init__(self):
        self.slots = []  # [slot_id, type, enabled, params, proc]

    def sync(self, slots):
        old = {s[0]: s for s in self.slots}
        new = []
        for es in slots:
            prev = old.get(es.id)
            if prev and prev[1] == es.type:
                proc = prev[4]
                if prev[3] != es.params:
                    if proc is None or not proc.update(_clamped(es.type, es.params)):
                        proc = build(es.type, es.params)
                if es.enabled and not prev[2] and proc is not None:
                    proc.reset()
                new.append([es.id, es.type, es.enabled, dict(es.params), proc])
            else:
                new.append([es.id, es.type, es.enabled, dict(es.params), build(es.type, es.params)])
        self.slots = new

    def set_enabled(self, slot_id, enabled: bool):
        for s in self.slots:
            if s[0] == slot_id and s[2] != enabled:
                s[2] = enabled
                if enabled and s[4] is not None:
                    s[4].reset()

    def active(self) -> bool:
        return any(s[2] and s[4] is not None for s in self.slots)

    def process(self, block: np.ndarray, sr: int = SR) -> np.ndarray:
        for s in self.slots:
            if s[2] and s[4] is not None:
                try:
                    block = s[4].process(block)
                except Exception:
                    s[2] = False       # сломанный эффект выключаем, звук не должен пропасть
        return np.ascontiguousarray(block, dtype=np.float32)


# ===================================================================== случайные эффекты

# что не берём в «случайные»: незаметное (усиление, лимитер…) или то, что легко даёт тишину
RANDOM_SKIP = {"gain", "limiter", "noisegate", "compressor", "clipping", "widener", "autopan", "eq3"}
RANDOM_POOLS = {"all": "Любые", "voice": "Только голосовые", "nonvoice": "Без голосовых (эхо, фильтры, грязь…)"}


class _Slot:
    def __init__(self, sid, t, params):
        self.id, self.type, self.enabled, self.params = sid, t, True, params


def random_params(t: str, strength: float, rng) -> dict:
    """Случайные настройки: strength=0 — как по умолчанию, 1 — во весь диапазон."""
    out = {}
    for name, _l, lo, hi, default, step, unit in CATALOG[t][2]:
        if isinstance(unit, (list, tuple)):
            v = float(rng.integers(0, len(unit)))
        else:
            u = rng.uniform(lo, hi)
            v = default + (u - default) * strength
            v = round(round((v - lo) / step) * step + lo, 4)
        out[name] = min(hi, max(lo, v))
    if t == "lowpass":                         # слишком низкий срез — почти тишина
        out["cutoff_frequency_hz"] = max(out["cutoff_frequency_hz"], 500.0)
    if t == "highpass":
        out["cutoff_frequency_hz"] = min(out["cutoff_frequency_hz"], 1500.0)
    if t == "vc_underwater":
        out["cutoff"] = max(out["cutoff"], 600.0)
    if "mix" in out and t not in ("vc_vocoder",):
        out["mix"] = max(out["mix"], 0.35)    # чтобы эффект было слышно
    return out


def random_chain(count: int, strength: float = 0.7, pool: str = "all", seed=None):
    """(EffectChain, [названия]) — N разных случайных эффектов + страховочный лимитер в конце."""
    rng = np.random.default_rng(seed)
    types = [t for t, (_n, cat, _p) in CATALOG.items() if t not in RANDOM_SKIP
             and (pool == "all" or (pool == "voice") == (cat == VOICE))]
    count = max(1, min(int(count), len(types)))
    picked = list(rng.choice(types, size=count, replace=False))
    # высоту голоса меняем не больше одного раза — иначе каша
    pitchy = {"vc_chipmunk", "vc_monster", "vc_giant", "pitch", "vc_alien"}
    seen = False
    for i, t in enumerate(picked):
        if t in pitchy:
            if seen:
                rest = [x for x in types if x not in pitchy and x not in picked]
                if rest:
                    picked[i] = rest[int(rng.integers(0, len(rest)))]
            seen = True
    slots = [_Slot(f"r{i}", t, random_params(t, strength, rng)) for i, t in enumerate(picked)]
    slots.append(_Slot("safety", "limiter", {"threshold_db": -1.0, "release_ms": 100.0}))
    ch = EffectChain()
    ch.sync(slots)
    return ch, [effect_title(t) for t in picked]
