"""Заглушение выхода, задом наперёд, случайные эффекты на звук, блок «повтор»."""
import os
import sys
import tempfile
import threading
from pathlib import Path

import numpy as np

TMP = tempfile.mkdtemp()
os.environ["APPDATA"] = TMP
os.environ["HOME"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import soundfile as sf  # noqa: E402

import app.audio.engine as em  # noqa: E402
from app.audio.recorder import rec_defaults  # noqa: E402
from app.audio.ring import Ring  # noqa: E402
from app.input.router import execute  # noqa: E402
from app.model import Channel, Output, Pad, Project  # noqa: E402

SR = 48000


class FakeDev:
    def __init__(self, oid, n, h, sr):
        self.out_id, self.ring, self.error, self.stream, self.name, self.hostapi, self.volume = oid, Ring(SR), "", None, n, h, 1

    def start(self):
        return True

    def stop(self):
        pass


em.OutputDevice = FakeDev
t = np.arange(SR * 3) / SR
tone = (0.4 * np.sin(2 * np.pi * 440 * t))[:, None].repeat(2, 1)
ramp = np.linspace(-0.9, 0.9, SR * 2)[:, None].repeat(2, 1)        # «пила»: видно направление

p = Project()
p.outputs = [Output("PH", "Наушники", device="a"), Output("DS", "Дискорд", device="b", sends=["REC"]),
             Output("REC", "Повтор", bus=True, recorder="replay",
                    rec=dict(rec_defaults("replay"), replay_s=2, trim=False, normalize=False))]
p.channels = [Channel("sfx", "Эффекты", volume=1.0, outputs=["PH", "DS"])]
p.pads = [Pad("t", "тон", file="tone", channel="sfx", loop=True, mode="toggle"),
          Pad("r", "пила", file="ramp", channel="sfx", retrigger="restart"),
          Pad("n", "шум", file="noise", channel="sfx", retrigger="restart")]
p.repair()
eng = em.Engine()
eng.cache._data["tone"] = (tone * 32767).astype(np.int16)
eng.cache._data["ramp"] = (ramp * 32767).astype(np.int16)
noise = np.random.default_rng(2).normal(0, 0.2, (SR, 2)).clip(-1, 1)
eng.cache._data["noise"] = (noise * 32767).astype(np.int16)
events, done = [], threading.Event()
eng.on_record = lambda ev: (events.append(ev), done.set())
infos = []
eng.on_info = infos.append
eng.project = p
eng.apply_project(p)


def lv(blocks=10):
    for _ in range(blocks):
        m = eng.render_block()
    return {k: round(float(abs(v).max()), 2) for k, v in m.items()}


# ---------------------------------------------------------------- 1. заглушить выход
eng.pad_event("t", "press")
assert lv()["DS"] > 0.3
mute = {"type": "mute_out", "output": "DS", "mode": "toggle"}
execute(eng, mute, "press")
m = eng.render_block()
assert 0 < abs(m["DS"][:20]).max() and abs(m["DS"][-20:]).max() < 0.02, "заглушение должно быть плавным"
r = lv()
assert r["DS"] == 0 and r["PH"] > 0.3 and r["REC"] == 0, r        # пересылка из заглушённого выхода тоже молчит
execute(eng, mute, "press")
assert lv()["DS"] > 0.3
hold = {"type": "mute_out", "output": "PH", "mode": "ptt"}       # «звук, только пока держишь»
execute(eng, hold, "release")
assert lv()["PH"] == 0
execute(eng, hold, "press")
assert lv()["PH"] > 0.3
print("mute output ok")
eng.pad_event("t", "press")
lv(20)

# ---------------------------------------------------------------- 2. задом наперёд
eng.pad_event("r", "press")
lv(30)
v = next(x for x in eng.channels["sfx"].voices if x.pad_id == "r")
pos0 = v.pos
rev = {"type": "reverse", "scope": "all", "mode": "hold"}
execute(eng, rev, "press")
m = eng.render_block()
seg = m["PH"][100:, 0]
assert v.pos < pos0 and np.all(np.diff(seg) <= 1e-6) and seg[-1] < seg[0], "пила должна пойти вниз"
lv(20)
execute(eng, rev, "release")
p1 = v.pos
m = eng.render_block()
seg = m["PH"][100:, 0]
assert v.pos > p1 and np.all(np.diff(seg) >= -1e-6) and seg[-1] > seg[0]
# дошёл до начала — молчит, но не заканчивается
execute(eng, rev, "press")
lv(150)
assert not v.done and abs(eng.render_block()["PH"]).max() == 0
execute(eng, rev, "release")
assert abs(lv(3)["PH"]) > 0.1
print("reverse ok")
eng.stop_all(fade_ms=5)
lv(20)

# ---------------------------------------------------------------- 3. случайные эффекты
seen = set()
for k in range(6):
    execute(eng, {"type": "pad_random", "pad": "n", "count": 3, "strength": 0.8}, "press")
    seen.add(infos[-1])
    v = next(x for x in eng.channels["sfx"].voices if x.pad_id == "n" and not x.stopping)
    assert v.fx is not None and len(v.fx_names) == 3
    r = lv(30)
    assert 0.02 < r["PH"] <= 1.0, (k, r, infos[-1], [(x.pad_id, x.src_done, x.dir, x.pos) for x in eng.channels["sfx"].voices])
assert len(seen) > 3, seen                                         # каждый раз новый набор
lv(800)                                                            # звук кончился, хвосты доиграли
assert not eng.channels["sfx"].voices, "голос со случайными эффектами должен закончиться"
print("random fx ok:", infos[-1])

# ---------------------------------------------------------------- 4. повтор последних секунд
eng.pad_event("t", "press")
eng.cache._data["tone"][: SR] //= 4                                # не важно — проверяем длину и содержимое
lv(300)                                                            # 3 с звука, буфер помнит 2
st = eng.rec_states()["REC"]
assert abs(st["buf"] - 2.0) < 0.02 and st["cap"] == 2.0, st
execute(eng, {"type": "record", "rec": "REC", "op": "save"}, "press")
assert done.wait(10)
ev = events[-1]
assert ev["kind"] == "clip" and ev["add_pad"], ev
y, sr = sf.read(ev["path"])
assert abs(len(y) / SR - 2.0) < 0.02 and np.abs(y).max() > 0.3, len(y) / SR
print("replay ok:", Path(ev["path"]).name)
print("features ok")
