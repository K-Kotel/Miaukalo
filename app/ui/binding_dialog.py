"""Диалог привязки: клавиша/MIDI → любое действие."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QPushButton,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)

from app.audio.effects import effect_title
from app.input.router import EFFECT_MODES, MUTE_MODES, DRUM_OPS, REC_OPS, REVERSE_MODES, REVERSE_SCOPES, SWITCH_OPS
from app.audio.effects import RANDOM_POOLS
from app.model import ACTIONS, Binding, Trigger, new_id
from . import theme
from .widgets import CaptureButton, caption, confirm_trigger_conflict


class BindingDialog(QDialog):
    def __init__(self, ctx, binding: Binding | None = None, parent=None, preset: dict | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.orig = binding
        self.b = copy.deepcopy(binding) if binding else Binding(new_id("bind"), Trigger(), dict(preset or {"type": "stop_all"}))
        self.setWindowTitle("Привязка" if binding else "Новая привязка")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)

        h = QLabel("ЧТО НАЖАТЬ")
        h.setObjectName("h2")
        lay.addWidget(h)
        self.trig_label = QLabel()
        self.trig_label.setStyleSheet(f"font-size: 13pt; font-weight: 700; color: {theme.ACCENT2}; border: 1px solid {theme.ACCENT2}; padding: 6px;")
        lay.addWidget(self.trig_label)
        self.cap = CaptureButton(ctx, "НАЗНАЧИТЬ КЛАВИШУ / КНОПКУ / КРУТИЛКУ")
        self.cap.captured.connect(self._got)
        lay.addWidget(self.cap)

        h2 = QLabel("ЧТО СДЕЛАТЬ")
        h2.setObjectName("h2")
        lay.addWidget(h2)
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignRight)
        self.action = QComboBox()
        for k, v in ACTIONS.items():
            self.action.addItem(v, k)
        self.form.addRow(caption("Действие"), self.action)

        self.pad = QComboBox()
        for p in ctx.project.pads:
            self.pad.addItem(p.name, p.id)
        self.channel = QComboBox()
        for c in ctx.project.channels:
            self.channel.addItem(c.name, c.id)
        self.step = QSpinBox()
        self.step.setRange(-50, 50)
        self.step.setSuffix(" %")
        self.value = QSlider(Qt.Horizontal)
        self.value.setRange(0, 100)
        self.value_label = QLabel()
        self.value.valueChanged.connect(lambda v: self.value_label.setText(f"{v}%"))
        vrow = QWidget()
        vl = QHBoxLayout(vrow)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.value, 1)
        vl.addWidget(self.value_label)
        self.mute_mode = QComboBox()
        for k, v in MUTE_MODES.items():
            self.mute_mode.addItem(v, k)
        self.effect = QComboBox()
        self.fx_mode = QComboBox()
        for k, v in EFFECT_MODES.items():
            self.fx_mode.addItem(v, k)
        self.fxowner = QComboBox()
        for c in ctx.project.channels:
            self.fxowner.addItem(f"Канал: {c.name}", c.id)
        for o in ctx.project.outputs:
            self.fxowner.addItem(("Запись: " if o.recorder else "Шина: " if o.bus else "Выход: ") + o.name, o.id)
        self.switch = QComboBox()
        for o in ctx.project.outputs:
            if o.switch:
                self.switch.addItem(("1→N: " if o.switch == "split" else "N→1: ") + o.name, o.id)
        self.sw_op = QComboBox()
        for k, v in SWITCH_OPS.items():
            self.sw_op.addItem(v, k)
        self.sw_index = QComboBox()
        from app.audio.player import OPS as PLAYER_OPS
        self.pl_op = QComboBox()
        for k, v in PLAYER_OPS.items():
            self.pl_op.addItem(v, k)
        self.rec = QComboBox()
        for o in ctx.project.outputs:
            if o.recorder:
                self.rec.addItem(("● Файл: " if o.recorder == "file" else "● Звуки: ") + o.name, o.id)
        self.rec_op = QComboBox()
        for k, v in REC_OPS.items():
            self.rec_op.addItem(v, k)
        self.output = QComboBox()
        for o in ctx.project.outputs:
            if not o.recorder:
                self.output.addItem(("Шина: " if o.bus else "Выход: ") + o.name, o.id)
        self.rev_scope = QComboBox()
        for k, v in REVERSE_SCOPES.items():
            self.rev_scope.addItem(v, k)
        self.rev_mode = QComboBox()
        for k, v in REVERSE_MODES.items():
            self.rev_mode.addItem(v, k)
        self.rnd_count = QSpinBox()
        self.rnd_count.setRange(1, 6)
        self.rnd_count.setSuffix(" шт.")
        self.rnd_strength = QSpinBox()
        self.rnd_strength.setRange(10, 100)
        self.rnd_strength.setSingleStep(10)
        self.rnd_strength.setSuffix(" %")
        self.rnd_strength.setToolTip("Насколько далеко от обычных настроек могут уйти случайные параметры")
        self.rnd_pool = QComboBox()
        for k, v in RANDOM_POOLS.items():
            self.rnd_pool.addItem(v, k)
        self.drum_op = QComboBox()
        for k, v in DRUM_OPS.items():
            self.drum_op.addItem(v, k)
        self.rows = {
            "pad": self.pad, "channel": self.channel, "fxowner": self.fxowner, "step": self.step, "value": vrow,
            "mute_mode": self.mute_mode, "effect": self.effect, "fx_mode": self.fx_mode,
            "switch": self.switch, "sw_op": self.sw_op, "sw_index": self.sw_index, "pl_op": self.pl_op,
            "rec": self.rec, "rec_op": self.rec_op,
            "output": self.output, "rev_scope": self.rev_scope, "rev_mode": self.rev_mode,
            "rnd_count": self.rnd_count, "rnd_strength": self.rnd_strength, "rnd_pool": self.rnd_pool,
            "drum_op": self.drum_op,
        }
        labels = {"pad": "Звук", "channel": "Канал", "fxowner": "Где эффект", "step": "Шаг", "value": "Уровень",
                  "mute_mode": "Режим", "effect": "Эффект", "fx_mode": "Режим",
                  "switch": "Переключатель", "sw_op": "Что сделать", "sw_index": "Положение", "pl_op": "Команда",
                  "rec": "Блок записи", "rec_op": "Как", "output": "Выход",
                  "rev_scope": "Что развернуть", "rev_mode": "Режим",
                  "rnd_count": "Сколько эффектов", "rnd_strength": "Насколько дико", "rnd_pool": "Какие эффекты", "drum_op": "Команда"}
        for k, w in self.rows.items():
            self.form.addRow(caption(labels[k]), w)
        lay.addLayout(self.form)
        self.hint = QLabel()
        self.hint.setObjectName("small")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        lay.addStretch(1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        b_c = QPushButton("ОТМЕНА")
        b_c.clicked.connect(self.reject)
        self.b_ok = QPushButton("СОХРАНИТЬ")
        self.b_ok.setObjectName("primary")
        self.b_ok.clicked.connect(self._save)
        btns.addWidget(b_c)
        btns.addWidget(self.b_ok)
        lay.addLayout(btns)

        self.action.currentIndexChanged.connect(self._update)
        self.fxowner.currentIndexChanged.connect(self._fill_effects)
        self.switch.currentIndexChanged.connect(self._fill_positions)
        self.sw_op.currentIndexChanged.connect(self._update)
        self.rev_scope.currentIndexChanged.connect(self._update)
        self._load()

    def _set(self, combo, data):
        i = combo.findData(data)
        if i >= 0:
            combo.setCurrentIndex(i)

    def _load(self):
        a = self.b.action
        self._set(self.action, a.get("type", "stop_all"))
        self._set(self.pad, a.get("pad"))
        self._set(self.channel, a.get("channel"))
        self.step.setValue(int(round(float(a.get("step", 0.05)) * 100)))
        self.value.setValue(int(round(float(a.get("value", 0.8)) * 100)))
        self._set(self.mute_mode, a.get("mode", "toggle"))
        self._set(self.fx_mode, a.get("mode", "toggle"))
        self._set(self.fxowner, a.get("channel"))
        self._fill_effects()
        self._set(self.effect, a.get("effect"))
        self._set(self.switch, a.get("switch"))
        self._set(self.sw_op, a.get("op", "next"))
        self._set(self.pl_op, a.get("op", "play_pause"))
        self._set(self.output, a.get("output"))
        self._set(self.rev_scope, a.get("scope", "all"))
        self._set(self.rev_mode, a.get("mode", "hold"))
        self.rnd_count.setValue(int(a.get("count", 3)))
        self.rnd_strength.setValue(int(round(float(a.get("strength", 0.7)) * 100)))
        self._set(self.rnd_pool, a.get("pool", "all"))
        self._set(self.rec, a.get("rec"))
        self._set(self.drum_op, a.get("op", "play_stop"))
        self._set(self.rec_op, a.get("op", "hold"))
        self._fill_positions()
        self.sw_index.setCurrentIndex(max(0, min(int(a.get("index", 0)), self.sw_index.count() - 1)))
        self._update()
        self._show_trigger()

    def _fill_effects(self):
        self.effect.clear()
        oid = self.fxowner.currentData()
        owner = self.ctx.project.channel(oid) or self.ctx.project.output(oid)
        for s in (owner.effects if owner else []):
            self.effect.addItem(effect_title(s.type), s.id)

    def _fill_positions(self):
        self.sw_index.clear()
        sw = self.ctx.project.output(self.switch.currentData())
        if sw:
            for i, pid in enumerate(self.ctx.project.switch_ports(sw)):
                self.sw_index.addItem(f"{i + 1} — {self.ctx.project.node_name(pid)}", i)
        if not self.sw_index.count():
            self.sw_index.addItem("1 — (ещё не подключено)", 0)

    def _update(self):
        t = self.action.currentData()
        need = {
            "pad": ["pad"], "stop_pad": ["pad"], "stop_channel": ["channel"], "stop_all": [],
            "volume_step": ["channel", "step"], "volume_set": ["channel", "value"],
            "mute": ["channel", "mute_mode"], "effect": ["fxowner", "effect", "fx_mode"], "quick_mixer": [],
            "switch": ["switch", "sw_op"] + (["sw_index"] if self.sw_op.currentData() in ("set", "toggle", "hold") else []),
            "player": ["pl_op"], "record": ["rec", "rec_op"],
            "mute_out": ["output", "mute_mode"],
            "reverse": ["rev_scope"] + {"channel": ["channel"], "pad": ["pad"]}.get(self.rev_scope.currentData(), [])
                       + ["rev_mode"],
            "pad_random": ["pad", "rnd_count", "rnd_strength", "rnd_pool"], "drum": ["drum_op"],
        }[t]
        for k, w in self.rows.items():
            vis = k in need
            w.setVisible(vis)
            self.form.labelForField(w).setVisible(vis)
        hints = {
            "volume_set": "Назначьте на крутилку (MIDI CC) — громкость будет следовать её положению. "
                          "Если назначить клавишу, она выставит указанный уровень.",
            "volume_step": "Удерживайте клавишу — громкость будет меняться плавно (автоповтор).",
            "mute": "«Звук, только пока держишь» — режим рации (push-to-talk) для микрофона.",
            "effect": "Эффекты канала — «Микшер» → «Эффекты»; эффекты выхода или шины — двойной клик по блоку в «Коммутации».",
            "switch": "Переключатели создаются во вкладке «Коммутация» (+ 1→N / + N→1).",
            "mute_out": "Звук выхода пропадает плавно, без щелчка; устройство остаётся подключённым. "
                        "«Звук, пока держишь» — например, микрофон в Дискорд только по кнопке.",
            "reverse": "Звуки, которые играют в момент нажатия, пойдут задом наперёд с того же места; "
                       "отпустили — снова вперёд. Дошёл до начала — молчит, пока держите.",
            "pad_random": "Каждое нажатие — новый набор эффектов со случайными настройками (видно в строке "
                          "состояния внизу). На громкость стоит страховочный лимитер.",
            "record": "Блоки записи добавляются во вкладке «Коммутация» (+ ЗАПИСЬ). «Пока держишь» — удобно "
                      "для коротких звуков: зажал, сказал, отпустил — звук уже в саундпаде.",
            "quick_mixer": "Маленькое окно поверх всего: громкости, эффекты и звуки мышкой, рядом подписаны их клавиши. "
                           "Повторное нажатие закрывает.",
        }
        self.hint.setText(hints.get(t, ""))
        if t == "volume_step" and self.step.value() == 0:
            self.step.setValue(5)

    def _show_trigger(self):
        t = self.b.trigger
        has = (t.type == "key" and t.combo) or (t.type != "key" and t.number >= 0)
        self.trig_label.setText(t.label().upper() if has else "— НЕ НАЗНАЧЕНО —")

    def _got(self, trig):
        self.b.trigger = trig
        self._show_trigger()

    def _save(self):
        t = self.b.trigger
        if not ((t.type == "key" and t.combo) or (t.type != "key" and t.number >= 0)):
            self.trig_label.setText("Сначала нажмите «Назначить»")
            return
        a = {"type": self.action.currentData()}
        typ = a["type"]
        if typ in ("pad", "stop_pad"):
            if self.pad.currentData() is None:
                return
            a["pad"] = self.pad.currentData()
        if typ in ("stop_channel", "volume_step", "volume_set", "mute"):
            a["channel"] = self.channel.currentData()
        if typ == "mute_out":
            if self.output.currentData() is None:
                return
            a["output"] = self.output.currentData()
            a["mode"] = self.mute_mode.currentData()
        if typ == "reverse":
            a["scope"] = self.rev_scope.currentData()
            a["mode"] = self.rev_mode.currentData()
            if a["scope"] == "channel":
                a["channel"] = self.channel.currentData()
            elif a["scope"] == "pad":
                if self.pad.currentData() is None:
                    return
                a["pad"] = self.pad.currentData()
        if typ == "drum":
            a["op"] = self.drum_op.currentData()
        if typ == "pad_random":
            if self.pad.currentData() is None:
                return
            a["pad"] = self.pad.currentData()
            a["count"] = self.rnd_count.value()
            a["strength"] = self.rnd_strength.value() / 100
            a["pool"] = self.rnd_pool.currentData()
        if typ == "effect":
            a["channel"] = self.fxowner.currentData()
        if typ == "player":
            a["op"] = self.pl_op.currentData()
        if typ == "record":
            if self.rec.currentData() is None:
                self.hint.setText("Блоков записи пока нет — добавьте их во вкладке «Коммутация» (+ ЗАПИСЬ).")
                return
            a["rec"] = self.rec.currentData()
            a["op"] = self.rec_op.currentData()
        if typ == "switch":
            if self.switch.currentData() is None:
                self.hint.setText("Переключателей пока нет — добавьте их во вкладке «Коммутация».")
                return
            a["switch"] = self.switch.currentData()
            a["op"] = self.sw_op.currentData()
            a["index"] = int(self.sw_index.currentData() or 0)
        if typ == "volume_step":
            a["step"] = self.step.value() / 100
        if typ == "volume_set":
            a["value"] = self.value.value() / 100
        if typ == "mute":
            a["mode"] = self.mute_mode.currentData()
        if typ == "effect":
            if self.effect.currentData() is None:
                self.hint.setText("Здесь нет эффектов — сначала добавьте их.")
                return
            a["effect"] = self.effect.currentData()
            a["mode"] = self.fx_mode.currentData()
        self.b.action = a
        proj = self.ctx.project
        changed_trigger = self.orig is None or self.orig.trigger.key() != t.key()
        if changed_trigger and not confirm_trigger_conflict(self, self.ctx, t, {self.b.id}):
            return
        if self.orig is not None and self.orig in proj.bindings:
            proj.bindings[proj.bindings.index(self.orig)] = self.b
        else:
            proj.bindings.append(self.b)
        self.ctx.apply()
        self.accept()
