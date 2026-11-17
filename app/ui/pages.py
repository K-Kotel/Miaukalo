"""Вкладки главного окна: Звуки, Микшер, Клавиши, Устройства."""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from app.audio import devices
from app.audio.decoder import AUDIO_FILTER
from app.audio.effects import effect_title
from app.model import TRANSITION_HINTS, TRANSITIONS, Channel, LiveSource, Output, Pad, import_audio_file, new_id
from . import theme
from .binding_dialog import BindingDialog
from .effects_dialog import EffectsDialog
from .pad_editor import PadEditor
from .texts import describe_action
from .widgets import ColorButton, Meter, PadButton, Panel, caption


def page_widget():
    w = QWidget()
    w.setObjectName("page")
    return w


# ===================================================================== Звуки

class SoundsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("page")
        self.buttons: dict[str, PadButton] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        b_add = QPushButton("+ ДОБАВИТЬ ЗВУКИ")
        b_add.setObjectName("primary")
        b_add.clicked.connect(self.add_files)
        b_live = QPushButton("+ ЖИВОЙ ВХОД")
        b_live.setToolTip("Микрофон, звук браузера (YouTube) или другой программы")
        b_live.clicked.connect(self.add_live)
        self.filter = QComboBox()
        self.filter.setMinimumWidth(170)
        self.filter.currentIndexChanged.connect(self.rebuild)
        self.search = QLineEdit()
        self.search.setPlaceholderText("ПОИСК…")
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(self.rebuild)
        bar.addWidget(b_add)
        bar.addWidget(b_live)
        bar.addStretch(1)
        bar.addWidget(caption("Канал"))
        bar.addWidget(self.filter)
        bar.addWidget(self.search)
        lay.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid_host.setObjectName("page")
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.grid.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.grid_host)
        lay.addWidget(self.scroll, 1)

        self.empty = QLabel("[ НЕТ ЗВУКОВ ]\n\nперетащите сюда аудиофайлы (mp3, wav, ogg…)\nили нажмите «+ ДОБАВИТЬ ЗВУКИ»")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("muted")
        self.empty.setStyleSheet(f"font-size: 11pt; color: {theme.MUTED}; padding: 60px;"
                                 f"border: 1px dashed {theme.DIM};")
        lay.addWidget(self.empty)
        self._cols = 0
        self.fill_filter()
        self.rebuild()

    def fill_filter(self):
        cur = self.filter.currentData()
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem("ВСЕ", "")
        for c in self.ctx.project.channels:
            self.filter.addItem(c.name, c.id)
        i = self.filter.findData(cur)
        self.filter.setCurrentIndex(max(0, i))
        self.filter.blockSignals(False)

    def _hotkeys(self, pad):
        return ", ".join(b.trigger.label() for b in self.ctx.project.bindings_for_pad(pad.id))

    def rebuild(self):
        for b in self.buttons.values():
            b.setParent(None)
            b.deleteLater()
        self.buttons.clear()
        f = self.filter.currentData() or ""
        q = self.search.text().strip().lower()
        pads = [p for p in self.ctx.project.pads if (not f or p.channel == f) and (not q or q in p.name.lower())]
        allp = self.ctx.project.pads
        for p in pads:
            ch = self.ctx.project.channel(p.channel)
            btn = PadButton(p, ch.name if ch else "?", self._hotkeys(p), index=allp.index(p) + 1)
            btn.pressed.connect(lambda pid: self.ctx.engine.pad_event(pid, "press"))
            btn.released.connect(lambda pid: self.ctx.engine.pad_event(pid, "release"))
            btn.edit.connect(self.edit_pad)
            btn.context.connect(self.pad_menu)
            self.buttons[p.id] = btn
        self.empty.setVisible(not self.ctx.project.pads)
        self.scroll.setVisible(bool(self.ctx.project.pads))
        self._cols = 0
        self.relayout()

    def relayout(self):
        cols = max(1, (self.scroll.viewport().width() + 6) // 196)
        if cols == self._cols and self.grid.count() == len(self.buttons):
            return
        self._cols = cols
        while self.grid.count():
            self.grid.takeAt(0)
        for i, b in enumerate(self.buttons.values()):
            self.grid.addWidget(b, i // cols, i % cols)
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self.relayout)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self.relayout)

    def tick(self, states):
        cache = self.ctx.engine.cache
        for pid, btn in self.buttons.items():
            p = btn.pad
            status = ""
            if p.kind == "file":
                if cache.is_loading(p.file):
                    status = "загрузка…"
                elif cache.error(p.file):
                    status = "ошибка: файл не открывается"
            err = self.ctx.engine.pad_errors.get(pid)
            if err and pid not in states:
                status = "ошибка: " + err
            btn.set_state(states.get(pid), status)

    # ------------------------------------------------------------ actions
    def add_files(self, paths=None):
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(self, "Добавить звуки", "", AUDIO_FILTER)
        if not paths:
            return
        proj = self.ctx.project
        ch = proj.channel(self.filter.currentData() or "") or _default_sfx_channel(proj)
        used = len(proj.pads)
        for i, path in enumerate(paths):
            try:
                stored = import_audio_file(path)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Файл", f"{path}: {e}")
                continue
            pad = Pad(new_id("pad"), Path(path).stem[:40], file=stored, channel=ch.id,
                      color=theme.PAD_COLORS[(used + i) % len(theme.PAD_COLORS)])
            proj.pads.append(pad)
            self.ctx.engine.cache.request(stored)
        self.ctx.apply()
        self.rebuild()

    def add_live(self):
        proj = self.ctx.project
        ch = proj.channel(self.filter.currentData() or "") or proj.channels[0]
        pad = Pad(new_id("pad"), "Живой вход", kind="live", channel=ch.id, mode="toggle",
                  color="#5fd4d0", source=LiveSource(type="process", process="chrome.exe"), fade_out_ms=300)
        dlg = PadEditor(self.ctx, pad, is_new=True, parent=self)
        if dlg.exec():
            self.rebuild()

    def edit_pad(self, pid):
        pad = self.ctx.project.pad(pid)
        if pad is None:
            return
        dlg = PadEditor(self.ctx, pad, parent=self)
        if dlg.exec():
            self.rebuild()

    def pad_menu(self, pid, pos):
        pad = self.ctx.project.pad(pid)
        if pad is None:
            return
        m = QMenu(self)
        m.addAction("Настроить…", lambda: self.edit_pad(pid))
        m.addAction("Остановить", lambda: self.ctx.engine.stop_pad(pid))
        m.addAction("Дублировать", lambda: self.duplicate(pid))
        mv = m.addMenu("Перенести в канал")
        for c in self.ctx.project.channels:
            a = mv.addAction(c.name, lambda c=c: self.move(pid, c.id))
            a.setCheckable(True)
            a.setChecked(c.id == pad.channel)
        m.addSeparator()
        m.addAction("← Левее", lambda: self.shift(pid, -1))
        m.addAction("→ Правее", lambda: self.shift(pid, 1))
        m.addSeparator()
        m.addAction("Удалить", lambda: self.delete(pid))
        m.exec(pos)

    def duplicate(self, pid):
        pad = self.ctx.project.pad(pid)
        c = copy.deepcopy(pad)
        c.id = new_id("pad")
        c.name = pad.name + " (копия)"
        c.autostart = False
        self.ctx.project.pads.insert(self.ctx.project.pads.index(pad) + 1, c)
        self.ctx.apply()
        self.rebuild()

    def move(self, pid, cid):
        pad = self.ctx.project.pad(pid)
        self.ctx.engine.stop_pad(pid, fade_ms=30)
        pad.channel = cid
        self.ctx.apply()
        self.rebuild()

    def shift(self, pid, d):
        pads = self.ctx.project.pads
        pad = self.ctx.project.pad(pid)
        i = pads.index(pad)
        j = i + d
        if 0 <= j < len(pads):
            pads[i], pads[j] = pads[j], pads[i]
            self.ctx.save_later()
            self.rebuild()

    def delete(self, pid):
        pad = self.ctx.project.pad(pid)
        if QMessageBox.question(self, "Удалить", f"Удалить звук «{pad.name}»?") != QMessageBox.Yes:
            return
        self.ctx.engine.stop_pad(pid, fade_ms=30)
        self.ctx.project.pads.remove(pad)
        self.ctx.project.bindings = [b for b in self.ctx.project.bindings if b.action.get("pad") != pid]
        self.ctx.apply()
        self.rebuild()


