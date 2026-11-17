"""Дрампад: точность петли, запись с выравниванием, свинг, отсчёт, группы, повтор ноты, экспорт, проекты."""
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

TMP = tempfile.mkdtemp()
os.environ["APPDATA"] = TMP
os.environ["HOME"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app.audio.engine as em  # noqa: E402
from app.audio import drums as dm  # noqa: E402
from app.audio.ring import Ring  # noqa: E402
from app.input.router import InputRouter, execute  # noqa: E402
from app.model import Channel, Output, Project  # noqa: E402

SR = 48000
N = 480


class FakeDev:
    def __init__(self, oid, n, h, sr):
        self.out_id, self.ring, self.error, self.stream, self.name, self.hostapi, self.volume = oid, Ring(SR), "", None, n, h, 1

    def start(self):
        return True

    def stop(self):
        pass


em.OutputDevice = FakeDev
p = Project()
p.outputs = [Output("PH", "Наушники", device="a"), Output("DS", "Дискорд", device="b")]
p.channels = [Channel("dr", "Дрампад", volume=1.0, outputs=["PH", "DS"])]
p.settings.preview_output = "PH"
p.drum = {"channel": "dr", "bpm": 120.0, "bars": 1, "beats": 4, "metro": "off"}
p.repair()
eng = em.Engine()
eng.project = p
eng.apply_project(p)
dr = eng.drums
cfg = p.drum
assert dr.cfg is cfg and all(s is not None for s in dr.samples), "встроенный набор должен загрузиться"
# для замеров времени — короткие щелчки вместо долгой бочки
cfg["pads"][0]["src"], cfg["pads"][1]["src"] = "kit:808:9", "kit:808:5"
dr.refresh()
spb = dr.spb()
assert spb == 24000 and dr.loop_len() == 96000


def run(blocks):
    out = {"PH": [], "DS": []}
    for _ in range(blocks):
        m = eng.render_block(N)
        for k in out:
            out[k].append(m[k].copy())
    return {k: np.concatenate(v) for k, v in out.items()}


def onsets(x, thr=0.05):
    a = np.abs(x).max(axis=1)
    on = np.nonzero((a[1:] > thr) & (a[:-1] <= thr))[0] + 1
    res = []
    for o in on:
        if not res or o - res[-1] > 2000:
            res.append(int(o))
    return res


# ------------------------------------------------------------ 1. петля играет точно в долю
cfg["events"] = [[0.0, 0, 110, 0], [1.0, 1, 110, 0], [2.0, 0, 110, 0], [3.0, 1, 110, 0]]
dr.changed()
dr.play()
o = run(400)                                     # 4 с = 2 прохода петли
ons = onsets(o["DS"])
want = [int(b * spb) for b in range(8)]
ATT = 16                                         # у звуков атака ~1,5 мс — порог пересекается чуть позже
assert len(ons) == 8 and all(0 <= a - b <= ATT for a, b in zip(ons, want)), (ons, want)
assert onsets(o["PH"]) == ons
dr.stop()
run(150)
print("loop timing ok:", ons[:4])

# ------------------------------------------------------------ 2. свинг
cfg["events"] = [[0.0, 0, 110, 0], [0.25, 0, 110, 0]]
cfg["swing"] = 66
dr.changed()
dr.play()
o = run(60)
ons = onsets(o["DS"], 0.05)
assert 0 <= ons[1] - int((0.25 + 0.16 * 0.5) * spb) - (ons[0]) <= ATT, ons
cfg["swing"] = 50
dr.stop()
run(150)
print("swing ok:", ons[:2])

# ------------------------------------------------------------ 3. запись с выравниванием
cfg["events"] = []
cfg["comp_auto"] = False
cfg["comp_ms"] = 0
cfg["grid"] = "1/16"
dr.changed()
dr.play()
run(int(1.03 * spb / N))                          # чуть позже 2-й доли
dr.toggle_rec()
assert dr.recording and dr.countin == 0           # уже играет — без отсчёта
dr.hit(0, 100)
run(1)
ev = cfg["events"]
assert len(ev) == 1 and ev[0][0] == 1.0 and ev[0][1] == 0, ev  # выровнено назад к доле 2
# удар чуть раньше доли: выравнивание вперёд — и он не должен прозвучать второй раз в этом проходе
run(int(0.95 * spb / N) - 1)
dr.hit(0, 100)
before = len(dr.voices)
o = run(10)
assert cfg["events"][-1][0] == 2.0, cfg["events"]
ons = onsets(o["DS"], 0.05)
assert len(ons) <= 1, f"двойной удар: {ons}"
dr.toggle_rec()
assert dr.undo() and not cfg["events"]
dr.stop()
run(150)
print("record + quantize ok")

# ------------------------------------------------------------ 4. отсчёт и метроном только в наушники
cfg["metro"] = "rec"
cfg["countin"] = True
cfg["events"] = [[0.0, 0, 120, 0]]
dr.changed()
dr.toggle_rec()
assert dr.countin == int(4 * spb)
o = run(int(4 * spb / N) + 20)
ph, ds = onsets(o["PH"], 0.05), onsets(o["DS"], 0.05)
assert len(ph) >= 4 and ph[0] <= 2, ph            # щелчки отсчёта в наушниках
assert ds and 0 <= ds[0] - 4 * spb <= ATT, ds       # в Дискорде — только бочка после отсчёта
dr.stop()
cfg["metro"] = "off"
run(150)
print("count-in ok")

# ------------------------------------------------------------ 5. группы (закрытый хэт глушит открытый)
assert cfg["pads"][2]["choke"] == cfg["pads"][3]["choke"] == 1
dr.hit(3, 120, record=False)
run(5)
assert any(v[4] == 3 for v in dr.voices)
dr.hit(2, 120, record=False)
run(3)
assert not any(v[4] == 3 for v in dr.voices), "открытый хэт должен заглохнуть"
run(100)
print("choke ok")

# ------------------------------------------------------------ 6. повтор ноты
cfg["repeat"] = True
cfg["repeat_grid"] = "1/8"
cfg["events"] = []
dr.changed()
dr.play()
dr.hit(9, 100)
o = run(int(spb / N))                             # одна доля → удар + повтор на 1/8
dr.release(9)
o2 = run(int(spb / N))
assert len(onsets(o["DS"], 0.02)) >= 2 and len(onsets(o2["DS"], 0.02)) == 0
cfg["repeat"] = False
dr.stop()
run(150)
print("note repeat ok")

# ------------------------------------------------------------ 7. клавиши и MIDI через роутер
r = InputRouter(eng)
r.set_project(p)
dr.ui_focus = True
r.handle_key("z", True)
r.handle_key("z", False)
assert dr.queue and dr.queue[-1][0] == 0
r.handle_key("shift", True)
r.handle_key("x", True)
assert dr.queue[-1][:2] == (1, 127)                # Shift — акцент
r.handle_key("x", False)
r.handle_key("shift", False)
r.handle_key("ctrl", True)
r.handle_key("z", True)                            # Ctrl+Z — не пэд
r.handle_key("z", False)
r.handle_key("ctrl", False)
assert len(dr.queue) == 2


class Msg:
    def __init__(self, t, note, vel):
        self.type, self.note, self.velocity, self.channel = t, note, vel, 9


r.handle_midi("pads", Msg("note_on", 38, 64))
assert dr.queue[-1][:2] == (2, 64)
r.handle_key("space", True)
r.handle_key("space", False)
assert dr.playing
execute(eng, {"type": "drum", "op": "play_stop"}, "press")
assert not dr.playing
dr.ui_focus = False
r.handle_key("z", True)
r.handle_key("z", False)
assert len(dr.queue) == 3, "вкладка не открыта — клавиша не пэд"
run(50)
print("keys + midi ok")

# ------------------------------------------------------------ 8. экспорт и проекты
cfg["events"] = [[0.0, 0, 110, 0], [1.5, 1, 110, 0], [3.5, 3, 110, 0]]   # откр. хэт в конце — хвост
dr.changed()
loop = dr.render_offline(1, "loop")
assert len(loop) == dr.loop_len()
assert np.abs(loop[:2000]).max() > 0.1             # бочка + завёрнутый хвост хэта
f = dr.render_offline(4, "file")
assert 4 * dr.loop_len() <= len(f) <= 4 * dr.loop_len() + 4 * SR and np.abs(f).max() <= 0.981
dm.save_beat(cfg, "Тест бит")
assert "Тест бит" in dm.list_beats()
cfg["events"] = []
cfg["bpm"] = 140.0
dm.load_beat(cfg, "Тест бит")
assert len(cfg["events"]) == 3 and cfg["bpm"] == 120.0
dr.double()
assert cfg["bars"] == 2 and len(cfg["events"]) == 6
print("export + beats ok")
print("drums ok")
