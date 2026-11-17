"""Настройка цепочки эффектов канала + горячие клавиши для их включения."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMenu, QPushButton, QSlider, QVBoxLayout, QWidget)

from app.audio.effects import CATALOG, CATEGORIES, default_params, effect_title
from app.input.router import EFFECT_MODES
from app.model import Binding, EffectSlot, new_id
from . import theme
from .widgets import CaptureButton, caption, confirm_trigger_conflict


class EffectsDialog(QDialog):
    def __init__(self, ctx, channel, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.ch = channel
        self.setWindowTitle(f"Эффекты — {channel.name}")
        self.resize(820, 540)
        self._build()
        self._fill_list()

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        lay.setSpacing(16)
        left = QVBoxLayout()
        kind = "КАНАЛ" if self.ctx.project.channel(self.ch.id) else ("ШИНА" if getattr(self.ch, "bus", False) else "ВЫХОД")
        h = QLabel(f"{kind}: {self.ch.name.upper()}")
        h.setObjectName("h2")
        left.addWidget(h)
        sub = QLabel("Сигнал идёт сверху вниз. Квадрат — эффект включён.")
        sub.setObjectName("small")
        sub.setWordWrap(True)
        left.addWidget(sub)
        self.list = QListWidget()
        self.list.setFixedWidth(280)
        self.list.currentRowChanged.connect(self._select)
        self.list.itemChanged.connect(self._item_toggled)
        left.addWidget(self.list, 1)
        btns = QHBoxLayout()
        b_add = QPushButton("+ ЭФФЕКТ")
        b_add.setObjectName("primary")
        menu = QMenu(b_add)
        for cat in CATEGORIES:
            sub = menu.addMenu(cat.upper())
            for t, (title, c, _p) in CATALOG.items():
                if c == cat:
                    sub.addAction(title, lambda t=t: self._add(t))
        b_add.setMenu(menu)
        b_up = QPushButton("↑")
        b_dn = QPushButton("↓")
        b_rm = QPushButton("УДАЛИТЬ")
        b_up.clicked.connect(lambda: self._move(-1))
        b_dn.clicked.connect(lambda: self._move(1))
        b_rm.clicked.connect(self._remove)
        for b in (b_add, b_up, b_dn, b_rm):
            btns.addWidget(b)
        left.addLayout(btns)
        lay.addLayout(left)

        self.right = QFrame()
        self.right.setObjectName("card")
        self.right_lay = QVBoxLayout(self.right)
        self.right_lay.setContentsMargins(12, 10, 12, 12)
        lay.addWidget(self.right, 1)

    def _fill_list(self, select=0):
        self.list.blockSignals(True)
        self.list.clear()
        for s in self.ch.effects:
            it = QListWidgetItem(effect_title(s.type))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if s.enabled else Qt.Unchecked)
            keys = [b.trigger.label() for b in self._bindings(s)]
            if keys:
                it.setText(f"{effect_title(s.type)}   [{', '.join(keys)}]")
            self.list.addItem(it)
        self.list.blockSignals(False)
        if self.ch.effects:
            self.list.setCurrentRow(max(0, min(select, len(self.ch.effects) - 1)))
        else:
            self._select(-1)

    def _bindings(self, slot):
        return [b for b in self.ctx.project.bindings
                if b.action.get("type") == "effect" and b.action.get("effect") == slot.id]

    def _apply(self):
        self.ctx.engine.sync_effects(self.ch.id)
        self.ctx.apply()

    def _item_toggled(self, item):
        row = self.list.row(item)
        slot = self.ch.effects[row]
        self.ctx.engine.set_effect(self.ch.id, slot.id, item.checkState() == Qt.Checked)
        self.ctx.save_later()

    def _add(self, t):
        slot = EffectSlot(new_id("fx"), t, True, default_params(t))
        self.ch.effects.append(slot)
        self._apply()
        self._fill_list(len(self.ch.effects) - 1)

    def _remove(self):
        row = self.list.currentRow()
        if row < 0:
            return
        slot = self.ch.effects.pop(row)
        self.ctx.project.bindings = [b for b in self.ctx.project.bindings if b.action.get("effect") != slot.id]
        self._apply()
        self._fill_list(row)

    def _move(self, d):
        row = self.list.currentRow()
        j = row + d
        if row < 0 or not (0 <= j < len(self.ch.effects)):
            return
        e = self.ch.effects
        e[row], e[j] = e[j], e[row]
        self._apply()
        self._fill_list(j)

    def _clear_right(self):
        def clear(layout):
            while layout.count():
                it = layout.takeAt(0)
                w = it.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
                elif it.layout():
                    clear(it.layout())
        clear(self.right_lay)

    def _select(self, row):
        self._clear_right()
        if row < 0 or row >= len(self.ch.effects):
            lab = QLabel("[ ЦЕПОЧКА ПУСТА ]\n\nДобавьте эффект кнопкой «+ ЭФФЕКТ».\n\nНапример: «Эхо» на микрофон, "
                         "«Высота голоса» для смешного голоса, «Рация» для переговоров.")
            lab.setObjectName("muted")
            lab.setWordWrap(True)
            lab.setAlignment(Qt.AlignCenter)
            self.right_lay.addWidget(lab, 1)
            return
        slot = self.ch.effects[row]
        title, _cls, params = CATALOG[slot.type]
        h = QLabel(title.upper())
        h.setObjectName("h2")
        self.right_lay.addWidget(h)
        grid = QGridLayout()
        grid.setVerticalSpacing(12)
        for i, (name, label, lo, hi, default, step, unit) in enumerate(params):
            val = float(slot.params.get(name, default))
            sl = QSlider(Qt.Horizontal)
            steps = int(round((hi - lo) / step))
            sl.setRange(0, steps)
            sl.setValue(int(round((val - lo) / step)))
            vl = QLabel()
            vl.setFixedWidth(80)
            vl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            def fmt(v, unit=unit, step=step):
                if isinstance(unit, (list, tuple)):          # параметр-выбор: показываем название
                    return unit[max(0, min(len(unit) - 1, int(round(v))))]
                dec = 0 if step >= 1 else (1 if step >= 0.1 else 2)
                return f"{v:.{dec}f} {unit}".strip()

            vl.setText(fmt(val))

            def changed(pos, name=name, lo=lo, step=step, vl=vl, fmt=fmt):
                v = round(lo + pos * step, 4)
                slot.params[name] = v
                vl.setText(fmt(v))
                self.ctx.engine.sync_effects(self.ch.id)
                self.ctx.save_later()

            sl.valueChanged.connect(changed)
            grid.addWidget(caption(label), i, 0)
            grid.addWidget(sl, i, 1)
            grid.addWidget(vl, i, 2)
        self.right_lay.addLayout(grid)
        b_def = QPushButton("СБРОС ПАРАМЕТРОВ")
        b_def.setObjectName("ghost")

        def reset():
            slot.params = default_params(slot.type)
            self.ctx.engine.sync_effects(self.ch.id)
            self.ctx.save_later()
            self._select(row)

        b_def.clicked.connect(reset)
        self.right_lay.addWidget(b_def, 0, Qt.AlignLeft)
        self.right_lay.addSpacing(10)

        # горячие клавиши
        kh = QLabel("ГОРЯЧАЯ КЛАВИША")
        kh.setObjectName("h2")
        self.right_lay.addWidget(kh)
        mode = QComboBox()
        for k, v in EFFECT_MODES.items():
            mode.addItem(v, k)
        binds = self._bindings(slot)
        if binds:
            i = mode.findData(binds[0].action.get("mode", "toggle"))
            mode.setCurrentIndex(max(0, i))

        def mode_changed():
            for b in self._bindings(slot):
                b.action["mode"] = mode.currentData()
            self.ctx.apply()

        mode.currentIndexChanged.connect(mode_changed)
        chips = QHBoxLayout()
        for b in binds:
            chip = QPushButton(f"{b.trigger.label().upper()}  ✕")
            chip.setObjectName("chip")
            chip.clicked.connect(lambda _=False, b=b: self._remove_binding(b, row))
            chips.addWidget(chip)
        chips.addStretch(1)
        cap = CaptureButton(self.ctx)

        def got(trig):
            if not confirm_trigger_conflict(self, self.ctx, trig):
                return
            self.ctx.project.bindings.append(Binding(new_id("bind"), trig, {
                "type": "effect", "channel": self.ch.id, "effect": slot.id, "mode": mode.currentData()}))
            self.ctx.apply()
            self._fill_list(row)

        cap.captured.connect(got)
        mrow = QHBoxLayout()
        mrow.addWidget(caption("Как работает"))
        mrow.addWidget(mode, 1)
        self.right_lay.addLayout(mrow)
        self.right_lay.addLayout(chips)
        self.right_lay.addWidget(cap)
        self.right_lay.addStretch(1)

    def _remove_binding(self, b, row):
        if b in self.ctx.project.bindings:
            self.ctx.project.bindings.remove(b)
        self.ctx.apply()
        self._fill_list(row)
