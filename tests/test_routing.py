"""Пересылки «выход → выход», шины и защита от петель."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app.audio.engine as em  # noqa: E402
from app.audio.engine import Engine  # noqa: E402
from app.audio.ring import Ring  # noqa: E402
from app.model import Channel, Output, Pad, Project  # noqa: E402


class FakeDev:
    def __init__(self, oid, n, h, sr):
        self.out_id, self.ring, self.error, self.stream, self.name, self.hostapi, self.volume = oid, Ring(48000), "", None, n, h, 1

    def start(self):
        return True

    def stop(self):
        pass


em.OutputDevice = FakeDev
tone = (0.5 * np.sin(np.arange(48000 * 2) / 48000 * 2 * np.pi * 440))[:, None].repeat(2, 1)

p = Project()
p.outputs = [Output("A", "A"), Output("B", "B", sends=["H"]), Output("C", "C"), Output("H", "H", sends=["B"]),
             Output("X", "bus", bus=True, sends=["C"])]
p.channels = [Channel("m", "Музыка", volume=1.0, outputs=["A", "B"]), Channel("e", "Эффекты", volume=1.0, outputs=["X"])]
p.pads = [Pad("pm", "m", file="t", channel="m", loop=True, mode="toggle"),
          Pad("pe", "e", file="t", channel="e", loop=True, mode="toggle")]
p.repair()
assert p.output("B").sends == ["H"] and p.output("H").sends == [], "петля H→B должна быть отброшена"

eng = Engine()
eng.cache._data["t"] = (tone * 32767).astype(np.int16)
eng.project = p
eng.apply_project(p)
assert "X" not in eng.outputs, "у шины не должно быть своего устройства"
eng.pad_event("pm", "press")
for _ in range(30):
    m = eng.render_block()
lv = {k: round(float(abs(v).max()), 2) for k, v in m.items()}
assert lv["A"] > 0.3 and lv["B"] > 0.3 and lv["H"] > 0.3 and lv["C"] == 0, lv
eng.pad_event("pm", "press")
eng.pad_event("pe", "press")
for _ in range(60):
    m = eng.render_block()
lv = {k: round(float(abs(v).max()), 2) for k, v in m.items()}
assert lv["C"] > 0.3 and lv["A"] == 0 and lv["H"] == 0, lv
print("routing ok", lv)

# своя разводка отдельного звука: идёт только в свои выходы, мимо проводов канала («pe» всё ещё играет)
pad = p.pad("pe")
pad.route_own, pad.outputs = True, ["A"]
for _ in range(60):
    m = eng.render_block()
lv = {k: round(float(abs(v).max()), 2) for k, v in m.items()}
assert lv["A"] > 0.3 and lv["C"] == 0 and lv["X"] == 0, lv
pad.route_own = False
for _ in range(40):
    m = eng.render_block()
lv = {k: round(float(abs(v).max()), 2) for k, v in m.items()}
assert lv["A"] == 0 and lv["C"] > 0.3, lv
print("own route ok")

# ---------------------------------------------------------------- переключатели и эффекты на шине
q = Project()
q.outputs = [Output("O1", "o1"), Output("O2", "o2"), Output("SPL", "1→N", switch="split", sends=["O1", "O2"]),
             Output("MRG", "N→1", switch="merge", sends=["O1"])]
q.channels = [Channel("a", "a", volume=1.0, outputs=["MRG"]), Channel("b", "b", volume=1.0, outputs=["MRG"]),
              Channel("c", "c", volume=1.0, outputs=["SPL"])]
q.pads = [Pad("pa", "a", file="t", channel="a", loop=True, mode="toggle"),
          Pad("pb", "b", file="t", channel="b", loop=True, mode="toggle"),
          Pad("pc", "c", file="t", channel="c", loop=True, mode="toggle")]
q.repair()
assert q.output("MRG").sw_inputs == ["a", "b"] and q.output("MRG").bus, q.output("MRG")
e2 = Engine()
e2.cache._data["t"] = (tone * 32767).astype(np.int16)
e2.project = q
e2.apply_project(q)
assert "SPL" not in e2.outputs and "MRG" not in e2.outputs


def levels(engine, blocks=40):
    for _ in range(blocks):
        mm = engine.render_block()
    return {k: round(float(abs(v).max()), 2) for k, v in mm.items()}


e2.pad_event("pa", "press")
e2.pad_event("pb", "press")
lv = levels(e2)
assert 0.3 < lv["O1"] < 0.7, lv            # прошёл только вход «a» (не сумма a+b)
e2.switch_set("MRG", -1)
lv = levels(e2)
assert lv["O1"] == 0, lv                   # выключен
e2.switch_set("MRG", 1)
lv = levels(e2)
assert 0.3 < lv["O1"] < 0.7, lv
e2.pad_event("pa", "press")
e2.pad_event("pb", "press")
e2.switch_set("MRG", -1)
e2.pad_event("pc", "press")
lv = levels(e2)
assert lv["O1"] > 0.3 and lv["O2"] == 0, lv   # 1→N: положение 0 = O1
e2.switch_step("SPL", 1)
lv = levels(e2)
assert lv["O1"] == 0 and lv["O2"] > 0.3, lv
# плавность: сразу после переключения уровень меняется постепенно
e2.switch_step("SPL", 1)
mm = e2.render_block()
assert 0 < float(abs(mm["O1"]).max()) < 0.5, "переключение должно быть плавным"
# эффект на выходе-шине
from app.model import EffectSlot  # noqa: E402
from app.audio.effects import default_params  # noqa: E402
q.output("O1").effects = [EffectSlot("fx", "gain", True, {"gain_db": -12.0})]
e2.sync_effects("O1")
lv = levels(e2)
assert 0.05 < lv["O1"] < 0.2, lv
e2.set_effect("O1", "fx", False)
lv = levels(e2)
assert lv["O1"] > 0.4, lv
print("switches + bus fx ok")
