"""Шкала времени, клипы, автоматизация громкости и окна эффектов."""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractScrollArea

from app.audio.decoder import SR
from app.audio.editor import Clip, Track
from app.audio.effects import effect_title
from . import theme
from .widgets import fmt_time, ui_font

HEADER = 165
RULER = 30
CLIPS = 64
VOLUME = 48
LANE = 23
DB_MIN, DB_MAX = -60.0, 12.0


@dataclass
class Gesture:
    kind: str
    origin: int
    frame: int
    track: object = None
    item: object = None
    db: float = 0.0
    dest: object = None


@dataclass
class TrackRow:
    track: Track
    top: int
    lanes: list[list[Clip]]
    take_lane: int | None = None

    @property
    def audio_height(self):
        return CLIPS * len(self.lanes)

    @property
    def height(self):
        return self.audio_height + VOLUME + max(1, len(self.track.windows)) * LANE


class EditorTimeline(QAbstractScrollArea):
    selection_changed = Signal()
    edited = Signal(object)
    menu_requested = Signal(object)
    window_opened = Signal()
    cursor_drag_started = Signal()

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.waveforms = {}
        self.selected_track = self.selected_clip = self.selected_window = None
        self.cursor = 0
        self.range = None
        self.playhead = None
        self.take = None
        self._rows = []
        self.pixels_per_second = 100.0
        self.gesture = None
        self.editable = True
        self.snap = False
        self.setFrameShape(self.Shape.NoFrame)
        self.viewport().setMouseTracking(True)
        self.horizontalScrollBar().valueChanged.connect(self.viewport().update)
        self.verticalScrollBar().valueChanged.connect(self.viewport().update)
        self.update_layout()

    def rows(self):
        offset = RULER - self.verticalScrollBar().value()
        return [(row, row.top + offset) for row in self._rows]

    def _layout_tracks(self):
        top = 0
        self._rows.clear()
        for track in self.project.tracks:
            intervals = [(clip.start, clip.end, clip) for clip in track.clips]
            if self.take and self.take[0] == track.id:
                _, start, end = self.take
                intervals.append((start, max(end, start + 1), None))
            lane_ends = [0]
            row = TrackRow(track, top, [[]])
            for start, end, clip in sorted(intervals, key=lambda item: item[0]):
                lane = next((i for i, boundary in enumerate(lane_ends) if boundary <= start), len(lane_ends))
                if lane == len(lane_ends):
                    lane_ends.append(0)
                    row.lanes.append([])
                lane_ends[lane] = end
                if clip is None:
                    row.take_lane = lane
                else:
                    row.lanes[lane].append(clip)
            self._rows.append(row)
            top += row.height
        return top

    def update_layout(self):
        width = max(1, self.viewport().width() - HEADER)
        end = max(self.project.end, self.cursor, self.range[1] if self.range else 0)
        if self.take:
            end = max(end, self.take[2])
        duration = max(10.0, end / SR + 3.0)
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        hbar.setRange(0, max(0, int(duration * self.pixels_per_second) - width))
        hbar.setPageStep(width)
        hbar.setSingleStep(max(10, int(self.pixels_per_second / 4)))
        height = self._layout_tracks()
        vbar.setRange(0, max(0, height - (self.viewport().height() - RULER)))
        vbar.setPageStep(max(1, self.viewport().height() - RULER))
        vbar.setSingleStep(LANE)
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_layout()

    def zoom(self, factor=None, anchor=None):
        if factor is None:
            width = max(1, self.viewport().width() - HEADER - 16)
            self.pixels_per_second = max(0.2, min(1800, width * SR / max(SR, self.project.end)))
            self.update_layout()
            self.horizontalScrollBar().setValue(0)
        else:
            x = anchor if anchor is not None else max(HEADER, min(self.viewport().width(), self.x(self.cursor)))
            frame = self.frame(x)
            self.pixels_per_second = max(0.2, min(1800, self.pixels_per_second * factor))
            self.update_layout()
            self.horizontalScrollBar().setValue(round(frame * self.pixels_per_second / SR - (x - HEADER)))

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.zoom(1.35 ** (event.angleDelta().y() / 120), event.position().x())
            event.accept()
        elif event.modifiers() & Qt.ShiftModifier:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - event.angleDelta().y())
            event.accept()
        else:
            super().wheelEvent(event)

    def x(self, frame):
        return HEADER + frame * self.pixels_per_second / SR - self.horizontalScrollBar().value()

    def frame(self, x, snap=False):
        value = max(0, round((x - HEADER + self.horizontalScrollBar().value()) * SR / self.pixels_per_second))
        if snap and self.snap:
            step = SR // 10
            return round(value / step) * step
        return value

    def follow(self, frame):
        x = self.x(frame)
        if x < HEADER or x >= self.viewport().width() - 10:
            self.horizontalScrollBar().setValue(max(0, int(frame * self.pixels_per_second / SR) - 30))
        self.viewport().update()

    @staticmethod
    def _db_y(db, volume_top):
        return volume_top + 4 + (DB_MAX - db) * (VOLUME - 8) / (DB_MAX - DB_MIN)

    @staticmethod
    def _y_db(y, volume_top):
        db = DB_MAX - (y - volume_top - 4) * (DB_MAX - DB_MIN) / (VOLUME - 8)
        return round(max(DB_MIN, min(DB_MAX, db)), 1)

    def _positions(self, width):
        step = next((step for step in (.05, .1, .2, .5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200)
                     if step * self.pixels_per_second >= 78), 1800)
        start = math.ceil(self.frame(HEADER) / (step * SR))
        stop = int(self.frame(width) // (step * SR))
        return [(round(n * step * SR), self.x(round(n * step * SR))) for n in range(start, stop + 1)]

    def paintEvent(self, event):
        p = QPainter(self.viewport())
        width, height = self.viewport().width(), self.viewport().height()
        p.fillRect(0, 0, width, height, QColor(theme.BG))
        p.setFont(ui_font(9))
        ticks = self._positions(width)
        p.save()
        p.setClipRect(0, RULER, width, height - RULER)
        rows = self.rows()
        for index, (row, top) in enumerate(rows):
            if top + row.height <= RULER or top >= height:
                continue
            self._paint_track(p, index, row, top, width, ticks)
        if self.range:
            a, b = [round(self.x(f)) for f in self.range]
            selected = next(((top, row.height) for row, top in rows if row.track.id == self.selected_track), None)
            if selected:
                top, h = selected
                left, right = max(a, HEADER), min(b, width)
                if right > left:
                    color = QColor(theme.ACCENT2)
                    color.setAlpha(38)
                    p.fillRect(left, top, right - left, h, color)
        p.restore()
        p.fillRect(0, 0, width, RULER, QColor(theme.SURFACE2))
        p.setPen(QColor(theme.MUTED))
        p.drawText(9, 19, "ДОРОЖКИ")
        for frame, x in ticks:
            xi = round(x)
            p.drawLine(xi, RULER - 6, xi, RULER - 1)
            p.drawText(xi + 4, 18, fmt_time(frame / SR))
        p.setPen(QColor(theme.BORDER))
        p.drawLine(0, RULER - 1, width, RULER - 1)
        p.drawLine(HEADER - 1, 0, HEADER - 1, height)
        x = round(self._cursor_x())
        if HEADER <= x < width:
            p.setPen(QPen(QColor(theme.ACCENT2), 1))
            p.drawLine(x, 0, x, height)
            p.setBrush(QColor(theme.ACCENT2))
            p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 10)]))
        if not self.project.tracks:
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRect(14, 45, width - 28, 70), Qt.AlignCenter | Qt.TextWordWrap,
                       "Перетащите аудио сюда или нажмите «Импорт аудио».")

    def _paint_track(self, p, index, row, top, width, ticks):
        track = row.track
        height = row.height
        volume_top = top + row.audio_height
        effects_top = volume_top + VOLUME
        color = QColor(theme.PAD_COLORS[index % len(theme.PAD_COLORS)])
        selected = track.id == self.selected_track
        p.fillRect(0, top, HEADER, height, QColor(theme.SURFACE3 if selected else theme.SURFACE))
        p.fillRect(0, top, 3, row.audio_height, color)
        p.setPen(color)
        states = (" · СОЛО" if track.solo else "") + (" · ТИХО" if track.muted else "")
        text = p.fontMetrics().elidedText(track.name + states, Qt.ElideRight, HEADER - 18)
        p.drawText(10, top + 23, text)
        p.setPen(QColor(theme.MUTED))
        count = sum(s.enabled for s in track.effects)
        p.drawText(10, top + 44, f"Эффекты: {count}" if count else "Аудио")
        p.drawText(10, volume_top + 27, "Громкость, дБ")
        p.drawText(10, effects_top + 16, "Окна эффектов")
        p.save()
        p.setClipRect(HEADER, RULER, max(0, width - HEADER), self.viewport().height() - RULER)
        p.fillRect(HEADER, top, width - HEADER, row.audio_height, QColor(theme.SURFACE if selected else theme.BG))
        p.fillRect(HEADER, volume_top, width - HEADER, VOLUME, QColor(theme.SURFACE2))
        p.fillRect(HEADER, effects_top, width - HEADER, top + height - effects_top, QColor(theme.SURFACE))
        p.setPen(QColor(theme.SURFACE3))
        for _, x in ticks:
            p.drawLine(round(x), top, round(x), top + height)
        g = self.gesture
        for lane, clips in enumerate(row.lanes):
            lane_top = top + lane * CLIPS
            if lane:
                p.setPen(QColor(theme.BORDER))
                p.drawLine(HEADER, lane_top, width, lane_top)
            for clip in clips:
                if g is None or g.item is not clip:
                    self._paint_clip(p, clip, clip.start, clip.end, lane_top, color)
            if row.take_lane == lane:
                self._box(p, self.take[1], self.take[2], lane_top + 7, CLIPS - 14,
                          "Запись", QColor(theme.DANGER), True)
        if g is not None and g.kind.endswith("_clip") and g.dest is track:
            clip = g.item
            a, b = self._bounds(g)
            lane = next((i for i, clips in enumerate(row.lanes)
                         if all(other is clip or b <= other.start or a >= other.end for other in clips)), None)
            if lane is None:
                lane = next((i for i, clips in enumerate(row.lanes) if clip in clips), len(row.lanes) - 1)
            if g.kind.endswith("stretch_clip") and (a, b) != (clip.start, clip.end):
                clip = clip.stretched(b - a)
            elif g.kind in ("left_clip", "right_clip"):
                clip = clip.crop(a, b)
            self._paint_clip(p, clip, a, b, top + lane * CLIPS, color)
        self._paint_volume(p, track, volume_top, width)
        for index, window in enumerate(track.windows):
            a, b = window.start, window.end
            if g is not None and g.item is window:
                a, b = self._bounds(g)
            name = " + ".join(effect_title(s.type) for s in window.effects if s.enabled) or "Окно эффекта"
            tint = QColor(theme.PAD_COLORS[(8 + index) % len(theme.PAD_COLORS)])
            self._box(p, a, b, effects_top + index * LANE + 2, LANE - 4, name, tint,
                      window.id == self.selected_window)
        p.restore()
        p.setPen(QColor(theme.BORDER))
        p.drawLine(0, top + height - 1, width, top + height - 1)

    def _box(self, p, start, end, y, h, text, color, selected):
        x, right = round(self.x(start)), round(self.x(end))
        if right < HEADER or x >= self.viewport().width():
            return
        left = max(HEADER - 1, x)
        rect = QRect(left, y, max(2, min(right, self.viewport().width() + 1) - left), h)
        fill = QColor(color)
        fill.setAlpha(62 if selected else 32)
        p.fillRect(rect, fill)
        p.setPen(QPen(color, 2 if selected else 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect.adjusted(0, 0, -1, -1))
        text = p.fontMetrics().elidedText(text, Qt.ElideRight, max(0, rect.width() - 10))
        p.drawText(rect.left() + 5, y + 15, text)

    def _paint_clip(self, p, clip, start, end, top, color):
        name = ("↶ " if clip.reverse else "") + clip.name
        if clip.speed != 1:
            name += f" · {clip.speed:.2f}x"
        data = self.waveforms.get(clip.path)
        if isinstance(data, str):
            name = "! " + name
        self._box(p, start, end, top + 5, CLIPS - 10, name, color, clip.id == self.selected_clip)
        if clip.id == self.selected_clip and self.x(end) - self.x(start) >= 14:
            p.setPen(QPen(color, 3))
            for frame in (start, end):
                x = round(self.x(frame))
                if HEADER <= x < self.viewport().width():
                    p.drawLine(x, top + 24, x, top + 42)
        if data is None or isinstance(data, str):
            return
        left = max(HEADER + 1, math.ceil(self.x(start)) + 1)
        right = min(self.viewport().width(), math.floor(self.x(end)))
        if right <= left:
            return
        total, peaks = data
        if not len(peaks):
            return
        stride = max(1, (total + 4095) // 4096)
        source_scale = (clip.source_out - clip.source_in) / clip.span
        middle, amplitude = top + 40, 16 / 32768.0
        p.setPen(color)
        for x in range(left, right):
            offset = clip.offset + (x - self.x(start)) * (end - start) / max(1, self.x(end) - self.x(start))
            next_offset = offset + SR / self.pixels_per_second
            if clip.reverse:
                offset, next_offset = clip.span - next_offset, clip.span - offset
            a = max(0, min(len(peaks) - 1, int((clip.source_in + offset * source_scale) // stride)))
            b = max(a + 1, min(len(peaks), 1 + int((clip.source_in + next_offset * source_scale) // stride)))
            lo, hi = peaks[a:b, 0].min(), peaks[a:b, 1].max()
            p.drawLine(x, round(middle - hi * amplitude), x, round(middle - lo * amplitude))

    def _paint_volume(self, p, track, volume_top, width):
        points = track.points
        g = self.gesture
        if g is not None and g.kind == "point" and g.track is track:
            points = sorted([(t, v) for t, v in points if t not in (g.item, g.frame)] + [(g.frame, g.db)])
        line = points or [(0, 0.0)]
        if line[0][0] > 0:
            line = [(0, 0.0), *line]
        pen = QColor(theme.ACCENT2 if track.id == self.selected_track else theme.MUTED)
        p.setPen(pen)
        path = [QPointF(self.x(t), self._db_y(v, volume_top)) for t, v in line]
        path.append(QPointF(max(width, path[-1].x()), path[-1].y()))
        p.drawPolyline(QPolygonF(path))
        p.setBrush(pen)
        for t, v in points:
            x = self.x(t)
            if HEADER - 5 <= x <= width + 5:
                p.drawEllipse(QPointF(x, self._db_y(v, volume_top)), 3.5, 3.5)

    def _row(self, y):
        if y >= RULER:
            for row, top in self.rows():
                if top <= y < top + row.height:
                    return row, top
        return None, 0

    def _hit(self, x, y, row, top):
        if row is None or x < HEADER:
            return None, None
        volume_top = top + row.audio_height
        effects_top = volume_top + VOLUME
        if y < volume_top:
            clips = row.lanes[int((y - top) // CLIPS)]
            order = sorted(reversed(clips), key=lambda c: c.id != self.selected_clip)
            for clip in order:
                if self.x(clip.start) - 4 <= x <= self.x(clip.end) + 4:
                    return "clip", clip
        elif y < effects_top:
            point = next((t for t, v in row.track.points if abs(self.x(t) - x) <= 7
                          and abs(self._db_y(v, volume_top) - y) <= 8), None)
            return "point", point
        else:
            i = int((y - effects_top) // LANE)
            if i < len(row.track.windows):
                window = row.track.windows[i]
                if self.x(window.start) - 4 <= x <= self.x(window.end) + 4:
                    return "window", window
        return None, None

    def _handle(self, x, item):
        left, right = self.x(item.start), self.x(item.end)
        if right - left < 14:
            return "move"
        if abs(x - left) <= 5:
            return "left"
        if abs(x - right) <= 5:
            return "right"
        return "move"

    def _bounds(self, g):
        a, b = g.item.start, g.item.end
        delta = g.frame - g.origin
        if g.kind.startswith("move_"):
            start = max(0, a + delta)
            return start, start + b - a
        if g.kind.endswith("stretch_clip"):
            shortest, longest = g.item.stretch_limits
            if g.kind.startswith("left_"):
                length = max(shortest, min(longest, b - a - delta))
                return max(0, b - length), b
            length = max(shortest, min(longest, b - a + delta))
            return a, a + length
        limit = 0
        upper = float("inf")
        if g.kind.endswith("_clip"):
            limit = max(0, a - g.item.offset)
            upper = a + g.item.span - g.item.offset
        if g.kind.startswith("left_"):
            return min(b - 1, max(limit, a + delta)), b
        return a, max(a + 1, min(upper, b + delta))

    def _cursor_x(self):
        return self.x(self.playhead if self.playhead is not None else self.cursor)

    def _cursor_hit(self, x, y, kind):
        return x >= HEADER and (y < RULER or (kind is None and abs(self._cursor_x() - x) <= 5))

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self.editable:
            return
        self.setFocus(Qt.MouseFocusReason)
        x, y = event.position().x(), event.position().y()
        row, top = self._row(y)
        kind, item = self._hit(x, y, row, top)
        frame = self.frame(x, snap=True)
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        if (not shift or y < RULER) and self._cursor_hit(x, y, kind):
            self.cursor_drag_started.emit()
            self.range = None
            self.cursor = frame
            self.gesture = Gesture("cursor", frame, frame)
            self.selection_changed.emit()
            return
        track = row.track if row else None
        if track is not None:
            if track.id != self.selected_track:
                self.range = None
                self.selected_clip = self.selected_window = None
            self.selected_track = track.id
        if x >= HEADER:
            self.cursor = frame
            if track is not None:
                handle = self._handle(x, item) if kind in ("clip", "window") else None
                stretch = shift and kind == "clip" and handle != "move"
                if (shift and not stretch) or kind is None:
                    self.range = None
                    if kind == "clip":
                        self.selected_clip, self.selected_window = item.id, None
                    elif not shift:
                        self.selected_clip = self.selected_window = None
                    self.gesture = Gesture("range", frame, frame, track)
                elif kind == "point":
                    self.gesture = Gesture(kind, frame, frame if item is None else item, track, item,
                                           self._y_db(y, top + row.audio_height) if item is None
                                           else dict(track.points)[item])
                else:
                    self.range = None
                    self.selected_clip = item.id if kind == "clip" else None
                    self.selected_window = item.id if kind == "window" else None
                    action = f"{handle}_stretch_clip" if stretch else f"{handle}_{kind}"
                    self.gesture = Gesture(action, frame, frame, track, item, dest=track)
        self.selection_changed.emit()
        self.viewport().update()

    def mouseMoveEvent(self, event):
        x, y = event.position().x(), event.position().y()
        g = self.gesture
        if g is None:
            row, top = self._row(y)
            kind, item = self._hit(x, y, row, top)
            handle = self._handle(x, item) if kind in ("clip", "window") else None
            cursor = (Qt.SizeHorCursor if self._cursor_hit(x, y, kind) or handle in ("left", "right")
                      else Qt.OpenHandCursor if handle
                      else Qt.CrossCursor if row is not None and x >= HEADER else Qt.ArrowCursor)
            self.viewport().setCursor(cursor)
            return
        g.frame = self.frame(x, snap=True)
        if g.kind == "cursor":
            self.cursor = g.frame
            self.selection_changed.emit()
        elif g.kind == "range":
            a, b = sorted((g.origin, g.frame))
            self.range = (a, b) if a != b else None
            self.selection_changed.emit()
        elif g.kind == "point":
            volume_top = next(top + row.audio_height for row, top in self.rows() if row.track is g.track)
            g.db = self._y_db(y, volume_top)
        elif g.kind == "move_clip":
            row, _ = self._row(y)
            g.dest = row.track if row else g.track
        self.viewport().update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self.gesture is None:
            return
        g, self.gesture = self.gesture, None
        if g.kind == "cursor":
            self.cursor = self.frame(event.position().x(), snap=True)
        elif g.kind == "point":
            self.edited.emit(("point", g.track, g.frame, g.db, g.item))
        elif g.kind != "range":
            a, b = self._bounds(g)
            if a != g.item.start or b != g.item.end or (g.dest is not None and g.dest is not g.track):
                self.edited.emit((g.kind, g.track, g.item, a, b, g.dest))
        self.update_layout()
        self.selection_changed.emit()

    def mouseDoubleClickEvent(self, event):
        if not self.editable or event.button() != Qt.LeftButton:
            return
        x, y = event.position().x(), event.position().y()
        row, top = self._row(y)
        kind, item = self._hit(x, y, row, top)
        self.gesture = None
        if kind == "window":
            self.selected_track, self.selected_window, self.selected_clip = row.track.id, item.id, None
            self.window_opened.emit()

    def contextMenuEvent(self, event):
        if not self.editable:
            return
        x, y = event.pos().x(), event.pos().y()
        row, top = self._row(y)
        if row is None:
            return
        track = row.track
        kind, item = self._hit(x, y, row, top)
        if track.id != self.selected_track:
            self.range = None
        self.selected_track = track.id
        self.selected_clip = item.id if kind == "clip" else None
        self.selected_window = item.id if kind == "window" else None
        self.selection_changed.emit()
        self.menu_requested.emit((event.globalPos(), kind, item))
