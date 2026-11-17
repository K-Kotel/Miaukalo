"""Редактирование цепочки эффектов дорожки или окна."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QPushButton,
                               QVBoxLayout)

from app.audio.decoder import SR
from app.audio.effects import CATALOG, CATEGORIES, default_params, effect_title
from app.model import EffectSlot, new_id


def time_field(frame, maximum):
    field = QDoubleSpinBox()
    field.setDecimals(6)
    field.setRange(0, maximum / SR)
    field.setValue(frame / SR)
    field.setSingleStep(0.1)
    field.setSuffix(" с")
    return field


class EditorEffectsDialog(QDialog):
    def __init__(self, title, effects, parent, bounds=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 420)
        self.effects = deepcopy(effects)
        self.bounds = bounds
        lay = QVBoxLayout(self)
        if bounds:
            time = QHBoxLayout()
            self.start = time_field(bounds[0], max(bounds[1] * 2, SR * 24 * 3600))
            self.end = time_field(bounds[1], max(bounds[1] * 2, SR * 24 * 3600))
            for label, field in (("Начало", self.start), ("Конец", self.end)):
                time.addWidget(QLabel(label))
                time.addWidget(field, 1)
            lay.addLayout(time)
        body = QHBoxLayout()
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setMinimumWidth(240)
        self.list.currentRowChanged.connect(self._select)
        self.list.itemChanged.connect(self._toggle)
        left.addWidget(self.list, 1)
        tools = QHBoxLayout()
        add = QPushButton("+ Эффект")
        menu = QMenu(add)
        for category in CATEGORIES:
            group = menu.addMenu(category)
            for kind, (name, cat, _) in CATALOG.items():
                if category == cat:
                    group.addAction(name, lambda kind=kind: self._add(kind))
        add.setMenu(menu)
        tools.addWidget(add)
        for label, tip, callback in (
            ("↑", "Поднять эффект в цепочке", lambda: self._move(-1)),
            ("↓", "Опустить эффект в цепочке", lambda: self._move(1)),
            ("Удалить", "Убрать выбранный эффект", self._remove),
        ):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(callback)
            tools.addWidget(button)
        left.addLayout(tools)
        body.addLayout(left)
        self.params = QFormLayout()
        body.addLayout(self.params, 1)
        lay.addLayout(body, 1)
        self.buttons = QDialogButtonBox()
        ok = self.buttons.addButton("Применить", QDialogButtonBox.AcceptRole)
        self.buttons.addButton("Отмена", QDialogButtonBox.RejectRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        if bounds:
            def validate():
                ok.setEnabled(self.start.value() < self.end.value() and bool(self.effects))
            self.start.valueChanged.connect(validate)
            self.end.valueChanged.connect(validate)
            self.list.model().rowsInserted.connect(validate)
            self.list.model().rowsRemoved.connect(validate)
            validate()
        lay.addWidget(self.buttons)
        self._fill(0)

    def accept(self):
        if self.bounds:
            self.bounds = round(self.start.value() * SR), round(self.end.value() * SR)
        super().accept()

    def _fill(self, row):
        self.list.blockSignals(True)
        self.list.clear()
        for effect in self.effects:
            item = QListWidgetItem(effect_title(effect.type))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if effect.enabled else Qt.Unchecked)
            self.list.addItem(item)
        row = min(len(self.effects) - 1, row)
        self.list.setCurrentRow(row)
        self.list.blockSignals(False)
        self._select(row)

    def _select(self, row):
        while self.params.rowCount():
            self.params.removeRow(0)
        if row < 0:
            self.params.addRow(QLabel("Добавьте эффект, чтобы настроить его параметры."))
            return
        effect = self.effects[row]
        self.params.addRow(QLabel(effect_title(effect.type)))
        for name, title, low, high, default, step, unit in CATALOG.get(effect.type, ("", "", []))[2]:
            value = effect.params.get(name, default)
            if isinstance(unit, (list, tuple)):
                control = QComboBox()
                control.addItems(unit)
                control.setCurrentIndex(int(value))
                control.currentIndexChanged.connect(lambda v, key=name: effect.params.__setitem__(key, v))
            else:
                control = QDoubleSpinBox()
                control.setDecimals(0 if step >= 1 else 1 if step >= .1 else 3)
                control.setRange(low, high)
                control.setSingleStep(step)
                if unit:
                    control.setSuffix(" " + unit)
                control.setValue(value)
                control.valueChanged.connect(lambda v, key=name: effect.params.__setitem__(key, v))
            self.params.addRow(title, control)

    def _toggle(self, item):
        self.effects[self.list.row(item)].enabled = item.checkState() == Qt.Checked

    def _add(self, kind):
        self.effects.append(EffectSlot(new_id("fx"), kind, True, default_params(kind)))
        self._fill(len(self.effects) - 1)

    def _move(self, delta):
        row = self.list.currentRow()
        target = row + delta
        if row >= 0 and 0 <= target < len(self.effects):
            self.effects[row], self.effects[target] = self.effects[target], self.effects[row]
            self._fill(target)

    def _remove(self):
        row = self.list.currentRow()
        if row >= 0:
            self.effects.pop(row)
            self._fill(row)
