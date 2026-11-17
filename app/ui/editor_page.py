"""Вкладка многодорожечного монтажа и записи с блоков коммутации."""
from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from app.audio.decoder import AUDIO_FILTER, SR
from app.audio.editor import (Clip, EditorProject, EffectWindow, RenderCancelled, Track, export_audio,
                              export_formats, import_clip, preview_audio, waveform)
from app.model import data_dir, new_id
from .editor_effects import EditorEffectsDialog, time_field
from .editor_timeline import DB_MAX, DB_MIN, EditorTimeline
from .widgets import Meter, fmt_time


class EditorPage(QWidget):
    idle = Signal()
    _job_finished = Signal(object)
    _wave_finished = Signal(str, object)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("page")
        try:
            self.project = EditorProject.from_dict(ctx.project.editor)
            load_error = ""
        except (TypeError, ValueError, KeyError) as exc:
            self.project = EditorProject()
            load_error = f"Не удалось открыть сохранённый монтаж: {exc}"
        self.timeline = EditorTimeline(self.project)
        if self.project.tracks:
            self.timeline.selected_track = self.project.tracks[0].id
        self.timeline.selection_changed.connect(self._refresh)
        self.timeline.cursor_drag_started.connect(self._stop_preview)
        self.timeline.edited.connect(self._timeline_edit)
        self.timeline.window_opened.connect(self._edit_window)
        self.timeline.menu_requested.connect(self._context_menu)
        self._undo, self._redo = [], []
        self._pending_waves = set()
        self._job = None
        self._voice = None
        self._preview_start = 0
        self._take = None
        self._finalizing = False
        self._source_items = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 7, 8, 6)
        lay.setSpacing(5)
        top = QHBoxLayout()
        self.import_button = self._button(top, "Импорт аудио…", self._import_dialog)
        self.add_button = self._button(top, "+ Дорожка", self._add_track)
        self.undo_button = self._button(top, "↶", self._undo_edit, "Отменить (Ctrl+Z)")
        self.redo_button = self._button(top, "↷", self._redo_edit, "Повторить (Ctrl+Shift+Z)")
        top.addStretch(1)
        self.play_button = self._button(top, "Играть", self._play, "Воспроизвести от курсора или выделенный участок (Пробел)")
        self.stop_button = self._button(top, "Стоп", self._stop)
        top.addStretch(1)
        self.export_button = self._button(top, "Экспорт аудио…", self._export)
        lay.addLayout(top)

        tools = QHBoxLayout()
        self.track_button = QPushButton("Дорожка")
        self.track_button.setMenu(self._track_menu(self.track_button))
        tools.addWidget(self.track_button)
        self.clip_button = QPushButton("Клип")
        self.clip_button.setMenu(self._clip_menu(self.clip_button))
        tools.addWidget(self.clip_button)
        self.fx_button = self._button(tools, "Эффекты дорожки…", self._track_effects)
        self.window_button = self._button(tools, "+ Окно эффекта", self._add_window,
                                          "Растяните выделение на дорожке, чтобы задать границы окна")
        tools.addStretch(1)
        snap = QCheckBox("Сетка 0,1 с")
        snap.toggled.connect(lambda on: setattr(self.timeline, "snap", on))
        tools.addWidget(snap)
        self._button(tools, "−", lambda: self.timeline.zoom(1 / 1.5), "Уменьшить масштаб")
        self._button(tools, "+", lambda: self.timeline.zoom(1.5), "Увеличить масштаб")
        self._button(tools, "Уместить", self.timeline.zoom, "Показать всю запись")
        lay.addLayout(tools)

        rec = QHBoxLayout()
        rec.addWidget(QLabel("Запись из коммутации"))
        self.sources = QComboBox()
        self.sources.setMinimumContentsLength(12)
        self.sources.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sources.currentIndexChanged.connect(lambda: self._refresh())
        rec.addWidget(self.sources, 1)
        self.record_button = self._button(rec, "Записать", self._record,
                                          "Записать сигнал блока на выбранную дорожку с позиции курсора")
        self.record_status = QLabel()
        self.record_status.setMinimumWidth(116)
        rec.addWidget(self.record_status)
        self.meter = Meter(vertical=False)
        self.meter.setFixedSize(92, 12)
        rec.addWidget(self.meter)
        lay.addLayout(rec)
        lay.addWidget(self.timeline, 1)

        footer = QHBoxLayout()
        self.position = QLabel()
        self.position.setMinimumWidth(240)
        footer.addWidget(self.position)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("small")
        footer.addWidget(self.status, 1)
        lay.addLayout(footer)
        self._message(load_error or "Тяните курсор на шкале. Shift + край клипа меняет скорость. "
                      "Shift + внутри клипа выделяет участок, Ctrl+T его оставляет.")

        for keys, callback in (
            ("Space", self._toggle_play), ("Ctrl+Z", self._undo_edit), ("Ctrl+Shift+Z", self._redo_edit),
            ("Delete", self._delete_selection), ("Ctrl+D", lambda: self._clip_action("duplicate")),
            ("Ctrl+T", lambda: self._clip_action("trim")), ("Ctrl+K", lambda: self._clip_action("split")),
        ):
            shortcut = QShortcut(QKeySequence(keys), self, callback)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)

        self._job_finished.connect(self._done)
        self._wave_finished.connect(self._wave_ready)
        ctx.bridge.editor_recorded.connect(self._recorded, Qt.QueuedConnection)
        self.timer = QTimer(self)
        self.timer.setInterval(45)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.refresh_sources()
        self._refresh()

    @staticmethod
    def _button(layout, label, callback, tip=""):
        button = QPushButton(label)
        button.setToolTip(tip)
        button.clicked.connect(lambda: callback())
        layout.addWidget(button)
        return button

    def _message(self, text):
        self.status.setText(text)
        self.status.setToolTip(text)

    def _track(self):
        return next((t for t in self.project.tracks if t.id == self.timeline.selected_track), None)

    def _clip(self, track):
        return next((c for c in track.clips if c.id == self.timeline.selected_clip), None) if track else None

    def _window(self, track):
        return next((w for w in track.windows if w.id == self.timeline.selected_window), None) if track else None

    def _refresh(self):
        track = self._track()
        ready = self._job is None and self._take is None
        playable = any(t.clips for t in self.project.tracks)
        self.timeline.editable = ready
        for control, enabled in (
            (self.import_button, ready), (self.add_button, ready), (self.undo_button, ready and bool(self._undo)),
            (self.redo_button, ready and bool(self._redo)), (self.play_button, ready and playable),
            (self.stop_button, self._voice is not None or self._job is not None),
            (self.export_button, ready and playable), (self.track_button, ready and track is not None),
            (self.clip_button, ready and self._clip(track) is not None), (self.fx_button, ready and track is not None),
            (self.window_button, ready and track is not None and bool(self.timeline.range or self._clip(track))),
            (self.sources, ready),
            (self.record_button, (ready and bool(self.sources.currentData()))
             or (self._take is not None and not self._finalizing)),
        ):
            control.setEnabled(enabled)
        self.record_button.setText("Завершить" if self._take is not None else "Записать")
        if self.timeline.range:
            a, b = self.timeline.range
            self.position.setText(f"{fmt_time(a / SR)} — {fmt_time(b / SR)}   ({fmt_time((b - a) / SR)})")
        else:
            pos = self.timeline.playhead if self.timeline.playhead is not None else self.timeline.cursor
            self.position.setText(f"{fmt_time(pos / SR)} / {fmt_time(self.project.end / SR)}")
        self.timeline.viewport().update()

    def refresh_sources(self):
        if self._take is not None:
            return
        pr = self.ctx.project
        items = [(f"Канал · {t.name}", t.id) for t in pr.channels]
        for output in pr.outputs:
            if output.enabled:
                kind = ("Запись" if output.recorder else "Переключатель" if output.switch
                        else "Шина" if output.bus else "Выход")
                items.append((f"{kind} · {output.name}", output.id))
        items.extend((f"Звук · {pad.name}", pad.id) for pad in pr.pads)
        if items == self._source_items:
            return
        self._source_items = items
        selected = self.sources.currentData()
        self.sources.blockSignals(True)
        self.sources.clear()
        for label, ident in items:
            self.sources.addItem(label, ident)
        self.sources.setCurrentIndex(max(0, self.sources.findData(selected)))
        self.sources.blockSignals(False)
        if not items:
            self.sources.setPlaceholderText("Нет доступных блоков")
        self._refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_sources()
        self._load_waves()

    def _load_waves(self):
        paths = {c.path for t in self.project.tracks for c in t.clips}
        todo = paths - self.timeline.waveforms.keys() - self._pending_waves
        self._pending_waves.update(todo)
        if not todo:
            return

        def load():
            for path in todo:
                try:
                    value = waveform(path)
                except Exception as exc:
                    value = str(exc)
                try:
                    self._wave_finished.emit(path, value)
                except RuntimeError:
                    return

        threading.Thread(target=load, name="editor-waveforms", daemon=True).start()

    def _wave_ready(self, path, value):
        self._pending_waves.discard(path)
        self.timeline.waveforms[path] = value
        self.timeline.viewport().update()

    def _before_edit(self):
        self._stop_preview()
        self._undo.append(self.project.to_dict())
        del self._undo[:-100]
        self._redo.clear()

    def _persist(self):
        self.ctx.project.editor = self.project.to_dict()
        self.ctx.save_later()
        if self._track() is None:
            self.timeline.selected_track = self.project.tracks[0].id if self.project.tracks else None
            self.timeline.selected_clip = self.timeline.selected_window = None
            self.timeline.range = None
        self.timeline.update_layout()
        self._load_waves()
        self._refresh()

    def _restore(self, source, destination):
        if not self.timeline.editable or not source:
            return
        self._stop_preview()
        destination.append(self.project.to_dict())
        self.project = self.timeline.project = EditorProject.from_dict(source.pop())
        self.timeline.selected_clip = self.timeline.selected_window = None
        self.timeline.range = None
        self._persist()

    def _undo_edit(self):
        self._restore(self._undo, self._redo)

    def _redo_edit(self):
        self._restore(self._redo, self._undo)

    def _start_job(self, label, task, finished):
        if self._job is not None or self._take is not None:
            return
        self._stop_preview()
        cancel = threading.Event()
        self._job = cancel, finished
        self._message(label)
        self._refresh()

        def work():
            try:
                result = task(cancel.is_set), None
            except Exception as exc:
                result = None, exc
            try:
                self._job_finished.emit(result)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="editor-audio", daemon=True).start()

    def _done(self, result):
        value, error = result
        cancel, finished = self._job
        self._job = None
        if isinstance(error, RenderCancelled):
            self._message("Операция отменена.")
        elif error is not None:
            self._message(str(error) or type(error).__name__)
        else:
            finished(value, cancel.is_set())
        self._refresh()
        self.idle.emit()

    def _import_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Импорт аудио", "", AUDIO_FILTER)
        self.import_paths(paths)

    def import_paths(self, paths):
        if not paths or not self.timeline.editable:
            return
        track, start = self._track(), self.timeline.cursor
        folder = data_dir() / "editor_audio"

        def task(cancelled):
            clips, errors = [], []
            for path in paths:
                if cancelled():
                    break
                try:
                    clips.append(import_clip(path, folder, start))
                except Exception as exc:
                    errors.append(f"{Path(path).name} — {exc}")
            return clips, errors

        def finished(value, cancelled):
            clips, errors = value
            if cancelled:
                for clip in clips:
                    Path(clip.path).unlink(missing_ok=True)
                self._message("Импорт отменён.")
                return
            if clips:
                self._before_edit()
                for clip in clips:
                    target = track if len(paths) == 1 and track is not None else self._new_track(clip.name)
                    target.clips.append(clip)
                    self._select_clip(target, clip)
                self._persist()
            message = f"Импортировано файлов: {len(clips)}."
            if errors:
                message += " Не открылись: " + " · ".join(errors)
            self._message(message)

        self._start_job("Импорт аудио…", task, finished)

    def _new_track(self, name):
        track = Track(name)
        self.project.tracks.append(track)
        self.timeline.selected_track = track.id
        self.timeline.selected_clip = self.timeline.selected_window = None
        self.timeline.range = None
        return track

    def _add_track(self):
        self._before_edit()
        self._new_track(f"Дорожка {len(self.project.tracks) + 1}")
        self._persist()
        self.timeline.verticalScrollBar().setValue(self.timeline.verticalScrollBar().maximum())

    def _select_clip(self, track, clip):
        self.timeline.selected_track, self.timeline.selected_clip = track.id, clip.id
        self.timeline.selected_window = None
        self.timeline.range = None

    def _play(self):
        if not self.play_button.isEnabled():
            return
        if self.ctx.engine.preview_hardware_output() is None:
            self._message("Включите звуковой выход во вкладке «Устройства».")
            return
        start = self.timeline.cursor if self.timeline.cursor < self.project.end else 0
        end = self.project.end
        if self.timeline.range:
            start, end = self.timeline.range
        snapshot = EditorProject.from_dict(self.project.to_dict())

        def finished(audio, cancelled):
            if cancelled:
                self._message("Прослушивание отменено.")
                return
            output = self.ctx.engine.preview_hardware_output()
            if output is None:
                self._message("Звуковой выход отключён.")
                return
            self._preview_start = start
            self._voice = self.ctx.engine.preview(audio, 0, len(audio), output_id=output)
            self.timeline.playhead = start
            self._message("Воспроизведение")

        self._start_job("Подготовка прослушивания…",
                        lambda cancelled: preview_audio(snapshot, start, end, cancelled), finished)

    def _toggle_play(self):
        if self._voice is not None or self._job is not None:
            self._stop()
        else:
            self._play()

    def _stop_preview(self):
        if self._voice is not None:
            self.ctx.engine.stop_preview(self._voice)
            self._voice = None
            self.timeline.playhead = None

    def _stop(self):
        if self._job is not None:
            self._job[0].set()
            self._message("Отмена…")
        elif self._voice is not None:
            self._stop_preview()
            self._message("Воспроизведение остановлено.")
        self._refresh()

    def _export(self):
        formats = export_formats()
        filters = {f"{label} (*.{suffix})": suffix for suffix, label in formats.items()}
        path, selected = QFileDialog.getSaveFileName(self, "Экспорт аудио", "Мяукало", ";;".join(filters))
        if not path:
            return
        target = Path(path)
        suffix = target.suffix.lower().lstrip(".")
        if suffix not in formats:
            suffix = filters.get(selected, next(iter(formats)))
            target = target.with_name(f"{target.name}.{suffix}")
            if target.exists() and QMessageBox.question(
                    self, "Заменить файл?", f"Файл «{target.name}» уже есть. Заменить его?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        snapshot = EditorProject.from_dict(self.project.to_dict())
        self._start_job(f"Экспорт в {formats[suffix]}…", lambda cancelled: export_audio(snapshot, target, cancelled),
                        lambda _result, _cancelled: self._message(f"Сохранено: {target}"))

    def _record(self):
        if self._take is not None:
            self.finish_recording()
            return
        ident = self.sources.currentData()
        if not ident:
            return
        self._stop_preview()
        track = self._track()
        self._take = track, self.timeline.cursor, self.sources.currentText()
        try:
            self.ctx.engine.start_editor_recording(ident, data_dir() / "editor_audio", self.ctx.bridge.editor_recorded.emit)
        except (ValueError, OSError) as exc:
            self._take = None
            self._message(str(exc))
        else:
            self.record_status.setText("0:00.00")
            self._message(f"Запись: {self.sources.currentText()}")
        self._refresh()

    def finish_recording(self):
        if self._take is None:
            return False
        if not self._finalizing:
            self._finalizing = True
            self.ctx.engine.stop_editor_recording()
            self.record_status.setText("Сохранение…")
            self._refresh()
        return True

    def finish_work(self):
        if self.finish_recording():
            return True
        if self._job is not None:
            self._stop()
            return True
        return False

    def _recorded(self, ev):
        if self._take is None:
            return
        track, start, source = self._take
        self._take = None
        self._finalizing = False
        self.timeline.take = None
        self.timeline.update_layout()
        self.record_status.clear()
        self.meter.set_level(0.0)
        if ev.get("kind") == "error":
            self._message(f"Запись: {ev.get('error')}")
        elif not ev.get("seconds"):
            Path(ev["path"]).unlink(missing_ok=True)
            self._message("Запись пуста.")
        else:
            self._before_edit()
            if track is None:
                track = self._new_track(source.split(" · ", 1)[-1])
            count = round(ev["seconds"] * SR)
            clip = Clip(ev["path"], ev["name"], start, 0, count, count, count)
            track.clips.append(clip)
            self._select_clip(track, clip)
            self._persist()
            self._message(f"Запись добавлена · {fmt_time(count / SR)}")
        self.refresh_sources()
        self._refresh()
        self.idle.emit()

    def _tick(self):
        if self._take is not None and not self._finalizing:
            seconds, level = self.ctx.engine.editor_recording_state()
            track, start, _ = self._take
            end = start + round(seconds * SR)
            self.timeline.take = (track.id, start, end) if track else None
            self.record_status.setText(fmt_time(seconds))
            self.meter.set_level(level)
            if self.isVisible() and track is not None:
                self.timeline.update_layout()
                self.timeline.follow(end)
        if self._voice is not None:
            position = self.ctx.engine.preview_pos(self._voice)
            if position is None:
                self._stop_preview()
                self._message("Воспроизведение завершено.")
            else:
                self.timeline.playhead = self._preview_start + position
                self.timeline.follow(self.timeline.playhead)
            self._refresh()

    def _track_menu(self, parent=None):
        menu = QMenu(parent)
        menu.addAction("Переименовать…", lambda: self._track_action("name"))
        menu.addAction("Заглушить / включить", lambda: self._track_action("mute"))
        menu.addAction("Соло / выключить соло", lambda: self._track_action("solo"))
        menu.addAction("Эффекты дорожки…", self._track_effects)
        menu.addSeparator()
        menu.addAction("Удалить дорожку", lambda: self._track_action("delete"))
        return menu

    def _track_action(self, action):
        track = self._track()
        if track is None or not self.timeline.editable:
            return
        if action == "name":
            name, ok = QInputDialog.getText(self, "Название дорожки", "Название", text=track.name)
            if not ok or not name.strip() or name.strip() == track.name:
                return
        self._before_edit()
        if action == "name":
            track.name = name.strip()
        elif action == "mute":
            track.muted = not track.muted
        elif action == "solo":
            track.solo = not track.solo
        elif action == "delete":
            self.project.tracks.remove(track)
        self._persist()

    def _clip_menu(self, parent=None):
        menu = QMenu(parent)
        for label, action in (
            ("Разрезать по курсору (Ctrl+K)", "split"), ("Обрезать до выделения (Ctrl+T)", "trim"),
            ("Дублировать (Ctrl+D)", "duplicate"), ("Реверс", "reverse"),
            ("Изменить длительность…", "stretch"), ("Переместить…", "move"),
            ("Поменять местами со следующим", "swap"), ("Удалить (Delete)", "delete"),
        ):
            menu.addAction(label, lambda action=action: self._clip_action(action))
        return menu

    def _clip_action(self, action):
        track = self._track()
        clip = self._clip(track)
        if clip is None or not self.timeline.editable:
            return
        selection = None if action in ("split", "swap") else self.timeline.range
        a, b = selection or (clip.start, clip.end)
        a, b = max(clip.start, a), min(clip.end, b)
        if a >= b:
            self._message("Выделите участок внутри выбранного клипа.")
            return
        if action == "trim" and (a, b) == (clip.start, clip.end):
            self._message("Выделите участок клипа с зажатым Shift.")
            return
        cursor = self.timeline.cursor
        if action == "split" and not clip.start < cursor < clip.end:
            self._message("Поставьте курсор внутрь клипа.")
            return
        part = clip.crop(a, b)
        if action == "stretch":
            shortest, longest = part.stretch_limits
            length, ok = QInputDialog.getDouble(self, "Длительность", "Новая длительность, с",
                                                 part.length / SR, shortest / SR, longest / SR, 5)
            if not ok:
                return
            length = max(1, round(length * SR))
            if length == part.length:
                return
        elif action == "move":
            result = self._move_dialog(track, a)
            if result is None:
                return
            target, start = result
            if target is track and start == a:
                return
        elif action == "swap":
            ordered = sorted(track.clips, key=lambda c: c.start)
            index = ordered.index(clip)
            if index == len(ordered) - 1:
                self._message("На дорожке нет следующего клипа.")
                return
            neighbor = ordered[index + 1]

        self._before_edit()
        if action == "split":
            right = replace(clip.crop(cursor, clip.end), id=new_id("clip"))
            i = track.clips.index(clip)
            track.clips[i:i + 1] = [clip.crop(clip.start, cursor), right]
            self._select_clip(track, right)
        elif action == "trim":
            track.clips[track.clips.index(clip)] = part
            self._select_clip(track, part)
        elif action == "duplicate":
            dup = replace(part, id=new_id("clip"), start=b)
            index = track.clips.index(clip)
            track.clips[index:index + 1] = [clip.crop(clip.start, b), dup]
            if b < clip.end:
                tail = replace(clip.crop(b, clip.end), id=new_id("clip"), start=dup.end)
                track.clips.insert(index + 2, tail)
            self._select_clip(track, dup)
        elif action == "swap":
            # Preserve the gap (or overlap) between clips of unequal lengths.
            gap = neighbor.start - clip.end
            neighbor.start, clip.start = clip.start, max(0, clip.start + neighbor.length + gap)
            self._select_clip(track, clip)
        else:
            remaining = []
            if a > clip.start:
                remaining.append(replace(clip.crop(clip.start, a), id=new_id("clip")))
            if b < clip.end:
                tail_start = a + length if action == "stretch" else b
                remaining.append(replace(clip.crop(b, clip.end), id=new_id("clip"), start=tail_start))
            i = track.clips.index(clip)
            track.clips[i:i + 1] = remaining
            if action in ("reverse", "stretch"):
                changed = part.flipped() if action == "reverse" else part.stretched(length)
                track.clips.insert(i + int(a > clip.start), changed)
                self._select_clip(track, changed)
            elif action == "move":
                moved = replace(part, start=start)
                target.clips.append(moved)
                self._select_clip(target, moved)
            else:
                self.timeline.selected_clip = None
                self.timeline.range = None
        self._persist()

    def _move_dialog(self, track, frame):
        dialog = QDialog(self)
        dialog.setWindowTitle("Переместить клип")
        form = QFormLayout(dialog)
        targets = QComboBox()
        for t in self.project.tracks:
            targets.addItem(t.name)
        targets.setCurrentIndex(self.project.tracks.index(track))
        time = time_field(frame, max(self.project.end * 2, SR * 24 * 3600))
        form.addRow("Дорожка", targets)
        form.addRow("Позиция", time)
        buttons = QDialogButtonBox()
        buttons.addButton("Переместить", QDialogButtonBox.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        result = (self.project.tracks[targets.currentIndex()], round(time.value() * SR)) if dialog.exec() else None
        dialog.deleteLater()
        return result

    def _timeline_edit(self, event):
        action, track, *args = event
        if not self.timeline.editable:
            return
        if action == "point":
            frame, db, previous = args
            if frame == previous and dict(track.points).get(frame) == db:
                return
            self._before_edit()
            track.set_point(frame, db, previous)
        else:
            item, a, b, dest = args
            self._before_edit()
            if action == "move_clip":
                index = track.clips.index(item)
                item.start = a
                if dest is not track:
                    track.clips.pop(index)
                    dest.clips.append(item)
                self._select_clip(dest, item)
            elif action.endswith("stretch_clip"):
                stretched = replace(item.stretched(b - a), start=a)
                track.clips[track.clips.index(item)] = stretched
                self._select_clip(track, stretched)
            elif action.endswith("_clip"):
                track.clips[track.clips.index(item)] = item.crop(a, b)
            else:
                item.start, item.end = a, b
        self._persist()

    def _track_effects(self):
        track = self._track()
        if track is None or not self.timeline.editable:
            return
        dialog = EditorEffectsDialog(f"Эффекты · {track.name}", track.effects, self)
        if dialog.exec() and dialog.effects != track.effects:
            self._before_edit()
            track.effects = dialog.effects
            self._persist()
        dialog.deleteLater()

    def _add_window(self):
        track = self._track()
        if track is None or not self.timeline.editable:
            return
        clip = self._clip(track)
        bounds = self.timeline.range or ((clip.start, clip.end) if clip else None)
        if bounds:
            self._window_dialog(track, None, bounds)

    def _edit_window(self):
        track = self._track()
        window = self._window(track)
        if window is not None:
            self._window_dialog(track, window, (window.start, window.end))

    def _window_dialog(self, track, window, bounds):
        dialog = EditorEffectsDialog(f"Окно эффектов · {track.name}", window.effects if window else [], self, bounds)
        if dialog.exec() and (window is None or dialog.bounds != bounds or dialog.effects != window.effects):
            self._before_edit()
            if window is None:
                window = EffectWindow(*dialog.bounds, dialog.effects)
                track.windows.append(window)
            else:
                window.start, window.end = dialog.bounds
                window.effects = dialog.effects
            self.timeline.selected_window, self.timeline.selected_clip = window.id, None
            self.timeline.range = None
            self._persist()
        dialog.deleteLater()

    def _delete_selection(self):
        if not self.timeline.editable:
            return
        track = self._track()
        window = self._window(track)
        if window is not None:
            self._before_edit()
            track.windows.remove(window)
            self.timeline.selected_window = None
            self._persist()
        else:
            self._clip_action("delete")

    def _move_window(self, delta):
        track = self._track()
        window = self._window(track)
        if window is not None:
            i = track.windows.index(window)
            j = i + delta
            if 0 <= j < len(track.windows):
                self._before_edit()
                track.windows[i], track.windows[j] = track.windows[j], track.windows[i]
                self._persist()

    def _context_menu(self, data):
        pos, kind, item = data
        track = self._track()
        if kind == "clip":
            menu = self._clip_menu(self)
        elif kind == "point" and item is not None:
            menu = QMenu(self)
            menu.addAction("Уровень точки…", lambda: self._point_level(track, item))
            menu.addAction("Удалить точку", lambda: self._remove_point(track, item))
        elif kind == "window":
            menu = QMenu(self)
            menu.addAction("Настроить окно…", self._edit_window)
            menu.addAction("Поднять окно в цепочке", lambda: self._move_window(-1))
            menu.addAction("Опустить окно в цепочке", lambda: self._move_window(1))
            menu.addAction("Удалить окно", self._delete_selection)
        else:
            menu = self._track_menu(self)
            if self.timeline.range:
                menu.addAction("+ Окно эффекта", self._add_window)
        menu.exec(pos)
        menu.deleteLater()

    def _point_level(self, track, frame):
        current = dict(track.points)[frame]
        db, ok = QInputDialog.getDouble(self, "Громкость", "Уровень, дБ", current, DB_MIN, DB_MAX, 1)
        if ok and db != current:
            self._before_edit()
            track.set_point(frame, db)
            self._persist()

    def _remove_point(self, track, frame):
        self._before_edit()
        track.points = [(t, v) for t, v in track.points if t != frame]
        self._persist()
