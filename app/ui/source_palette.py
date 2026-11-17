"""Панель найденных источников звука (слева на «Коммутации»)."""
from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, QObject, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from app.audio.detect import Scanner, pad_source_key, source_key
from . import theme
from .widgets import Meter

MIME = "application/x-miaukalo-source"


def item_key(it: dict) -> str:
    return source_key(it["type"], it.get("device", ""), it.get("process", ""))


class _Bridge(QObject):
    scanned = Signal(dict)


class SourceRow(QFrame):
    def __init__(self, palette, item: dict):
        super().__init__()
        self.palette = palette
        self.item = item
        self.setObjectName("srcrow")
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Перетащите на схему (на пустое место — новый источник, на блок канала — добавить в него) "
                        "или нажмите «+»")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 4, 4)
        lay.setSpacing(6)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.name = QLabel()
        self.name.setStyleSheet("font-weight: 700;")
        self.sub = QLabel()
        self.sub.setObjectName("cap")
        self.meter = Meter(vertical=False)
        self.meter.setFixedHeight(4)
        col.addWidget(self.name)
        col.addWidget(self.sub)
        col.addWidget(self.meter)
        lay.addLayout(col, 1)
        self.add = QPushButton("+")
        self.add.setFixedSize(26, 26)
        self.add.setStyleSheet(f"QPushButton {{ padding: 0; min-height: 0; font-size: 12pt; font-weight: 700; color: {theme.ACCENT}; }}"
                               f"QPushButton:hover {{ background: {theme.ACCENT}; color: {theme.INK}; }}")
        self.add.setToolTip("Добавить на схему отдельным источником")
        self.add.clicked.connect(lambda: palette.add_requested(self.item, None))
        lay.addWidget(self.add)
        self._drag_start = None
        self.update_item(item)

    def update_item(self, item: dict):
        self.item = item
        used = item_key(item) in self.palette.used
        active = item.get("active", True)
        fm = self.name.fontMetrics()
        self.name.setText(fm.elidedText(item["label"].upper(), Qt.ElideRight, 170))
        sub = item.get("sub", "")
        tag = "● В СХЕМЕ  " if used else ""
        if item["type"] in ("process", "tab") and not active:
            tag += "ТИХО  "
        self.sub.setText(fm.elidedText(tag + sub, Qt.ElideRight, 190))
        self.sub.setToolTip(sub)
        col = theme.ACCENT2 if used else (theme.TEXT if active else theme.MUTED)
        self.name.setStyleSheet(f"font-weight: 700; color: {col};")
        border = theme.ACCENT if used else theme.BORDER
        self.setStyleSheet(f"QFrame#srcrow {{ background: {theme.SURFACE}; border: 1px solid {border}; }}"
                           f"QFrame#srcrow:hover {{ border-color: {theme.ACCENT}; }}")
        self.meter.setVisible(item["type"] in ("process", "tab"))
        self.meter.set_level(item.get("level", 0.0))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_start = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self._drag_start is None or not (e.buttons() & Qt.LeftButton):
            return
        if (e.position().toPoint() - self._drag_start).manhattanLength() < 6:
            return
        md = QMimeData()
        md.setData(MIME, json.dumps(self.item).encode("utf-8"))
        md.setText(self.item["label"])
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(self.grab())
        drag.setHotSpot(self._drag_start)
        self._drag_start = None
        drag.exec(Qt.CopyAction)


