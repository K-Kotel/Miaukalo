"""Реальное время: несколько устройств с разными часами и размерами блоков + живой микрофон.

Имитирует то, что бывает в Windows: VB-Cable забирает звук по 10 мс, Bluetooth-/USB-наушники —
кусками по 40–100 мс, часы у всех чуть расходятся. Звук не должен рваться ни на одном выходе,
а микрофон — «заикаться» (раньше выходы дрались за общий такт и подрезали друг другу буферы).
"""
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app.audio.devices as dm  # noqa: E402
import app.audio.engine as em  # noqa: E402
from app.audio.sources import LiveSource  # noqa: E402
from app.model import Channel, LiveSource as LS, Output, Pad, Project  # noqa: E402

SR = 48000
FREQ = 440.0
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0


class SimDev(dm.OutputDevice):
    """Устройство вывода с собственным потоком-«колбэком»: свой размер блока и свои часы."""
    PROFILES = {}

    def start(self):
        chunk, drift = self.PROFILES.get(self.name, (480, 0.0))
        self.stream = True
        self.error = ""
        self.captured = []
        self._run = True
        self.on_open()

        def loop():
            rate = SR * (1 + drift)
            t0 = time.perf_counter()
            done = 0
            buf = np.zeros((chunk, 2), np.float32)
            while self._run:
                due = (time.perf_counter() - t0) * rate
                if due - done < chunk:
                    time.sleep(0.001)
                    continue
                self._cb(buf, chunk, None, None)
                self.captured.append(buf[:, 0].copy())
                done += chunk
        self._t = threading.Thread(target=loop, daemon=True)
        self._t.start()
        return True

    def on_open(self):
        pass

    def stop(self):
        self._run = False


class SimMic(LiveSource):
    def __init__(self, drift):
        super().__init__(SR)
        self.drift = drift

    def start(self):
        self._run = True

        def loop():
            rate = SR * (1 + self.drift)
            t0 = time.perf_counter()
            done = 0
            while self._run:
                due = (time.perf_counter() - t0) * rate
                if due - done < 480:
                    time.sleep(0.001)
                    continue
                n = np.arange(done, done + 480)
                x = (0.5 * np.sin(2 * np.pi * FREQ * n / SR)).astype(np.float32)
                self.ring.write(np.stack([x, x], 1))
                done += 480
        threading.Thread(target=loop, daemon=True).start()
        return True

    def stop(self):
        self._run = False


def glitches(sig, skip):
    x = np.concatenate(sig)[skip:]
    w = 2 * np.pi * FREQ / SR
    res = np.abs(x[2:] - 2 * np.cos(w) * x[1:-1] + x[:-2])
    bad = res > 0.02
    # одно событие = подряд идущие плохие сэмплы
    return int(np.count_nonzero(bad[1:] & ~bad[:-1]) + (1 if len(bad) and bad[0] else 0))


def run(profiles, mic_drift, seconds=SECONDS):
    SimDev.PROFILES = profiles
    em.OutputDevice = SimDev
    em.make_source = lambda src, sr: SimMic(mic_drift)
    p = Project()
    p.outputs = [Output(n, n, device=n) for n in profiles]
    p.channels = [Channel("mic", "Микрофон", volume=1.0, outputs=list(profiles))]
    p.pads = [Pad("m", "Микрофон", kind="live", channel="mic", mode="toggle", source=LS(type="input"))]
    p.repair()
    eng = em.Engine()
    eng.start(p)
    eng.pad_event("m", "press")
    time.sleep(seconds)
    eng.shutdown()
    return eng


def measure(profiles, mic_drift, label):
    devs = {}
    orig = SimDev.on_open

    def grab(self):
        devs[self.name] = self
    SimDev.on_open = grab
    run(profiles, mic_drift)
    SimDev.on_open = orig
    out = {n: glitches(d.captured, SR * 2) for n, d in devs.items()}
    print(f"{label:<42} разрывов за {SECONDS - 2:.0f} с: {out}")
    return out


if __name__ == "__main__":
    cases = [
        ("кабель 10 мс + наушники 10 мс", {"CABLE": (480, 0.0003), "PHONES": (480, -0.0002)}, 0.0001),
        ("кабель 10 мс + USB-наушники 21 мс", {"CABLE": (480, 0.0004), "PHONES": (1024, -0.0003)}, -0.0002),
        ("кабель 10 мс + Bluetooth 85 мс", {"CABLE": (480, 0.0005), "PHONES": (4096, -0.0004)}, 0.0002),
        ("только кабель", {"CABLE": (480, 0.0005)}, -0.0003),
        ("3 выхода, сильный разнобой часов (0,2 %)", {"CABLE": (480, 0.002), "CABLE-B": (441, -0.0015),
                                                      "PHONES": (4800, 0.001)}, -0.002),
    ]
    worst = 0
    for label, prof, md in cases:
        r = measure(prof, md, label)
        worst = max(worst, max(r.values()))
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    assert worst <= limit, f"звук рвётся: {worst} разрывов"
    print("clock ok")
