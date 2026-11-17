"""Плеер: проигрывание по плейлисту, пауза, перемотка, переход к следующему, плавный переход, «Стоп всё»."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app.audio.engine as em  # noqa: E402
from app.audio.engine import Engine  # noqa: E402
from app.audio.ring import Ring  # noqa: E402
from app.model import Channel, Output, Project  # noqa: E402


class FakeDev:
    def __init__(self, oid, n, h, sr):
        self.out_id, self.ring, self.error, self.stream, self.name, self.hostapi, self.volume = oid, Ring(48000), "", None, n, h, 1

    def start(self):
        return True

    def stop(self):
        pass


em.OutputDevice = FakeDev
SR = 48000


def tone(f, sec):
    t = np.arange(int(SR * sec)) / SR
    return (np.sin(2 * np.pi * f * t) * 0.5 * 32767).astype(np.int16)[:, None].repeat(2, 1)


p = Project()
p.outputs = [Output("O", "o")]
p.channels = [Channel("m", "Музыка", volume=1.0, outputs=["O"])]
p.player.playlist = [{"path": "a", "title": "A", "dur": 1.0}, {"path": "b", "title": "B", "dur": 1.0},
                     {"path": "c", "title": "C", "dur": 1.0}]
p.player.crossfade = 0.0
p.player.volume = 1.0
p.repair()
assert p.player.channel == "m"
e = Engine()
for k, f in (("a", 300), ("b", 600), ("c", 900)):
    e.cache._data[k] = tone(f, 1.0)
e.cache.forget = lambda path: None               # в тесте «файлы» подложены в кэш вручную
e.project = p
e.apply_project(p)
pl = e.player


def run(blocks):
    out = []
    for _ in range(blocks):
        out.append(e.render_block()["O"].copy())
    return np.concatenate(out)


def freq(x):
    x = x[:, 0]
    if np.abs(x).max() < 0.01:
        return 0
    s = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return int(round(np.fft.rfftfreq(len(x), 1 / SR)[s.argmax()] / 10) * 10)


pl.command("play")
a = run(20)
assert pl.state == "playing" and freq(a) == 300, (pl.state, freq(a))
pl.command("pause")
a = run(10)
assert pl.state == "paused" and np.abs(a).max() < 0.01
pos = pl.position()
pl.command("play")
a = run(5)
assert pl.state == "playing" and abs(pl.position() - pos - 0.05) < 0.02, (pl.position(), pos)
pl.command("next")
a = run(20)
assert pl.cfg.index == 1 and freq(a[2400:]) == 600, (pl.cfg.index, freq(a))
run(100)                                         # трек B доиграл → сам перешёл на C
assert pl.cfg.index == 2 and pl.state == "playing", (pl.cfg.index, pl.state)
pl.seek(0.5)
assert abs(pl.position() - 0.5) < 0.02
e.stop_all()
run(20)
assert pl.state == "paused" and pl.paused_pos > 0.4, (pl.state, pl.paused_pos)
pl.command("play")
run(3)
assert pl.state == "playing" and pl.position() > 0.4
# плавный переход между треками
p.player.crossfade = 0.3
pl.play(0)
run(70)                                          # 0.7 с — переход ещё не начался
both = run(10)                                   # 0.7–0.8 с: идёт кроссфейд A→B
s = np.abs(np.fft.rfft(both[:, 0] * np.hanning(len(both))))
f = np.fft.rfftfreq(len(both), 1 / SR)
a_lvl, b_lvl = s[np.argmin(abs(f - 300))], s[np.argmin(abs(f - 600))]
assert a_lvl > 0 and b_lvl > 0 and pl.cfg.index == 1, (a_lvl, b_lvl, pl.cfg.index)
pl.command("stop")
run(10)
assert pl.state == "stopped" and np.abs(run(5)).max() < 0.01
print("player ok")