class Section(QWidget):
    def __init__(self, palette, title, key, hint=""):
        super().__init__()
        self.palette = palette
        self.key = key
        self.title = title
        self.rows: dict[str, SourceRow] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.head = QPushButton()
        self.head.setCheckable(True)
        self.head.setChecked(True)
        self.head.setStyleSheet(f"QPushButton {{ text-align: left; background: {theme.SURFACE2}; border: none;"
                                f" border-left: 3px solid {theme.ACCENT}; padding: 4px 8px; font-weight: 700; color: {theme.ACCENT}; }}"
                                f"QPushButton:checked {{ background: {theme.SURFACE2}; color: {theme.ACCENT}; }}"
                                f"QPushButton:hover {{ color: {theme.ACCENT2}; }}")
        self.head.toggled.connect(self._toggle)
        lay.addWidget(self.head)
        self.body = QWidget()
        self.bl = QVBoxLayout(self.body)
        self.bl.setContentsMargins(0, 0, 0, 4)
        self.bl.setSpacing(3)
        lay.addWidget(self.body)
        self.empty = QLabel(hint)
        self.empty.setObjectName("cap")
        self.empty.setWordWrap(True)
        self.bl.addWidget(self.empty)
        self._count = 0
        self._label()

    def _label(self):
        arrow = "▾" if self.head.isChecked() else "▸"
        self.head.setText(f"{arrow} {self.title}  [{self._count}]")

    def _toggle(self, on):
        self.body.setVisible(on)
        self._label()

    def set_items(self, items: list):
        keys = [item_key(i) for i in items]
        if keys != list(self.rows):
            for r in self.rows.values():
                r.setParent(None)
                r.deleteLater()
            self.rows = {}
            for k, it in zip(keys, items):
                row = SourceRow(self.palette, it)
                self.rows[k] = row
                self.bl.addWidget(row)
        else:
            for k, it in zip(keys, items):
                self.rows[k].update_item(it)
        self._count = len(items)
        self.empty.setVisible(not items)
        self._label()


class SourcePalette(QWidget):
    def __init__(self, ctx, on_add):
        super().__init__()
        self.ctx = ctx
        self.on_add = on_add
        self.used: set = set()
        self.setFixedWidth(262)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QLabel("  ИСТОЧНИКИ · НАЙДЕНО")
        head.setStyleSheet(f"background: {theme.ACCENT}; color: {theme.INK}; font-weight: 700; padding: 3px;")
        lay.addWidget(head)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setStyleSheet(f"QScrollArea {{ border: 1px solid {theme.BORDER}; border-top: none; }}")
        host = QWidget()
        host.setObjectName("page")
        hl = QVBoxLayout(host)
        hl.setContentsMargins(4, 4, 4, 4)
        hl.setSpacing(6)
        self.sections = {
            "tabs": Section(self, "ВКЛАДКИ БРАУЗЕРА", "tabs",
                            "Звук отдельной вкладки: установите расширение «Мяукало — звук вкладки» "
                            "(«Устройства» → «Расширение для браузера») и нажмите его значок на нужной вкладке."),
            "programs": Section(self, "ПРОГРАММЫ", "programs",
                                "Включите звук в браузере или плеере — программа появится здесь."),
            "inputs": Section(self, "МИКРОФОНЫ И ВХОДЫ", "inputs", "Устройства ввода не найдены."),
            "cables": Section(self, "КАБЕЛИ: ЧТО В НИХ ПРИШЛО", "cables",
                              "Нет виртуальных кабелей. Сюда попадает звук, который другие программы отправили в VB-Cable."),
        }
        for s in self.sections.values():
            hl.addWidget(s)
        hl.addStretch(1)
        sc.setWidget(host)
        lay.addWidget(sc, 1)
        tip = QLabel("ПЕРЕТАЩИТЕ НА СХЕМУ ИЛИ «+»")
        tip.setObjectName("cap")
        tip.setAlignment(Qt.AlignCenter)
        tip.setStyleSheet(f"padding: 3px; color: {theme.DIM};")
        lay.addWidget(tip)
        self._bridge = _Bridge()
        self._bridge.scanned.connect(self._apply)
        self.scanner = Scanner(self._bridge.scanned.emit, interval=0.8)
        self._last = None

    def refresh_used(self):
        self.used = {k for k in (pad_source_key(p) for p in self.ctx.project.pads) if k}
        if self._last:
            self._apply(self._last)

    def _apply(self, data: dict):
        self._last = data
        self.used = {k for k in (pad_source_key(p) for p in self.ctx.project.pads) if k}
        for key in ("tabs", "programs", "inputs", "cables"):
            self.sections[key].set_items(data.get(key, []))

    def add_requested(self, item, scene_pos):
        self.on_add(item, scene_pos, None)

    def showEvent(self, e):
        super().showEvent(e)
        self.scanner.start()

    def hideEvent(self, e):
        self.scanner.stop()
        super().hideEvent(e)
