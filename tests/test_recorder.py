"""Блоки записи: короткие звуки в саундпад и длинная запись в файл."""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMP = tempfile.mkdtemp()
os.environ["APPDATA"] = TMP
os.environ["HOME"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import soundfile as sf  # noqa: E402

import app.audio.engine as em  # noqa: E402
from app.audio.recorder import available_formats, process_clip, rec_defaults  # noqa: E402
from app.audio.ring import Ring  # noqa: E402
from app.input.router import execute  # noqa: E402
from app.model import Binding, Channel, Output, Pad, Project, Trigger  # noqa: E402

SR = 48000


class FakeDev:
    def __init__(self, oid, n, h, sr):
        self.out_id, self.ring, self.error, self.stream, self.name, self.hostapi, self.volume = oid, Ring(SR), "", None, n, h, 1

    def start(self):
        return True

    def stop(self):
        pass


em.OutputDevice = FakeDev
events = []
done = threading.Event()


def on_rec(ev):
    events.append(ev)
    done.set()


def wait_event():
    assert done.wait(10), "запись не сохранилась"
    done.clear()
    return events[-1]


# голос: 0,4 с тишины, 0,8 с «речи», 0,5 с тишины
voice = np.zeros((int(SR * 1.7), 2), np.float32)
t = np.arange(int(SR * 0.8)) / SR
voice[int(SR * 0.4):int(SR * 0.4) + len(t)] = (0.3 * np.sin(2 * np.pi * 220 * t))[:, None]

p = Project()
p.outputs = [Output("PH", "Наушники", device="x"),
             Output("CLIP", "Голос в звуки", bus=True, recorder="clip",
                    rec=dict(rec_defaults("clip"), channel="sfx", slots="ctrl+alt+digits")),
             Output("FILE", "Запись в файл", bus=True, recorder="file",
                    rec=dict(rec_defaults("file"), folder=str(Path(TMP) / "rec"), fmt="flac"))]
p.channels = [Channel("mic", "Микрофон", volume=1.0, outputs=["PH", "CLIP", "FILE"]),
              Channel("sfx", "Эффекты", volume=1.0, outputs=["PH"])]
p.pads = [Pad("v", "голос", file="voice", channel="mic", mode="toggle")]
p.repair()
assert p.output("CLIP").bus and p.output("CLIP").sends == []
eng = em.Engine()
eng.cache._data["voice"] = (voice * 32767).astype(np.int16)
eng.on_record = on_rec
eng.project = p
eng.apply_project(p)
assert "CLIP" not in eng.outputs and set(eng.recorders) == {"CLIP", "FILE"}


def blocks(n):
    for _ in range(n):
        eng.render_block()


# ---------------------------------------------------------- короткий звук, «пока держишь»
hold = {"type": "record", "rec": "CLIP", "op": "hold"}
eng.pad_event("v", "press")
blocks(20)                                         # 0,2 с тишины — попадут в предзахват
execute(eng, hold, "press")
assert eng.rec_states()["CLIP"]["on"]
blocks(150)                                        # 1,5 с
execute(eng, hold, "release")
ev = wait_event()
assert ev["kind"] == "clip", ev
y, sr = sf.read(ev["path"])
assert sr == SR and 0.8 < len(y) / SR < 1.05, len(y) / SR        # тишина по краям обрезана
assert 0.85 < np.abs(y).max() < 0.92, np.abs(y).max()           # громкость выровнена до −1 дБ
print(f"clip ok: {len(y) / SR:.2f} с, пик {np.abs(y).max():.2f}")

# добавление в саундпад (как делает главное окно)
from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication(sys.argv)
from app.ui.main_window import add_recorded_pad  # noqa: E402


class C:
    project = p

    def apply(self):
        p.repair()


ctx = C()
names = []
for k in range(11):
    pad, combo = add_recorded_pad(ctx, dict(ev, path=ev["path"]))
    names.append((pad.name, combo))
assert names[0] == ("Запись 1", "ctrl+alt+1") and names[8][1] == "ctrl+alt+9", names
assert names[9][1] == "ctrl+alt+1" and names[10][1] == "ctrl+alt+2", names   # клавиши переходят к новым записям
assert all(pp.channel == "sfx" for pp in p.pads if pp.name.startswith("Запись"))
by = {b.trigger.combo: b.action["pad"] for b in p.bindings}
assert p.pad(by["ctrl+alt+1"]).name == "Запись 10"
print("pads ok:", names[-3:])

# только тишина — звук не создаётся, приходит понятная ошибка
eng.pad_event("v", "press")       # выключили голос
blocks(30)
execute(eng, hold, "press")
blocks(50)
execute(eng, hold, "release")
ev = wait_event()
assert ev["kind"] == "error" and "тишина" in ev["error"], ev
print("silence ok")

# ---------------------------------------------------------- длинная запись в файл
eng.pad_event("v", "press")
execute(eng, {"type": "record", "rec": "FILE", "op": "toggle"}, "press")
blocks(300)                                         # 3 с
execute(eng, {"type": "record", "rec": "FILE", "op": "toggle"}, "press")
ev = wait_event()
assert ev["kind"] == "file" and ev["path"].endswith(".flac"), ev
info = sf.info(ev["path"])
assert abs(info.duration - 3.3) < 0.05, info.duration          # + 0,3 с предзахвата
print(f"file ok: {Path(ev['path']).name} {info.duration:.2f} с")

# ограничение длины — запись останавливается сама
p.output("FILE").rec["max_s"] = 1
p.output("FILE").rec["fmt"] = "mp3" if "mp3" in available_formats() else "wav"
eng.apply_project(p)
eng.rec_command("FILE", "start")
blocks(200)
assert not eng.rec_states()["FILE"]["on"]
ev = wait_event()
info = sf.info(ev["path"])
assert 0.9 < info.duration < 1.4, info.duration
print(f"max length ok ({Path(ev['path']).suffix}, {info.duration:.2f} с)")

# удаление блока во время записи: файл закрывается
eng.rec_command("FILE", "start")
blocks(20)
p.outputs = [o for o in p.outputs if o.id != "FILE"]
p.repair()
eng.apply_project(p)
ev = wait_event()
assert ev["kind"] == "file" and "FILE" not in eng.recorders
assert process_clip(np.zeros((4800, 2), np.float32), SR) is None
print("recorder ok")
