"""Типовые схемы «наушники + Дискорд»: микрофон и звуки должны доходить туда, куда протянуты провода.

1. Движок: канал → кабель + наушники; всё → кабель, кабель → наушники; своя разводка звука; шина.
2. Интерфейс «Коммутации»: протянуть провод от раскрытого звука «Микрофон» к наушникам —
   микрофон должен остаться и в Дискорде (раньше он оттуда пропадал).
"""
import os
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app.audio.engine as em  # noqa: E402
from app.audio.ring import Ring  # noqa: E402
from app.audio.sources import LiveSource  # noqa: E402
from app.model import Channel, LiveSource as LS, Output, Pad, Project  # noqa: E402


class FakeDev:
    def __init__(self, oid, n, h, sr):
        self.out_id, self.ring, self.error, self.stream, self.name, self.hostapi, self.volume = oid, Ring(48000), "", None, n, h, 1

    def start(self):
        return True

    def stop(self):
        pass


class Mic(LiveSource):
    """«Микрофон», который всегда что-то говорит (синус 300 Гц)."""
    def __init__(self):
        super().__init__(48000)
        self.n = 0

    def start(self):
        return True

    def feed(self, frames):
        t = np.arange(self.n, self.n + frames)
        self.n += frames
        x = (0.5 * np.sin(2 * np.pi * 300 * t / 48000)).astype(np.float32)
        self.ring.write(np.stack([x, x], 1))


em.OutputDevice = FakeDev
MICS = []


def _src(src, sr):
    m = Mic()
    MICS.append(m)
    return m


em.make_source = _src
tone = (0.5 * np.sin(np.arange(48000 * 2) / 48000 * 2 * np.pi * 440))[:, None].repeat(2, 1)


def build(mic_outs, sfx_outs, sends=None, own=None):
    p = Project()
    p.outputs = [Output("PH", "Мои наушники", device="Headphones"),
                 Output("DS", "Дискорд", device="CABLE Input (VB-Audio Virtual Cable)"),
                 Output("BUS", "Шина", bus=True)]
    for o in p.outputs:
        o.sends = list((sends or {}).get(o.id, []))
    p.channels = [Channel("mic", "Микрофон", volume=1.0, outputs=mic_outs),
                  Channel("sfx", "Эффекты", volume=1.0, outputs=sfx_outs)]
    p.pads = [Pad("pm", "Микрофон", kind="live", channel="mic", mode="toggle", source=LS(type="input")),
              Pad("ps", "Бум", file="t", channel="sfx", loop=True, mode="toggle")]
    if own is not None:
        p.pads[0].route_own, p.pads[0].outputs = True, own
    p.repair()
    return p


def levels(p):
    MICS.clear()
    eng = em.Engine()
    eng.cache._data["t"] = (tone * 32767).astype(np.int16)
    eng.project = p
    eng.apply_project(p)
    eng.pad_event("pm", "press")
    eng.pad_event("ps", "press")
    res = {}
    for i in range(80):
        for m in MICS:
            m.feed(480)
        mm = eng.render_block()
        if i > 40:
            for k, v in mm.items():
                res[k] = max(res.get(k, 0.0), float(abs(v).max()))
    return eng, {k: round(v, 2) for k, v in res.items()}


def has_mic(eng, p, out_id):
    """Есть ли в выходе микрофон: глушим «Эффекты» и смотрим уровень."""
    p.channel("sfx").muted = True
    for _ in range(30):
        for m in MICS:
            m.feed(480)
        mm = eng.render_block()
    p.channel("sfx").muted = False
    return float(abs(mm[out_id]).max()) > 0.2


cases = [
    ("микрофон → Дискорд + наушники", build(["DS", "PH"], ["DS", "PH"]), {"DS": True, "PH": True}),
    ("всё → кабель, кабель → наушники", build(["DS"], ["DS"], sends={"DS": ["PH"]}), {"DS": True, "PH": True}),
    ("микрофон только в Дискорд", build(["DS"], ["DS", "PH"]), {"DS": True, "PH": False}),
    ("своя разводка микрофона: оба", build(["DS"], ["PH"], own=["DS", "PH"]), {"DS": True, "PH": True}),
    ("через шину → оба", build(["BUS"], ["BUS"], sends={"BUS": ["DS", "PH"]}), {"DS": True, "PH": True}),
]
for label, p, want in cases:
    eng, lv = levels(p)
    got = {o: has_mic(eng, p, o) for o in want}
    assert got == want, (label, got, lv)
    print(f"{label:<36} микрофон: {got}  уровни: {lv}")
print("engine schemes ok")

# --------------------------------------------------------------------- интерфейс «Коммутации»
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
from app.input.router import InputRouter  # noqa: E402
from app.ui.context import Ctx  # noqa: E402
from app.ui import theme  # noqa: E402
import app.ui.context as ctxmod  # noqa: E402

ctxmod.save_project = lambda project: None          # не трогаем настоящий конфиг
theme.load_fonts()
p = build(["DS"], ["DS", "PH"])
p.ui["expanded"] = ["mic"]
eng = em.Engine()
eng.cache._data["t"] = (tone * 32767).astype(np.int16)
eng.apply_project(p)
ctx = Ctx(p, eng, InputRouter(eng))
from app.ui.main_window import MainWindow  # noqa: E402

win = MainWindow(ctx)
page = win.routing
page._rebuild_now()
pad_node = page.padnodes["pm"]
page.connect(pad_node, page.nodes["PH"])
pad = p.pad("pm")
assert pad.route_own and set(pad.outputs) == {"DS", "PH"}, pad.outputs
eng.project = p
_, lv = levels(p)
eng2, _ = levels(p)
assert has_mic(eng2, p, "DS") and has_mic(eng2, p, "PH")
print("ui: провод от микрофона к наушникам — микрофон остался в Дискорде:", pad.outputs)
win.shutdown()
print("schemes ok")
