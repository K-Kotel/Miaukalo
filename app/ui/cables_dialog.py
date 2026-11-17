"""Окно «Найти кабели»: какие виртуальные устройства добавить выходами."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from app.defaults import add_missing_cables, missing_cables
from . import theme
from .widgets import caption


class CablesDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.added = []
        self.setWindowTitle("Виртуальные кабели")
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 12)
        bar = QLabel("  НАЙДЕННЫЕ ВИРТУАЛЬНЫЕ УСТРОЙСТВА")
        bar.setStyleSheet(f"background: {theme.ACCENT}; color: {theme.INK}; font-weight: 700; padding: 4px;")
        lay.addWidget(bar)
        self.found = missing_cables(ctx.project)
        info = QLabel("Отметьте, какие добавить выходами. VB-Audio — основной вариант; устройства других программ "
                      "(Voicemod, Wave Link, Sonar, NVIDIA Broadcast…) работают, только пока запущена их программа.")
        info.setWordWrap(True)
        info.setObjectName("small")
        info.setContentsMargins(12, 6, 12, 0)
        lay.addWidget(info)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setMinimumHeight(220)
        host = QWidget()
        host.setObjectName("page")
        hl = QVBoxLayout(host)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.setSpacing(4)
        self.checks = []
        vendor = None
        if not self.found:
            lab = QLabel("Новых виртуальных устройств не найдено. Все найденные уже добавлены, или кабели не установлены.\n"
                         "Если только что установили VB-Cable — перезагрузите компьютер и нажмите «Обновить устройства».")
            lab.setWordWrap(True)
            hl.addWidget(lab)
        for c in self.found:
            if c["vendor"] != vendor:
                vendor = c["vendor"]
                hl.addWidget(caption(vendor + ("  — основной" if vendor == "VB-Audio" else "")))
            row = QHBoxLayout()
            cb = QCheckBox(c["label"])
            cb.setChecked(c["vendor"] in ("VB-Audio", "VAC"))
            cb.setToolTip(c["name"])
            mic = QLabel(("→ микрофон: " + c["mic"]) if c["mic"] else "→ сторона «микрофона» не найдена")
            mic.setStyleSheet(f"color: {theme.ACCENT2 if c['mic'] else theme.DIM}; font-size: 8.5pt;")
            row.addWidget(cb)
            row.addStretch(1)
            row.addWidget(mic)
            hl.addLayout(row)
            self.checks.append((cb, c))
        hl.addStretch(1)
        sc.setWidget(host)
        lay.addWidget(sc, 1)
        btns = QHBoxLayout()
        btns.setContentsMargins(12, 6, 12, 0)
        btns.addStretch(1)
        b_c = QPushButton("ОТМЕНА")
        b_c.clicked.connect(self.reject)
        b_ok = QPushButton("ДОБАВИТЬ ОТМЕЧЕННЫЕ")
        b_ok.setObjectName("primary")
        b_ok.setEnabled(bool(self.found))
        b_ok.clicked.connect(self._add)
        btns.addWidget(b_c)
        btns.addWidget(b_ok)
        lay.addLayout(btns)

    def _add(self):
        names = {c["name"] for cb, c in self.checks if cb.isChecked()}
        self.added = add_missing_cables(self.ctx.project, names=names)
        if self.added:
            self.ctx.apply()
        self.accept()
