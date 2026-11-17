"""Переиспользуемые виджеты: вкладки-папки, кнопка звука, индикатор, волна, захват клавиш."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygon
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (QColorDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget)

from app.audio.decoder import SR, peaks
from app.model import Trigger
from . import theme


def mix(c1: str, c2: str, t: float) -> QColor:
    a, b = QColor(c1), QColor(c2)
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def fmt_time(sec: float) -> str:
    sec = max(0.0, sec)
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:05.2f}"


def ui_font(pt: float, weight=QFont.Normal) -> QFont:
    f = QFont(theme.FONT)
    f.setStyleHint(QFont.Monospace)
    f.setPointSizeF(pt)
    f.setWeight(weight)
    return f


# ===================================================================== вкладки-папки

class _TabStrip(QWidget):
    clicked = Signal(int)
    H = 34
    CUT = 12      # срез угла ярлыка
    LIFT = 5      # насколько неактивные ярлыки «утоплены»

    def __init__(self, parent=None):
        super().__init__(parent)
        self.titles: list[str] = []
        self.labels: list[str] = []
        self.current = 0
        self.hover = -1
        self.setFixedHeight(self.H)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def _rects(self):
        fm = QFontMetrics(ui_font(9.5, QFont.Bold))
        x = 4
        res = []
        widths = [max(105, fm.horizontalAdvance(t) + 47) for t in self.labels]
        scale = min(1.0, max(1, self.width() - 8) / max(1, sum(widths)))
        for width in widths:
            w = max(1, int(width * scale))
            res.append(QRect(x, 0, w, self.H))
            x += w - 1
        return res

    def sizeHint(self):
        return QSize(200, self.H)

    def mousePressEvent(self, e):
        for i, r in enumerate(self._rects()):
            if r.contains(e.position().toPoint()):
                self.clicked.emit(i)
                return

    def mouseMoveEvent(self, e):
        h = next((i for i, r in enumerate(self._rects()) if r.contains(e.position().toPoint())), -1)
        if h != self.hover:
            self.hover = h
            self.setToolTip(self.titles[h] if h >= 0 else "")
            self.update()

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def _shape(self, r: QRect, top: int) -> QPolygon:
        c = self.CUT
        return QPolygon([QPoint(r.left(), r.bottom() + 1), QPoint(r.left(), top),
                         QPoint(r.right() - c, top), QPoint(r.right(), top + c),
                         QPoint(r.right(), r.bottom() + 1)])

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG))
        rects = self._rects()
        base_y = self.H - 2
        order = [i for i in range(len(rects)) if i != self.current] + ([self.current] if rects else [])
        for i in order:
            r = rects[i]
            active = i == self.current
            top = 0 if active else self.LIFT
            poly = self._shape(QRect(r.left(), r.top(), r.width(), base_y), top)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.BG if active else (theme.SURFACE3 if i == self.hover else theme.SURFACE2)))
            p.drawPolygon(poly)
            p.setPen(QPen(QColor(theme.ACCENT if active else theme.DIM), 2 if active else 1))
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(poly)
            # номер раздела — «инвертированный» блок
            num = f"{i + 1:02d}"
            nb = QRect(r.left() + 5, top + 8, 19, base_y - top - 13)
            p.fillRect(nb, QColor(theme.ACCENT if active else theme.DIM))
            p.setFont(ui_font(8, QFont.Bold))
            p.setPen(QColor(theme.INK))
            p.drawText(nb, Qt.AlignCenter, num)
            p.setFont(ui_font(9.5, QFont.Bold))
            p.setPen(QColor(theme.ACCENT2 if active else (theme.TEXT if i == self.hover else theme.MUTED)))
            label = QRect(nb.right() + 5, top, max(0, r.width() - 41), base_y - top)
            p.drawText(label, Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(self.labels[i], Qt.ElideRight, label.width()))
        # базовая линия с разрывом под активным ярлыком
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        y = self.H - 1
        if rects:
            a = rects[self.current]
            p.drawLine(0, y, a.left(), y)
            p.drawLine(a.right(), y, self.width(), y)
        else:
            p.drawLine(0, y, self.width(), y)


class FolderTabs(QWidget):
    """Вкладки в виде ярлыков папок: активная выходит вперёд и сливается с панелью."""
    currentChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.strip = _TabStrip()
        self.strip.clicked.connect(self.setCurrentIndex)
        self.body = QFrame()
        self.body.setObjectName("folderBody")
        self.body.setStyleSheet(f"QFrame#folderBody {{ background: {theme.BG}; border: 2px solid {theme.ACCENT};"
                                f" border-top: none; }}")
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(2, 0, 2, 2)
        self.stack = QStackedWidget()
        self._pages = []
        self._visible = []
        bl.addWidget(self.stack)
        lay.addWidget(self.strip)
        lay.addWidget(self.body, 1)

    def addTab(self, w, title, short_title=None):
        self.stack.addWidget(w)
        self._pages.append((w, title.upper(), (short_title or title).upper()))
        self.setVisibleTabs(self._visible + [w])

    def setVisibleTabs(self, widgets):
        previous = self.currentWidget()
        visible = [(w, title, label) for w, title, label in self._pages if w in widgets]
        self._visible = [w for w, _, _ in visible]
        self.strip.titles = [title for _, title, _ in visible]
        self.strip.labels = [label for _, _, label in visible]
        self.strip.hover = -1
        self.strip.setToolTip("")
        if self._visible:
            current = previous if previous in self._visible else self._visible[0]
            self.stack.setCurrentWidget(current)
            self.strip.current = self._visible.index(current)
            if current is not previous:
                self.currentChanged.emit(self.strip.current)
        self.strip.update()

    def count(self):
        return len(self._visible)

    def currentIndex(self):
        return self.indexOf(self.currentWidget())

    def currentWidget(self):
        return self.stack.currentWidget()

    def indexOf(self, w):
        return self._visible.index(w) if w in self._visible else -1

    def setCurrentIndex(self, i):
        if 0 <= i < len(self._visible) and i != self.currentIndex():
            self.stack.setCurrentWidget(self._visible[i])
            self.strip.current = i
            self.strip.update()
            self.currentChanged.emit(i)

    def setCurrentWidget(self, w):
        self.setCurrentIndex(self.indexOf(w))


# ===================================================================== панель с заголовком

class Panel(QFrame):
    """Блок с «инвертированной» шапкой во всю ширину, как на приборной панели."""

    def __init__(self, title: str, parent=None, margins=(10, 8, 10, 10), right=None):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget()
        head.setObjectName("panelHead")
        head.setAttribute(Qt.WA_StyledBackground, True)
        head.setStyleSheet(f"QWidget#panelHead {{ background: {theme.ACCENT}; }}")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(8, 0, 2, 0)
        hl.setSpacing(4)
        self.title = QLabel(title.upper())
        self.title.setStyleSheet(f"color: {theme.INK}; font-weight: 700; background: transparent;")
        hl.addWidget(self.title, 1)
        self.head_lay = hl
        head.setFixedHeight(24)
        outer.addWidget(head)
        self.inner = QWidget()
        outer.addWidget(self.inner, 1)
        self.body = QVBoxLayout(self.inner)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(8)

    def add_head_button(self, text, slot, tip=""):
        b = QPushButton(text)
        b.setStyleSheet(f"QPushButton {{ background: {theme.INK}; color: {theme.ACCENT}; border: none; padding: 1px 10px;"
                        f" min-height: 18px; font-weight: 700; }} QPushButton:hover {{ color: {theme.ACCENT2}; }}")
        b.setCursor(Qt.PointingHandCursor)
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(slot)
        self.head_lay.addWidget(b)
        return b


def caption(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setObjectName("cap")
    return lab


# ===================================================================== meter

class Meter(QWidget):
    """Сегментный индикатор уровня, как светодиодная линейка."""
    SEG = 4
    GAP = 2

    def __init__(self, vertical=True, parent=None):
        super().__init__(parent)
        self.vertical = vertical
        self.level = 0.0
        self.hold = 0.0
        if vertical:
            self.setFixedWidth(10)
        else:
            self.setFixedHeight(8)

    def set_level(self, v: float):
        v = max(0.0, min(1.2, v))
        db = 20 * np.log10(max(v, 1e-5))
        pos = max(0.0, min(1.0, (db + 48) / 48))
        self.level = pos if pos > self.level else self.level * 0.82 + pos * 0.18
        self.hold = max(self.hold - 0.008, self.level)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor(theme.BG))
        length = r.height() if self.vertical else r.width()
        n = max(1, length // (self.SEG + self.GAP))
        lit = int(round(self.level * n))
        hold = min(n - 1, int(round(self.hold * n)) - 1)
        for i in range(n):
            frac = i / n
            if frac > 0.88:
                on = QColor(theme.DANGER)
            elif frac > 0.7:
                on = QColor(theme.ACCENT2)
            else:
                on = QColor(theme.ACCENT)
            col = on if i < lit or i == hold else QColor(theme.SURFACE3)
            off = i * (self.SEG + self.GAP)
            if self.vertical:
                p.fillRect(QRect(0, r.height() - off - self.SEG, r.width(), self.SEG), col)
            else:
                p.fillRect(QRect(off, 0, self.SEG, r.height()), col)


# ===================================================================== pad button

MODE_SHORT = {"oneshot": "ОДИН РАЗ", "toggle": "ВКЛ/ВЫКЛ", "hold": "УДЕРЖАНИЕ"}


class PadButton(QWidget):
    pressed = Signal(str)
    released = Signal(str)
    edit = Signal(str)
    context = Signal(str, object)

    def __init__(self, pad, channel_name: str, hotkeys: str, parent=None, index: int = 0):
        super().__init__(parent)
        self.pad = pad
        self.index = index
        self.channel_name = channel_name
        self.hotkeys = hotkeys
        self.state = None        # None | True | float progress
        self.status = ""
        self.hover = False
        self.down = False
        self.setMinimumSize(168, 80)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self._tip()

    def _tip(self):
        from app.model import PAD_MODES
        t = f"<b>{self.pad.name}</b><br>{PAD_MODES.get(self.pad.mode, '')}"
        if self.pad.loop:
            t += " · по кругу"
        t += f"<br>Канал: {self.channel_name}<br>Клавиши: {self.hotkeys or 'не назначены'}"
        t += "<br><br>Клик — играть · ⚙ или правая кнопка — настроить"
        self.setToolTip(t)

    def sizeHint(self):
        return QSize(190, 80)

    def set_state(self, state, status=""):
        if state != self.state or status != self.status:
            self.state = state
            self.status = status
            self.update()

    def _edit_rect(self):
        return QRectF(self.width() - 26, 0, 26, 22)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self._edit_rect().contains(e.position()):
                self.edit.emit(self.pad.id)
                return
            self.down = True
            self.update()
            self.pressed.emit(self.pad.id)
        elif e.button() == Qt.RightButton:
            self.context.emit(self.pad.id, e.globalPosition().toPoint())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.down:
            self.down = False
            self.update()
            self.released.emit(self.pad.id)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and not self._edit_rect().contains(e.position()):
            self.mousePressEvent(e)

    def mouseMoveEvent(self, _e):
        self.update()

    def enterEvent(self, _e):
        self.hover = True
        self.update()

    def leaveEvent(self, _e):
        self.hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        W, H = self.width(), self.height()
        playing = self.state is not None
        tag = QColor(self.pad.color)
        bg = QColor(theme.SURFACE3 if (self.down or playing) else (theme.SURFACE2 if self.hover else theme.SURFACE))
        p.fillRect(self.rect(), bg)

        # шапка: номер (цвет звука) + название; во время игры шапка инвертируется
        head_h = 22
        head_bg = QColor(theme.ACCENT) if playing else QColor(theme.BG)
        p.fillRect(QRect(0, 0, W, head_h), head_bg)
        p.fillRect(QRect(0, 0, 30, head_h), tag)
        p.setFont(ui_font(8, QFont.Bold))
        p.setPen(QColor(theme.INK))
        p.drawText(QRect(0, 0, 30, head_h), Qt.AlignCenter, f"{self.index:02d}" if self.index else "··")
        p.setFont(ui_font(9.5, QFont.Bold))
        p.setPen(QColor(theme.INK if playing else theme.TEXT))
        name_r = QRect(36, 0, W - 66, head_h)
        p.drawText(name_r, Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(self.pad.name.upper(), Qt.ElideRight, name_r.width()))
        er = self._edit_rect().toRect()
        hover_edit = self.hover and er.contains(self.mapFromGlobal(self.cursor().pos()))
        if hover_edit:
            p.fillRect(er, QColor(theme.INK if playing else theme.ACCENT))
        p.setFont(ui_font(10, QFont.Bold))
        p.setPen(QColor(theme.ACCENT if hover_edit and playing else
                        (theme.INK if (playing or hover_edit) else theme.MUTED)))
        p.drawText(er, Qt.AlignCenter, "⚙")

        # строка режима
        p.setFont(ui_font(8, QFont.DemiBold))
        p.setPen(QColor(theme.MUTED))
        mode = MODE_SHORT.get(self.pad.mode, "")
        src = ""
        if self.pad.kind == "live":
            src = {"input": "ВХОД", "process": "ПРОГРАММА", "loopback": "ЗАХВАТ"}.get(self.pad.source.type, "ВХОД") + " · "
        line = f"{src}{mode}{' · ↻' if self.pad.loop else ''}  /  {self.channel_name.upper()}"
        p.drawText(QRect(8, head_h + 4, W - 16, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(line, Qt.ElideRight, W - 16))

        # клавиши — как «клавиши-колпачки»
        y = H - 27
        if self.status:
            p.setFont(ui_font(8.5, QFont.Bold))
            p.setPen(QColor(theme.DANGER if self.status.startswith("ошибка") else theme.WARN))
            p.drawText(QRect(8, y, W - 16, 20), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(self.status.upper(), Qt.ElideRight, W - 16))
        elif self.hotkeys:
            p.setFont(ui_font(8.5, QFont.Bold))
            fm = p.fontMetrics()
            x = 8
            for k in self.hotkeys.split(", "):
                k = k.upper()
                w = fm.horizontalAdvance(k) + 12
                if x + w > W - 8:
                    p.setPen(QColor(theme.ACCENT2))
                    p.drawText(QRect(x, y, 20, 20), Qt.AlignVCenter, "…")
                    break
                p.setPen(QPen(QColor(theme.ACCENT2), 1))
                p.setBrush(Qt.NoBrush)
                p.drawRect(QRect(x, y, w, 19))
                p.drawText(QRect(x, y, w, 19), Qt.AlignCenter, k)
                x += w + 5
        else:
            p.setFont(ui_font(8.5))
            p.setPen(QColor(theme.DIM))
            p.drawText(QRect(8, y, W - 16, 20), Qt.AlignLeft | Qt.AlignVCenter, "— НЕТ КЛАВИШИ —")

        # прогресс — сегментная полоса снизу
        if playing:
            bar = QRect(0, H - 5, W, 5)
            p.fillRect(bar, QColor(theme.BG))
            if isinstance(self.state, float):
                fill = int(W * max(0.0, min(1.0, self.state)))
                for x in range(0, fill, 6):
                    p.fillRect(QRect(x, H - 5, min(4, fill - x), 5), QColor(theme.ACCENT))
            else:
                for x in range(0, W, 6):
                    p.fillRect(QRect(x, H - 5, 4, 5), QColor(theme.ACCENT))

        # рамка
        p.setBrush(Qt.NoBrush)
        if playing:
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.drawRect(QRect(1, 1, W - 2, H - 2))
        else:
            p.setPen(QPen(QColor(theme.ACCENT if self.hover else theme.BORDER), 1))
            p.drawRect(QRect(0, 0, W - 1, H - 1))


# ===================================================================== waveform

class WaveformView(QWidget):
    """Волна с двумя маркерами обрезки, зумом колесом и курсором воспроизведения."""
    trimChanged = Signal(float, float)       # секунды
    seekRequested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = None
        self.total = 0
        self.start = 0
        self.end = 0
        self.view0 = 0
        self.view1 = 1
        self.play_pos = None
        self._drag = None
        self._pan_from = None
        self._peaks = None
        self._peaks_key = None
        self.color = theme.ACCENT
        self.setMinimumHeight(140)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setToolTip("Тяните маркеры IN/OUT, чтобы обрезать звук. Колёсико — масштаб, "
                        "правая кнопка — сдвиг. Клик по волне — прослушать с этого места.")

    def set_data(self, data, start_s=0.0, end_s=0.0):
        self.data = data
        self.total = len(data) if data is not None else 0
        self.start = int(start_s * SR)
        self.end = int(end_s * SR) if end_s > 0 else self.total
        self.start = max(0, min(self.total, self.start))
        self.end = max(self.start, min(self.total, self.end))
        self.view0, self.view1 = 0, max(1, self.total)
        self._peaks_key = None
        self.update()

    def set_trim(self, start_s, end_s):
        self.start = max(0, min(self.total, int(start_s * SR)))
        self.end = max(self.start, min(self.total, int(end_s * SR) if end_s > 0 else self.total))
        self.update()

    def zoom_to_selection(self):
        if self.total:
            pad = max(int((self.end - self.start) * 0.08), SR // 20)
            self.view0 = max(0, self.start - pad)
            self.view1 = min(self.total, self.end + pad)
            self._peaks_key = None
            self.update()

    def zoom_all(self):
        self.view0, self.view1 = 0, max(1, self.total)
        self._peaks_key = None
        self.update()

    def _x(self, s):
        return (s - self.view0) / max(1, self.view1 - self.view0) * self.width()

    def _s(self, x):
        return int(self.view0 + x / max(1, self.width()) * (self.view1 - self.view0))

    def paintEvent(self, _e):
        p = QPainter(self)
        r = QRectF(self.rect())
        p.fillRect(r, QColor(theme.BG))
        # сетка, как на анализаторе
        grid = QPen(QColor(theme.BORDER), 1, Qt.DotLine)
        p.setPen(grid)
        for i in range(1, 10):
            x = r.width() * i / 10
            p.drawLine(QPointF(x, 0), QPointF(x, r.height()))
        for i in range(1, 4):
            y = r.height() * i / 4
            p.drawLine(QPointF(0, y), QPointF(r.width(), y))
        p.setPen(QPen(QColor(theme.DIM), 1))
        p.drawLine(QPointF(0, r.height() / 2), QPointF(r.width(), r.height() / 2))
        if self.data is None or self.total == 0:
            p.setFont(ui_font(10, QFont.Bold))
            p.setPen(QColor(theme.MUTED))
            p.drawText(r, Qt.AlignCenter, "НЕТ СИГНАЛА")
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.drawRect(r.adjusted(0, 0, -1, -1))
            return
        w = max(1, self.width())
        key = (self.view0, self.view1, w)
        if key != self._peaks_key:
            seg = self.data[self.view0:self.view1]
            self._peaks = peaks(seg, min(w, max(1, len(seg))))
            self._peaks_key = key
        pk = self._peaks
        mid = r.height() / 2
        amp = r.height() / 2 - 10
        xs = self._x(self.start)
        xe = self._x(self.end)
        sel = QColor(theme.ACCENT)
        sel.setAlpha(22)
        p.fillRect(QRectF(xs, 0, xe - xs, r.height()), sel)
        n = len(pk)
        scale = w / n
        on = QColor(self.color)
        off = QColor(theme.DIM)
        for i in range(n):
            x = i * scale
            s = self.view0 + int(i / n * (self.view1 - self.view0))
            p.setPen(QPen(on if self.start <= s <= self.end else off, max(1.0, scale)))
            y0 = mid - pk[i, 1] * amp
            y1 = mid - pk[i, 0] * amp
            p.drawLine(QPointF(x, y0), QPointF(x, max(y1, y0 + 1)))
        p.setFont(ui_font(7.5, QFont.Bold))
        for x, label, left in ((xs, "IN", False), (xe, "OUT", True)):
            p.setPen(QPen(QColor(theme.ACCENT2), 2))
            p.drawLine(QPointF(x, 0), QPointF(x, r.height()))
            tw = 28
            box = QRectF(x - tw if left else x, 0, tw, 14)
            p.fillRect(box, QColor(theme.ACCENT2))
            p.setPen(QColor(theme.INK))
            p.drawText(box, Qt.AlignCenter, label)
        if self.play_pos is not None:
            x = self._x(self.play_pos)
            p.setPen(QPen(QColor("white"), 1))
            p.drawLine(QPointF(x, 0), QPointF(x, r.height()))
        p.setPen(QColor(theme.MUTED))
        p.setFont(ui_font(7.5))
        p.drawText(QRectF(4, r.height() - 16, 120, 14), Qt.AlignLeft, fmt_time(self.view0 / SR))
        p.drawText(QRectF(r.width() - 124, r.height() - 16, 120, 14), Qt.AlignRight, fmt_time(self.view1 / SR))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))

    def mousePressEvent(self, e):
        if self.total == 0:
            return
        x = e.position().x()
        if e.button() == Qt.RightButton or e.button() == Qt.MiddleButton:
            self._pan_from = (x, self.view0, self.view1)
            return
        xs, xe = self._x(self.start), self._x(self.end)
        if abs(x - xs) < 10 and abs(x - xs) <= abs(x - xe):
            self._drag = "start"
        elif abs(x - xe) < 10:
            self._drag = "end"
        else:
            self._drag = None
            self.seekRequested.emit(max(0, min(self.total, self._s(x))) / SR)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._pan_from:
            x0, v0, v1 = self._pan_from
            ds = int((x0 - x) / max(1, self.width()) * (v1 - v0))
            ds = max(-v0, min(self.total - v1, ds))
            self.view0, self.view1 = v0 + ds, v1 + ds
            self.update()
            return
        if self._drag:
            s = max(0, min(self.total, self._s(x)))
            if self._drag == "start":
                self.start = max(0, min(s, self.end - int(0.01 * SR)))
            else:
                self.end = min(self.total, max(s, self.start + int(0.01 * SR)))
            self.update()
            self.trimChanged.emit(self.start / SR, self.end / SR)
            return
        xs, xe = self._x(self.start), self._x(self.end)
        self.setCursor(Qt.SizeHorCursor if min(abs(x - xs), abs(x - xe)) < 10 else Qt.ArrowCursor)

    def mouseReleaseEvent(self, _e):
        self._drag = None
        self._pan_from = None

    def wheelEvent(self, e):
        if self.total == 0:
            return
        x = e.position().x()
        anchor = self._s(x)
        factor = 0.8 if e.angleDelta().y() > 0 else 1.25
        span = max(int(SR * 0.02), min(self.total, int((self.view1 - self.view0) * factor)))
        frac = x / max(1, self.width())
        v0 = int(anchor - span * frac)
        v0 = max(0, min(self.total - span, v0))
        self.view0, self.view1 = v0, v0 + span
        self._peaks_key = None
        self.update()


# ===================================================================== capture

class CaptureButton(QPushButton):
    """Кнопка «Назначить»: ждёт нажатия клавиши/комбинации или кнопки MIDI."""
    captured = Signal(object)

    def __init__(self, ctx, text="+ НАЗНАЧИТЬ КЛАВИШУ / КНОПКУ", parent=None):
        super().__init__(text, parent)
        self.ctx = ctx
        self.idle_text = text
        self.setObjectName("capture")
        self.setCheckable(True)
        self.toggled.connect(self._toggled)

    def _toggled(self, on):
        if on:
            self.setText("▒▒ ЖДУ НАЖАТИЯ: КЛАВИША, СОЧЕТАНИЕ ИЛИ КНОПКА МАКРОПАДА ▒▒")
            try:
                self.ctx.bridge.captured.disconnect(self._got)
            except Exception:
                pass
            self.ctx.bridge.captured.connect(self._got)
            self.ctx.router.start_capture(self.ctx.bridge.captured.emit)
            if self.ctx.router.keyboard_error:
                self.setToolTip("Глобальные клавиши недоступны: " + self.ctx.router.keyboard_error)
        else:
            self.ctx.router.cancel_capture()
            self.setText(self.idle_text)
            try:
                self.ctx.bridge.captured.disconnect(self._got)
            except Exception:
                pass

    def _got(self, trig: Trigger):
        self.blockSignals(True)
        self.setChecked(False)
        self.blockSignals(False)
        self.setText(self.idle_text)
        try:
            self.ctx.bridge.captured.disconnect(self._got)
        except Exception:
            pass
        self.captured.emit(trig)

    def hideEvent(self, e):
        if self.isChecked():
            self.setChecked(False)
        super().hideEvent(e)


def confirm_trigger_conflict(parent, ctx, trig: Trigger, ignore_ids=()) -> bool:
    """Если триггер уже занят — спросить. True = можно назначать (старое удалено)."""
    from app.ui.texts import describe_action
    others = [b for b in ctx.project.bindings
              if b.trigger.key() == trig.key() and b.id not in ignore_ids
              and (b.trigger.channel < 0 or trig.channel < 0 or b.trigger.channel == trig.channel)]
    if not others:
        return True
    names = "\n".join("• " + describe_action(ctx.project, b.action) for b in others)
    r = QMessageBox.question(parent, "Клавиша уже занята",
                             f"{trig.label()} уже назначено на:\n{names}\n\nПереназначить на новое действие?",
                             QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
    if r == QMessageBox.Yes:
        for b in others:
            ctx.project.bindings.remove(b)
        return True
    if r == QMessageBox.No:
        return True   # оставить оба — сработают вместе
    return False


class ColorButton(QPushButton):
    colorChanged = Signal(str)

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 28)
        self.color = color
        self.setToolTip("Цвет метки")
        self._style()
        self.clicked.connect(self._pick)

    def _style(self):
        self.setStyleSheet(f"QPushButton {{ background:{self.color}; border:1px solid {theme.INK}; padding: 0; }}"
                           f"QPushButton:hover {{ border:1px solid white; }}")

    def set_color(self, c):
        self.color = c
        self._style()

    def _pick(self):
        from PySide6.QtWidgets import QGridLayout, QMenu, QWidgetAction
        menu = QMenu(self)
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(6, 6, 6, 6)
        g.setSpacing(4)
        for i, c in enumerate(theme.PAD_COLORS):
            b = QPushButton()
            b.setFixedSize(24, 24)
            b.setStyleSheet(f"QPushButton {{ background:{c}; border:1px solid {theme.INK}; padding:0; }}"
                            f"QPushButton:hover {{ border:2px solid white; }}")
            b.clicked.connect(lambda _=False, c=c: (self._set(c), menu.close()))
            g.addWidget(b, i // 6, i % 6)
        more = QPushButton("ДРУГОЙ…")
        more.clicked.connect(lambda: (menu.close(), self._custom()))
        g.addWidget(more, 2, 0, 1, 6)
        act = QWidgetAction(menu)
        act.setDefaultWidget(w)
        menu.addAction(act)
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))

    def _custom(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Цвет")
        if c.isValid():
            self._set(c.name())

    def _set(self, c):
        self.set_color(c)
        self.colorChanged.emit(c)


def hline():
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{theme.BORDER};")
    return f


def row(*widgets, stretch_last=False, spacing=8):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, str):
            lay.addWidget(QLabel(x))
        else:
            lay.addWidget(x)
    if stretch_last:
        lay.addStretch(1)
    return w
