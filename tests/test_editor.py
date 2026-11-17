"""Проверка монтажа, записи из коммутации и настроек интерфейса."""
import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QPushButton

from app.audio.decoder import decode_file
from app.audio.editor import (EditorProject, EffectWindow, RenderCancelled, Track, export_audio,
                              export_formats, import_clip, mix_blocks, preview_audio)
from app.audio.engine import BLOCK, SR, Engine, SampleVoice
from app.input.router import InputRouter
from app.model import Channel, EffectSlot, Output, Pad, Project
from app.ui.context import Ctx
from app.ui.editor_effects import EditorEffectsDialog
from app.ui.editor_timeline import CLIPS, HEADER, RULER
from app.ui.main_window import MainWindow
from app.ui.quick_mixer import QuickSettingsDialog


def test_render(folder):
    src = folder / "source.wav"
    sf.write(src, np.full((SR, 2), 0.1, dtype=np.float32), SR)
    clip = import_clip(src, folder)
    gain = EffectSlot("fx", "gain", True, {"db": 6.0})
    track = Track("Test", clips=[clip], effects=[gain], points=[(0, 0), (SR, -12)],
                  windows=[EffectWindow(SR // 8, 3 * SR // 4, [gain]),
                           EffectWindow(SR // 3, 5 * SR // 6, [gain])])
    project = EditorProject.from_dict(EditorProject([track]).to_dict())
    mix = np.concatenate(list(mix_blocks(project)))
    assert len(mix) == SR
    for sec, level in ((.05, 6), (.25, 12), (.5, 18), (.8, 12), (.9, 6)):
        assert np.isclose(mix[round(sec * SR), 0], .1 * 10 ** ((level - 12 * sec) / 20), atol=.001)
    preview = preview_audio(project, SR // 4, SR // 2)
    assert len(preview) == SR // 4
    assert np.allclose(preview / 32767, mix[SR // 4:SR // 2], atol=.00004)
    dst = folder / "mix.wav"
    export_audio(project, dst)
    out, sr = sf.read(dst)
    assert sr == SR and np.allclose(out, mix, atol=.00004)

    t = np.arange(SR) / SR
    tone = np.sin(2 * np.pi * 440 * t) * .2
    sf.write(src, np.column_stack((tone, tone)), SR)
    source_clip = import_clip(src, folder)
    dry_project = EditorProject([Track("Tone", clips=[source_clip])])
    formats = export_formats()
    assert {"wav", "mp3", "flac", "ogg", "opus", "m4a", "aiff"} <= formats.keys()
    for suffix in formats:
        output = folder / f"export.{suffix}"
        export_audio(dry_project, output)
        encoded = decode_file(str(output))
        assert abs(len(encoded) - SR) < 2048, suffix
        peak = np.abs(np.fft.rfft(encoded[4000:40000, 0])).argmax()
        assert abs(peak * SR / 36000 - 440) < 3, suffix
    try:
        export_audio(dry_project, dst, cancelled=lambda: True)
    except RenderCancelled:
        assert np.array_equal(sf.read(dst)[0], out)
    else:
        raise AssertionError("Отмена должна сохранить прежний файл")

    clip = source_clip.stretched(SR * 2)
    data = np.concatenate(list(mix_blocks(EditorProject([Track("Tone", clips=[clip])]))))
    assert len(data) == SR * 2
    assert abs(np.abs(np.fft.rfft(data[:, 0])).argmax() / 2 - 440) < 2
    cropped = clip.crop(1000, 5500).flipped()
    cropped = replace(cropped, start=0)
    fragment = np.concatenate(list(mix_blocks(EditorProject([Track("Crop", clips=[cropped])]))))
    assert np.array_equal(fragment, data[1000:5500][::-1])
    return src


def mixer_with_record_source():
    project = Project(outputs=[Output("bus", "Bus", bus=True, volume=.5, sends=["rec"]),
                               Output("rec", "Recorder", bus=True, recorder="replay")],
                      channels=[Channel("ch", "Channel", volume=1, outputs=["bus"])],
                      pads=[Pad("pad", "Pad", channel="ch")])
    engine = Engine()
    engine.apply_project(project)
    engine.channels["ch"].voices.append(
        SampleVoice("pad", "ch", 1, 0, frozenset(), np.full((SR, 2), 16384, np.int16), 0, SR, True))
    return project, engine


def test_capture(folder):
    project, engine = mixer_with_record_source()
    results = []
    done = threading.Event()

    def recorded(event):
        results.append(event)
        done.set()

    for source, value in (("pad", .5), ("ch", .5), ("bus", .25), ("rec", .25)):
        done.clear()
        engine.start_editor_recording(source, folder, recorded)
        for _ in range(5):
            engine.render_block()
        if source == "rec":
            project.output(source).enabled = False
            engine.render_block()
        else:
            engine.stop_editor_recording()
        assert done.wait(3)
        take = results[-1]
        assert take["kind"] == "file", take
        assert not take["add_pad"] and take["rec"] == source
        samples, sr = sf.read(take["path"])
        assert sr == SR and len(samples) == BLOCK * 5
        assert np.max(np.abs(samples - value)) < .0001
    engine.shutdown()


def wait(app, predicate):
    deadline = time.monotonic() + 4
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert predicate()


def test_ui(source):
    app = QApplication([])
    project, engine = mixer_with_record_source()
    win = MainWindow(Ctx(project, engine, InputRouter(engine)))
    win.resize(900, 580)
    win.show()
    app.processEvents()
    assert win.tabs.count() == 8
    assert win.tabs.strip._rects()[-1].right() < win.tabs.strip.width()
    checks = []

    def inspect_quick():
        dialog = app.activeModalWidget()
        checks.append(isinstance(dialog, QuickSettingsDialog) and dialog.isVisible() and not win.quick.isVisible())
        win.toggle_quick()
        checks.append(not win.quick.isVisible())
        dialog.reject()

    win.toggle_quick()
    QTimer.singleShot(0, inspect_quick)
    gear = next(button for button in win.quick.findChildren(QPushButton) if button.text() == "НАСТРОИТЬ")
    gear.click()
    win.toggle_quick()
    QTimer.singleShot(0, inspect_quick)
    win.open_quick_settings()
    assert all(checks) and len(checks) == 4

    def hide_tabs():
        dialog = app.activeModalWidget()
        for cb in dialog.findChildren(QCheckBox):
            if cb.text() in ("Плеер", "Дрампад"):
                cb.setChecked(False)
        dialog.accept()

    QTimer.singleShot(0, hide_tabs)
    win.open_tabs_settings()
    assert win.tabs.count() == 6 and win.tabs.indexOf(win.player) == -1
    assert Project.from_dict(project.to_dict()).ui["hidden_tabs"] == ["player", "drums"]
    win.tabs.setCurrentWidget(win.editor)
    win.activateWindow()
    app.processEvents()
    editor = win.editor
    editor.import_paths([str(source)])
    wait(app, lambda: editor._job is None and bool(editor.timeline.waveforms))
    track = editor.project.tracks[0]
    assert len(track.clips) == 1
    view = editor.timeline.viewport()
    QTest.mouseClick(view, Qt.LeftButton, pos=QPoint(HEADER + 20, RULER + CLIPS + 18))
    assert len(track.points) == 1
    QTest.mousePress(view, Qt.LeftButton, Qt.ShiftModifier, QPoint(HEADER + 20, RULER + 24))
    QTest.mouseMove(view, QPoint(HEADER + 65, RULER + 24))
    QTest.mouseRelease(view, Qt.LeftButton, Qt.ShiftModifier, QPoint(HEADER + 65, RULER + 24))
    assert editor.timeline.range and editor.timeline.selected_clip == track.clips[0].id
    editor._clip_action("reverse")
    assert [clip.reverse for clip in track.clips] == [False, True, False]
    editor._undo_edit()
    assert len(editor.project.tracks[0].clips) == 1

    timeline = editor.timeline
    track = editor.project.tracks[0]
    first = track.clips[0]
    timeline.cursor = SR // 5
    editor.import_paths([str(source)])
    wait(app, lambda: editor._job is None)
    second = track.clips[1]
    assert timeline.rows()[0][0].lanes == [[first], [second]]

    lower = RULER + CLIPS + 24
    QTest.mouseClick(view, Qt.LeftButton, pos=QPoint(HEADER + 42, lower))
    assert timeline.selected_clip == second.id
    QTest.mousePress(view, Qt.LeftButton, pos=QPoint(HEADER + 42, RULER // 2))
    QTest.mouseMove(view, QPoint(HEADER + 62, RULER // 2))
    assert timeline.cursor == SR * 62 // 100
    QTest.mouseRelease(view, Qt.LeftButton, pos=QPoint(HEADER + 62, RULER // 2))
    assert timeline.selected_clip == second.id
    QTest.keyClick(timeline, Qt.Key_K, Qt.ControlModifier)
    assert len(track.clips) == 3 and editor._clip(track).start == SR * 62 // 100
    editor._undo_edit()

    track = editor.project.tracks[0]
    QTest.mousePress(view, Qt.LeftButton, Qt.ShiftModifier, QPoint(HEADER + 35, lower))
    QTest.mouseMove(view, QPoint(HEADER + 65, lower))
    QTest.mouseRelease(view, Qt.LeftButton, Qt.ShiftModifier, QPoint(HEADER + 65, lower))
    QTest.keyClick(timeline, Qt.Key_T, Qt.ControlModifier)
    trimmed = editor._clip(track)
    assert trimmed.start == SR * 35 // 100 and trimmed.end == SR * 65 // 100
    assert trimmed.offset > 0
    QTest.mousePress(view, Qt.LeftButton, Qt.ShiftModifier, QPoint(HEADER + 65, lower))
    QTest.mouseMove(view, QPoint(HEADER + 95, lower))
    QTest.mouseRelease(view, Qt.LeftButton, Qt.ShiftModifier, QPoint(HEADER + 95, lower))
    stretched = editor._clip(track)
    assert stretched.start == trimmed.start and stretched.length == trimmed.length * 2
    assert stretched.speed == 0.5 and stretched.offset == 0
    QTest.mousePress(view, Qt.LeftButton, Qt.ShiftModifier, QPoint(HEADER + 35, lower))
    QTest.mouseMove(view, QPoint(HEADER + 65, lower))
    QTest.mouseRelease(view, Qt.LeftButton, Qt.ShiftModifier, QPoint(HEADER + 65, lower))
    stretched = editor._clip(track)
    assert stretched.start == SR * 65 // 100 and stretched.end == SR * 95 // 100
    assert stretched.speed == 1.0
    assert track.clips[0].start == 0 and track.clips[0].length == SR
    QTest.mouseClick(view, Qt.LeftButton, pos=QPoint(HEADER + 40, RULER + CLIPS * 2 + 18))
    assert len(track.points) == 2

    export_path = source.parent / "export-from-editor"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(export_path), "MP3 (*.mp3)")):
        editor.export_button.click()
    wait(app, lambda: editor._job is None)
    assert export_path.with_suffix(".mp3").is_file()

    dialog = EditorEffectsDialog("Эффект", [], editor, (0, SR))
    dialog._add("gain")
    assert dialog.buttons.buttons()[0].isEnabled()
    dialog._remove()
    assert not dialog.buttons.buttons()[0].isEnabled()

    editor.sources.setCurrentIndex(editor.sources.findData("bus"))
    editor.record_button.click()
    for _ in range(5):
        engine.render_block()
    editor.record_button.click()
    wait(app, lambda: editor._take is None)
    assert len(editor.project.tracks[0].clips) == 3
    assert len(project.editor["tracks"][0]["clips"]) == 3
    win.shutdown()
    win.close()
    engine.shutdown()


if __name__ == "__main__":
    with TemporaryDirectory() as tmp:
        folder = Path(tmp)
        os.environ["APPDATA"] = tmp
        test_ui_source = test_render(folder)
        test_capture(folder)
        test_ui(test_ui_source)
    print("editor ok")