def _default_sfx_channel(proj):
    for c in proj.channels:
        if c.transition == "overlay" and "микро" not in c.name.lower():
            return c
    return proj.channels[0]


# ===================================================================== Микшер

class ChannelStrip(Panel):
    def __init__(self, ctx, ch: Channel, page, parent=None):
        super().__init__(ch.name, parent, margins=(10, 8, 10, 10))
        self.ctx = ctx
        self.ch = ch
        self.page = page
        self.setFixedWidth(236)
        lay = self.body
        lay.setSpacing(6)

        self.color = ColorButton(ch.color)
        self.color.setFixedSize(14, 14)
        self.color.colorChanged.connect(self._color)
        self.head_lay.insertWidget(0, self.color)
        menu_btn = QPushButton("≡")
        menu_btn.setFixedSize(26, 20)
        menu_btn.setStyleSheet(f"QPushButton {{ background: {theme.INK}; color: {theme.ACCENT}; border: none; padding: 0;"
                               f" font-weight: 700; }} QPushButton::menu-indicator {{ image: none; width: 0; }}")
        m = QMenu(menu_btn)
        m.addAction("Переименовать", self._rename)
        m.addAction("Остановить звуки канала", lambda: ctx.engine.stop_channel(ch.id))
        m.addSeparator()
        m.addAction("Удалить канал", self._delete)
        menu_btn.setMenu(m)
        self.head_lay.addWidget(menu_btn)

        mid = QHBoxLayout()
        self.slider = QSlider(Qt.Vertical)
        self.slider.setRange(0, 100)
        self.slider.setMinimumHeight(150)
        self.slider.valueChanged.connect(self._vol)
        self.meter = Meter(vertical=True)
        col = QVBoxLayout()
        self.vol_label = QLabel()
        self.vol_label.setObjectName("big")
        vcap = caption("Громкость")
        self.mute = QPushButton("ЗАГЛУШИТЬ")
        self.mute.setCheckable(True)
        self.mute.toggled.connect(self._mute)
        self.mute.setStyleSheet(f"QPushButton:checked {{ background: {theme.DANGER}; border-color: {theme.DANGER}; color: {theme.INK}; }}")
        b_stop = QPushButton("■ СТОП")
        b_stop.clicked.connect(lambda: ctx.engine.stop_channel(ch.id))
        col.addWidget(vcap)
        col.addWidget(self.vol_label)
        col.addStretch(1)
        col.addWidget(self.mute)
        col.addWidget(b_stop)
        mid.addWidget(self.slider)
        mid.addWidget(self.meter)
        mid.addSpacing(6)
        mid.addLayout(col, 1)
        lay.addLayout(mid)

        lay.addWidget(caption("Выходы"))
        self.out_btn = QPushButton()
        self.out_btn.setStyleSheet("text-align: left;")
        self.out_menu = QMenu(self.out_btn)
        self.out_menu.aboutToShow.connect(self._fill_out_menu)
        self.out_btn.setMenu(self.out_menu)
        lay.addWidget(self.out_btn)

        lay.addWidget(caption("Переход между звуками"))
        self.trans = QComboBox()
        for i, (k, v) in enumerate(TRANSITIONS.items()):
            self.trans.addItem(v, k)
            self.trans.setItemData(i, TRANSITION_HINTS[k], Qt.ToolTipRole)
        self.trans.setToolTip("\n".join(f"{TRANSITIONS[k]} — {h}" for k, h in TRANSITION_HINTS.items()))
        self.trans.currentIndexChanged.connect(self._trans)
        self.trans_ms = QSpinBox()
        self.trans_ms.setRange(0, 20000)
        self.trans_ms.setSingleStep(100)
        self.trans_ms.setSuffix(" мс")
        self.trans_ms.valueChanged.connect(self._trans)
        lay.addWidget(self.trans)
        lay.addWidget(self.trans_ms)

        lay.addWidget(caption("Эффекты"))
        self.fx_box = QVBoxLayout()
        self.fx_box.setSpacing(2)
        lay.addLayout(self.fx_box)
        b_fx = QPushButton("НАСТРОИТЬ ЭФФЕКТЫ…")
        b_fx.clicked.connect(self._effects)
        lay.addWidget(b_fx)
        lay.addStretch(1)
        self.refresh()

    def refresh(self):
        ch = self.ch
        self.title.setText(ch.name.upper())
        self.color.set_color(ch.color)
        for w, v in ((self.slider, int(round(ch.volume * 100))), (self.mute, ch.muted)):
            w.blockSignals(True)
            if w is self.slider:
                w.setValue(v)
            else:
                w.setChecked(v)
            w.blockSignals(False)
        self.vol_label.setText(f"{int(round(ch.volume * 100))}%")
        names = [self.ctx.project.output(o).name for o in ch.outputs if self.ctx.project.output(o)]
        if len(names) == len(self.ctx.project.outputs) and names:
            txt = "ВЕЗДЕ"
        else:
            txt = ", ".join(names) if names else "! НИКУДА"
        self.out_btn.setText("→ " + txt)
        self.out_btn.setToolTip(", ".join(names) or "Канал никуда не выводится")
        self.trans.blockSignals(True)
        self.trans.setCurrentIndex(max(0, self.trans.findData(ch.transition)))
        self.trans.blockSignals(False)
        self.trans_ms.blockSignals(True)
        self.trans_ms.setValue(ch.transition_ms)
        self.trans_ms.setEnabled(ch.transition in ("crossfade", "fade_out_in"))
        self.trans_ms.blockSignals(False)
        while self.fx_box.count():
            w = self.fx_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not ch.effects:
            l = QLabel("— НЕТ —")
            l.setObjectName("cap")
            self.fx_box.addWidget(l)
        for s in ch.effects:
            keys = [b.trigger.label() for b in self.ctx.project.bindings
                    if b.action.get("type") == "effect" and b.action.get("effect") == s.id]
            b = QPushButton(("■ " if s.enabled else "□ ") + effect_title(s.type).upper() + (f"  {keys[0]}" if keys else ""))
            b.setCheckable(True)
            b.setChecked(s.enabled)
            b.setStyleSheet("text-align: left; padding: 3px 8px;")
            b.toggled.connect(lambda on, b=b, s=s: b.setText(("■ " if on else "□ ") + b.text()[2:]))
            b.toggled.connect(lambda on, s=s: self._fx_toggle(s, on))
            self.fx_box.addWidget(b)

    def _vol(self, v):
        self.ctx.engine.set_volume(self.ch.id, v / 100)
        self.vol_label.setText(f"{v}%")

    def _mute(self, on):
        self.ctx.engine.set_muted(self.ch.id, on)

    def _fx_toggle(self, slot, on):
        self.ctx.engine.set_effect(self.ch.id, slot.id, on)

    def _trans(self):
        self.ch.transition = self.trans.currentData()
        self.ch.transition_ms = self.trans_ms.value()
        self.trans_ms.setEnabled(self.ch.transition in ("crossfade", "fade_out_in"))
        self.ctx.save_later()

    def _color(self, c):
        self.ch.color = c
        self.ctx.save_later()

    def _fill_out_menu(self):
        m = self.out_menu
        m.clear()
        a = m.addAction("Везде")
        a.triggered.connect(lambda: self._set_outputs([o.id for o in self.ctx.project.outputs]))
        m.addSeparator()
        for o in self.ctx.project.outputs:
            act = m.addAction(o.name + ("" if o.enabled else "  (выключен)"))
            act.setCheckable(True)
            act.setChecked(o.id in self.ch.outputs)
            act.toggled.connect(lambda on, oid=o.id: self._toggle_output(oid, on))
        m.addSeparator()
        m.addAction("Настроить выходы…", lambda: self.page.window().go_devices())

    def _toggle_output(self, oid, on):
        outs = [o for o in self.ch.outputs if o != oid]
        if on:
            outs.append(oid)
        self._set_outputs(outs)

    def _set_outputs(self, outs):
        order = [o.id for o in self.ctx.project.outputs]
        self.ch.outputs = sorted(set(outs), key=order.index)
        self.ctx.apply()
        self.refresh()

    def _rename(self):
        name, ok = QInputDialog.getText(self, "Канал", "Название:", text=self.ch.name)
        if ok and name.strip():
            self.ch.name = name.strip()
            self.ctx.save_later()
            self.page.changed_structure()

    def _delete(self):
        proj = self.ctx.project
        if len(proj.channels) <= 1:
            QMessageBox.information(self, "Канал", "Нужен хотя бы один канал.")
            return
        n = sum(1 for p in proj.pads if p.channel == self.ch.id)
        msg = f"Удалить канал «{self.ch.name}»?"
        if n:
            msg += f"\n{n} звук(ов) переедут в канал «{[c for c in proj.channels if c is not self.ch][0].name}»."
        if QMessageBox.question(self, "Удалить канал", msg) != QMessageBox.Yes:
            return
        proj.channels.remove(self.ch)
        proj.bindings = [b for b in proj.bindings if b.action.get("channel") != self.ch.id]
        self.ctx.apply()
        self.page.changed_structure()

    def _effects(self):
        EffectsDialog(self.ctx, self.ch, self).exec()
        self.refresh()


class MixerPage(QWidget):
    def __init__(self, ctx, on_structure, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.on_structure = on_structure
        self.setObjectName("page")
        self.strips: dict[str, ChannelStrip] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        bar = QHBoxLayout()
        info = QLabel("Каналы — это группы звуков со своей громкостью, эффектами, выходами и правилом перехода. "
                      "Например: «Музыка» с кроссфейдом, «Эффекты» с наложением, «Микрофон» с эхом.")
        info.setObjectName("muted")
        info.setWordWrap(True)
        b_add = QPushButton("+ КАНАЛ")
        b_add.setObjectName("primary")
        b_add.clicked.connect(self.add_channel)
        bar.addWidget(info, 1)
        bar.addWidget(b_add)
        lay.addLayout(bar)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        host = QWidget()
        host.setObjectName("page")
        self.row = QHBoxLayout(host)
        self.row.setContentsMargins(0, 6, 0, 0)
        self.row.setSpacing(6)
        self.row.setAlignment(Qt.AlignLeft)
        self.scroll.setWidget(host)
        lay.addWidget(self.scroll, 1)
        self.rebuild()

    def rebuild(self):
        for s in self.strips.values():
            s.setParent(None)
            s.deleteLater()
        self.strips.clear()
        while self.row.count():
            self.row.takeAt(0)
        for c in self.ctx.project.channels:
            s = ChannelStrip(self.ctx, c, self)
            self.strips[c.id] = s
            self.row.addWidget(s)
        self.row.addStretch(1)

    def refresh(self):
        for c in self.ctx.project.channels:
            s = self.strips.get(c.id)
            if s:
                s.ch = c
                s.refresh()

    def tick(self, peaks):
        for cid, s in self.strips.items():
            s.meter.set_level(peaks.get(cid, 0.0))

    def add_channel(self):
        name, ok = QInputDialog.getText(self, "Новый канал", "Название:", text="Новый канал")
        if not ok or not name.strip():
            return
        proj = self.ctx.project
        c = Channel(new_id("ch"), name.strip(), outputs=[o.id for o in proj.outputs],
                    color=theme.PAD_COLORS[len(proj.channels) % len(theme.PAD_COLORS)])
        proj.channels.append(c)
        self.ctx.apply()
        self.changed_structure()

    def changed_structure(self):
        self.rebuild()
        self.on_structure()


# ===================================================================== Клавиши

class BindingsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        bar = QHBoxLayout()
        info = QLabel("Все назначения клавиатуры и макропадов. Клавиши звуков удобнее назначать прямо в звуке (⚙), "
                      "эффектов — в окне эффектов канала. Здесь — всё вместе плюс громкость, стоп и т.д.")
        info.setObjectName("muted")
        info.setWordWrap(True)
        b_add = QPushButton("+ ПРИВЯЗКА")
        b_add.setObjectName("primary")
        b_add.clicked.connect(self.add)
        bar.addWidget(info, 1)
        bar.addWidget(b_add)
        lay.addLayout(bar)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["КЛАВИША / КНОПКА", "ДЕЙСТВИЕ"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.doubleClicked.connect(lambda _i: self.edit())
        lay.addWidget(self.table, 1)
        bot = QHBoxLayout()
        b_edit = QPushButton("ИЗМЕНИТЬ")
        b_edit.clicked.connect(self.edit)
        b_del = QPushButton("УДАЛИТЬ")
        b_del.clicked.connect(self.delete)
        self.status = QLabel()
        self.status.setObjectName("small")
        bot.addWidget(b_edit)
        bot.addWidget(b_del)
        bot.addStretch(1)
        bot.addWidget(self.status)
        lay.addLayout(bot)
        self.rebuild()

    def rebuild(self):
        bs = self.ctx.project.bindings
        self.table.setRowCount(len(bs))
        for i, b in enumerate(bs):
            t = QTableWidgetItem(" [ " + b.trigger.label().upper() + " ] ")
            t.setForeground(QColor(theme.ACCENT2 if b.trigger.type == "key" else theme.ACCENT))
            f = t.font()
            f.setBold(True)
            t.setFont(f)
            self.table.setItem(i, 0, t)
            self.table.setItem(i, 1, QTableWidgetItem(describe_action(self.ctx.project, b.action)))
            self.table.setRowHeight(i, 28)
        r = self.ctx.router
        parts = []
        parts.append("КЛАВИАТУРА: " + ("OK" if not r.keyboard_error else "НЕ РАБОТАЕТ — " + r.keyboard_error))
        ports = list(r.midi_ports)
        parts.append("MIDI: " + (", ".join(ports) if ports else "НЕТ УСТРОЙСТВ"))
        self.status.setText("   │   ".join(parts))

    def _selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.ctx.project.bindings):
            return self.ctx.project.bindings[row]
        return None

    def add(self):
        if BindingDialog(self.ctx, None, self).exec():
            self.window().refresh_all()

    def edit(self):
        b = self._selected()
        if b and BindingDialog(self.ctx, b, self).exec():
            self.window().refresh_all()

    def delete(self):
        b = self._selected()
        if b:
            self.ctx.project.bindings.remove(b)
            self.ctx.apply()
            self.window().refresh_all()


# ===================================================================== Устройства

class DevicesPage(QWidget):
    def __init__(self, ctx, on_structure, on_quick_settings, on_tabs_settings, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.on_structure = on_structure
        self.on_quick_settings = on_quick_settings
        self.on_tabs_settings = on_tabs_settings
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("page")
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(10, 10, 10, 10)
        self.lay.setSpacing(8)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self.rows = []
        self.rebuild()

    def _clear(self):
        def clear(layout):
            while layout.count():
                it = layout.takeAt(0)
                w = it.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
                elif it.layout():
                    clear(it.layout())
        clear(self.lay)

    def rebuild(self):
        self._clear()
        proj = self.ctx.project
        self.rows = []

        # баннер про VB-Cable
        if devices.sd is not None and devices.find_vb_cable() is None:
            ban = QFrame()
            ban.setObjectName("bannerWarn")
            bl = QHBoxLayout(ban)
            bl.setContentsMargins(16, 12, 16, 12)
            others = devices.list_virtual_cables()
            extra = (" Сейчас найдены другие виртуальные устройства: " + ", ".join(c["vendor"] for c in others[:3])
                     + " — их тоже можно использовать (кнопка «НАЙТИ КАБЕЛИ»).") if others else ""
            t = QLabel("<b>! VB-CABLE НЕ НАЙДЕН.</b> Чтобы звуки и музыка шли в Дискорд, установите бесплатный "
                       "виртуальный кабель VB-Audio Virtual Cable (установка от имени администратора, затем "
                       "перезагрузка), потом нажмите «Обновить устройства»." + extra)
            t.setWordWrap(True)
            b = QPushButton("СКАЧАТЬ VB-CABLE")
            b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://vb-audio.com/Cable/")))
            bl.setContentsMargins(12, 8, 12, 8)
            bl.addWidget(t, 1)
            bl.addWidget(b)
            self.lay.addWidget(ban)
        if devices.sd is None:
            ban = QFrame()
            ban.setObjectName("bannerWarn")
            bl = QHBoxLayout(ban)
            t = QLabel("Звуковая подсистема не загрузилась: " + devices.SD_ERROR)
            t.setWordWrap(True)
            bl.addWidget(t)
            self.lay.addWidget(ban)

        # выходы
        card = Panel("Выходы")
        cl = card.body
        cl.setSpacing(4)
        card.add_head_button("ОБНОВИТЬ УСТРОЙСТВА", self.rescan, "Если подключили наушники или установили VB-Cable")
        card.add_head_button("НАЙТИ КАБЕЛИ", self.find_cables,
                             "Добавить выходы для виртуальных кабелей: VB-Cable (в т.ч. A+B, C+D), Voicemeeter, VAC, "
                             "Voicemod, Wave Link, Sonar и других")
        card.add_head_button("+ ВЫХОД", self.add_output)
        sub = QLabel("Выход — это куда может идти звук: ваши наушники, колонки, Дискорд (через VB-Cable), "
                     "программа записи. В микшере каждому каналу выбирается набор выходов.")
        sub.setObjectName("small")
        sub.setWordWrap(True)
        cl.addWidget(sub)
        devs = devices.list_devices("output", proj.settings.show_all_hostapis)
        status = self.ctx.engine.device_status()
        for o in proj.outputs:
            if o.recorder:              # блоки записи настраиваются в «Коммутации»
                continue
            cl.addWidget(self._output_row(o, devs, status.get(o.id)))
        self.lay.addWidget(card)

        # виртуальные кабели: шпаргалка «куда звук → что выбрать в программе»
        cables = devices.list_virtual_cables()
        if cables:
            cp = Panel(f"Виртуальные кабели: {len(cables)}")
            g = QGridLayout()
            g.setHorizontalSpacing(14)
            g.setVerticalSpacing(3)
            for j, title in enumerate(("КАБЕЛЬ", "ПРОИЗВ.", "ЗВУК ИЗ МЯУКАЛО ИДЁТ В", "В ДИСКОРДЕ / OBS ВЫБРАТЬ МИКРОФОН",
                                       "ИСПОЛЬЗУЕТ ВЫХОД")):
                g.addWidget(caption(title), 0, j)
            for i, c in enumerate(cables, 1):
                users = ", ".join(o.name for o in proj.outputs if not o.bus and o.device == c["name"]) or "— не используется —"
                lab = QLabel(c["label"])
                lab.setStyleSheet(f"color: {theme.ACCENT}; font-weight: 700;")
                mic = QLabel(c["mic"] or "— не найден —")
                mic.setStyleSheet(f"color: {theme.ACCENT2 if c['mic'] else theme.DIM}; font-weight: 700;")
                mic.setTextInteractionFlags(Qt.TextSelectableByMouse)
                vend = QLabel(c["vendor"])
                vend.setObjectName("small")
                nm = QLabel(c["name"])
                nm.setObjectName("small")
                g.addWidget(lab, i, 0)
                g.addWidget(vend, i, 1)
                g.addWidget(nm, i, 2)
                g.addWidget(mic, i, 3)
                u = QLabel(users)
                u.setObjectName("small")
                g.addWidget(u, i, 4)
            g.setColumnStretch(4, 1)
            cp.body.addLayout(g)
            self.lay.addWidget(cp)

        # расширение браузера: звук отдельной вкладки
        from app.audio.tabaudio import HUB
        from app.extension import installed_dir
        ep = Panel("Расширение для браузера — звук отдельной вкладки")
        ext_dir = installed_dir()
        ep.add_head_button("ОТКРЫТЬ ПАПКУ", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ext_dir))),
                           "Папка, которую нужно выбрать в браузере кнопкой «Загрузить распакованное»")
        n_tabs = len(HUB.snapshot())
        st = QLabel(("● ПРИЁМ РАБОТАЕТ (127.0.0.1:%d)" % HUB.port if HUB.running else "● ПРИЁМ НЕ ЗАПУЩЕН: " + HUB.error)
                    + (f"   ·   ПОДКЛЮЧЕНО ВКЛАДОК: {n_tabs}" if HUB.running else ""))
        st.setObjectName("ok" if HUB.running else "err")
        ep.body.addWidget(st)
        how = QLabel("Windows не различает вкладки внутри браузера, поэтому для звука одной вкладки нужно маленькое "
                     "расширение (Chrome, Edge, Яндекс, Opera, Brave, Vivaldi; Firefox — нет).<br>"
                     "<b>Установка:</b> откройте <b>chrome://extensions</b> (в Edge — edge://extensions, в Яндексе — "
                     "browser://extensions) → включите «Режим разработчика» → «Загрузить распакованное» → выберите папку "
                     "(кнопка «ОТКРЫТЬ ПАПКУ»).<br><b>Использование:</b> на нужной вкладке нажмите значок Мяукало "
                     "(или Alt+Shift+M) — вкладка появится в «Коммутации» → «Источники» → «Вкладки браузера».")
        how.setWordWrap(True)
        how.setObjectName("small")
        ep.body.addWidget(how)
        path_l = QLabel(str(ext_dir))
        path_l.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_l.setStyleSheet(f"color: {theme.ACCENT2};")
        ep.body.addWidget(path_l)
        self.lay.addWidget(ep)

        # подсказка про Дискорд
        tip = QFrame()
        tip.setObjectName("banner")
        tl = QVBoxLayout(tip)
        tl.setContentsMargins(12, 8, 12, 8)
        tt = QLabel("<b>ДИСКОРД:</b> Дискорд → Настройки → Голос и видео → "
                    "<b>Устройство ввода: CABLE Output (VB-Audio Virtual Cable)</b>. "
                    "Микрофон тогда идёт через эту программу (звук «Микрофон» в канале «Микрофон»), "
                    "и друзья слышат голос вместе со звуками и музыкой. В Дискорде лучше выключить "
                    "шумоподавление Krisp и «эхоподавление», иначе музыка будет «резаться».<br><br>"
                    "<b>ЗАПИСЬ (OBS):</b> добавьте источник «Захват входного аудиопотока» → CABLE Output, "
                    "или заведите отдельный выход на другое устройство.")
        tt.setWordWrap(True)
        tl.addWidget(tt)
        self.lay.addWidget(tip)

        # настройки
        card2 = Panel("Настройки")
        g = QGridLayout()
        g.setVerticalSpacing(6)
        card2.body.addLayout(g)
        self.lat = QSpinBox()
        self.lat.setRange(20, 400)
        self.lat.setSuffix(" мс")
        self.lat.setValue(proj.settings.latency_ms)
        self.lat.valueChanged.connect(self._latency)
        g.addWidget(QLabel("Задержка буфера"), 1, 0)
        g.addWidget(self.lat, 1, 1)
        l = QLabel("Меньше — быстрее отклик, больше — надёжнее (если звук трещит, увеличьте).")
        l.setObjectName("small")
        g.addWidget(l, 1, 2)
        self.prev = QComboBox()
        for o in proj.outputs:
            if not o.recorder:
                self.prev.addItem(o.name, o.id)
        self.prev.setCurrentIndex(max(0, self.prev.findData(proj.settings.preview_output)))
        self.prev.currentIndexChanged.connect(self._preview_out)
        g.addWidget(QLabel("Прослушивание в редакторе"), 2, 0)
        g.addWidget(self.prev, 2, 1)
        self.all_apis = QCheckBox("Показывать устройства всех драйверов (MME, DirectSound, WDM-KS)")
        self.all_apis.setChecked(proj.settings.show_all_hostapis)
        self.all_apis.toggled.connect(self._all_apis)
        g.addWidget(self.all_apis, 3, 0, 1, 3)
        self.midi = QCheckBox("Слушать MIDI-устройства (макропады, контроллеры)")
        self.midi.setChecked(proj.settings.midi_enabled)
        self.midi.toggled.connect(self._midi)
        g.addWidget(self.midi, 4, 0, 1, 3)
        self.tray = QCheckBox("При закрытии окна сворачивать в трей (клавиши продолжают работать)")
        self.tray.setChecked(proj.settings.minimize_to_tray)
        self.tray.toggled.connect(self._tray)
        g.addWidget(self.tray, 5, 0, 1, 3)
        bq = QPushButton("НАСТРОИТЬ БЫСТРЫЙ МИКШЕР…")
        bq.setToolTip("Размер окна, какие каналы/выходы/переключатели/звуки показывать, прозрачность")
        bq.clicked.connect(self.on_quick_settings)
        g.addWidget(bq, 7, 0, 1, 2)
        bt = QPushButton("ВИДИМЫЕ ВКЛАДКИ…")
        bt.clicked.connect(self.on_tabs_settings)
        g.addWidget(bt, 8, 0, 1, 2)
        from app import winutil
        if winutil.autostart_supported():
            self.auto = QCheckBox("Запускать вместе с Windows (свёрнутым в трей) — нужно, если микрофон в Дискорде идёт через программу")
            self.auto.setChecked(winutil.get_autostart())
            self.auto.toggled.connect(self._autostart)
            g.addWidget(self.auto, 6, 0, 1, 3)
        self.lay.addWidget(card2)
        self.lay.addStretch(1)

    def _output_row(self, o: Output, devs, err):
        w = QFrame()
        w.setObjectName("outrow")
        w.setStyleSheet(f"QFrame#outrow {{ background: {theme.BG}; border: 1px solid {theme.BORDER}; }}")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)
        en = QCheckBox()
        en.setChecked(o.enabled)
        en.setToolTip("Включён")
        name = QLineEdit(o.name)
        name.setFixedWidth(170)
        from .routing import apply_device_choice, device_caption, fill_device_combo
        dev = QComboBox()
        dev.setMinimumWidth(300)
        fill_device_combo(dev, o, self.ctx.project.settings.show_all_hostapis)
        mic = device_caption(o)[1]
        if mic:
            dev.setToolTip(f"В Дискорде / OBS выберите микрофоном: «{mic}»")
        vol = QSlider(Qt.Horizontal)
        vol.setRange(0, 150)
        vol.setValue(int(o.volume * 100))
        vol.setFixedWidth(110)
        vol.setToolTip("Общая громкость этого выхода")
        vlab = QLabel(f"{int(o.volume * 100)}%")
        vlab.setFixedWidth(44)
        st = QLabel("[ВЫКЛ]" if not o.enabled else ("[ШИНА]" if o.bus else ("[СБОЙ]" if err else "[ OK ]")))
        st.setObjectName("ok" if (not err and o.enabled) else ("muted" if not o.enabled else "err"))
        if err:
            st.setToolTip(err)
        b_test = QPushButton("ТЕСТ")
        b_test.setToolTip("Проверить — короткий сигнал на это устройство")
        b_del = QPushButton("✕")
        b_del.setObjectName("ghost")
        b_del.setFixedWidth(32)

        def commit():
            o.enabled = en.isChecked()
            o.name = name.text().strip() or "Выход"
            apply_device_choice(o, dev.currentData())
            self.ctx.apply()
            self.on_structure()
            QTimer.singleShot(300, self.rebuild)

        def vol_changed(v):
            o.volume = v / 100
            vlab.setText(f"{v}%")
            self.ctx.engine.apply_project(self.ctx.project)
            self.ctx.save_later()

        def test():
            dn, ha = dev.currentData()
            if dn == "__bus__":
                return
            try:
                devices.play_test_tone(dn, ha)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Проверка", str(e))

        def delete():
            if QMessageBox.question(self, "Удалить выход", f"Удалить выход «{o.name}»?") == QMessageBox.Yes:
                self.ctx.project.outputs.remove(o)
                self.ctx.apply()
                self.on_structure()
                self.rebuild()

        en.toggled.connect(lambda _v: commit())
        name.editingFinished.connect(commit)
        dev.currentIndexChanged.connect(lambda _i: commit())
        vol.valueChanged.connect(vol_changed)
        b_test.clicked.connect(test)
        b_del.clicked.connect(delete)
        for x in (en, name, dev):
            lay.addWidget(x)
        lay.addWidget(vol)
        lay.addWidget(vlab)
        lay.addWidget(st)
        lay.addStretch(1)
        lay.addWidget(b_test)
        lay.addWidget(b_del)
        return w

    def add_output(self):
        proj = self.ctx.project
        o = Output(new_id("out"), f"Выход {len(proj.outputs) + 1}", enabled=True)
        d = devices.default_device("output")
        if d:
            o.device, o.hostapi = d["name"], d["hostapi"]
        proj.outputs.append(o)
        self.ctx.apply()
        self.on_structure()
        self.rebuild()

    def find_cables(self):
        from .cables_dialog import CablesDialog
        d = CablesDialog(self.ctx, self)
        if d.exec() and d.added:
            self.on_structure()
            self.rebuild()
            self.window()._set_msg(f"ДОБАВЛЕНО: {len(d.added)} — " + ", ".join(o.name for o in d.added))

    def rescan(self):
        self.ctx.engine.restart_devices()
        self.rebuild()
        self.on_structure()

    def _latency(self, v):
        self.ctx.project.settings.latency_ms = v
        self.ctx.apply()

    def _preview_out(self):
        self.ctx.project.settings.preview_output = self.prev.currentData() or ""
        self.ctx.save_later()

    def _all_apis(self, on):
        self.ctx.project.settings.show_all_hostapis = on
        self.ctx.save_later()
        QTimer.singleShot(0, self.rebuild)

    def _midi(self, on):
        self.ctx.project.settings.midi_enabled = on
        self.ctx.apply()

    def _autostart(self, on):
        from app import winutil
        try:
            winutil.set_autostart(on)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Автозапуск", str(e))

    def _tray(self, on):
        self.ctx.project.settings.minimize_to_tray = on
        self.ctx.save_later()
