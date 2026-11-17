"""Редактор звука: файл и обрезка, живой источник, режим, канал, выходы, горячие клавиши."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)

from app.audio import devices
from app.audio.decoder import AUDIO_FILTER, SR
from app.audio.sources import running_apps
from app.model import LIVE_TYPES, PAD_MODES, RETRIGGER, Binding, import_audio_file, new_id
from . import theme
from .widgets import CaptureButton, ColorButton, Panel, WaveformView, caption, confirm_trigger_conflict, fmt_time, hline


class PadEditor(QDialog):
    def __init__(self, ctx, pad, is_new=False, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.orig = pad
        self.pad = copy.deepcopy(pad)
        self.is_new = is_new
        self.bindings = [copy.deepcopy(b) for b in ctx.project.bindings_for_pad(pad.id)]
        self.removed_binding_ids = set()
        self.setWindowTitle("Новый звук" if is_new else f"Звук — {pad.name}")
        self.resize(860, 780)
        self._build()
        self._load()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    # ------------------------------------------------------------ ui
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        bar = QLabel("  РЕДАКТОР ЗВУКА" + ("  ·  НОВЫЙ" if self.is_new else ""))
        bar.setStyleSheet(f"background: {theme.ACCENT}; color: {theme.INK}; font-weight: 700; padding: 4px;")
        outer.addWidget(bar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("page")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # имя + цвет + тип
        top = QHBoxLayout()
        top.setSpacing(6)
        self.name = QLineEdit()
        self.name.setPlaceholderText("НАЗВАНИЕ")
        self.name.setStyleSheet("font-size: 12pt; font-weight: 700; padding: 5px 8px;")
        self.color = ColorButton(self.pad.color)
        self.color.setFixedSize(34, 34)
        self.kind = QComboBox()
        self.kind.addItem("ФАЙЛ", "file")
        self.kind.addItem("ЖИВОЙ ВХОД", "live")
        self.kind.setToolTip("Файл — звук с диска. Живой вход — микрофон, браузер или другая программа.")
        self.kind.setFixedWidth(160)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        top.addWidget(self.color)
        top.addWidget(self.name, 1)
        top.addWidget(self.kind)
        lay.addLayout(top)

        # ---- файл
        self.file_box = Panel("Источник: файл")
        fl = self.file_box.body
        fl.setSpacing(6)
        self.file_box.add_head_button("ВЫБРАТЬ ФАЙЛ…", self._choose_file)
        self.file_label = QLabel()
        self.file_label.setObjectName("small")
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.file_label.setWordWrap(True)
        self.file_label.setMinimumWidth(10)
        fl.addWidget(self.file_label)
        self.wave = WaveformView()
        self.wave.trimChanged.connect(self._wave_trim)
        self.wave.seekRequested.connect(self._seek_preview)
        fl.addWidget(self.wave)

        tr = QHBoxLayout()
        tr.setSpacing(6)
        self.wave.setToolTip("Тяните маркеры IN/OUT, чтобы обрезать. Колёсико — масштаб, правая кнопка — сдвиг, "
                             "клик по волне — слушать с этого места.")
        self.t_start = QDoubleSpinBox()
        self.t_end = QDoubleSpinBox()
        for s in (self.t_start, self.t_end):
            s.setDecimals(3)
            s.setSingleStep(0.01)
            s.setSuffix(" с")
            s.setRange(0, 36000)
            s.setFixedWidth(110)
        self.t_start.valueChanged.connect(self._spin_trim)
        self.t_end.valueChanged.connect(self._spin_trim)
        self.len_label = QLabel()
        self.len_label.setObjectName("muted")
        self.btn_play = QPushButton("► СЛУШАТЬ")
        self.btn_play.setObjectName("primary")
        self.btn_play.setFixedWidth(120)
        self.btn_play.clicked.connect(self._toggle_preview)
        b_zoom = QPushButton("К ВЫДЕЛЕНИЮ")
        b_zoom.clicked.connect(self.wave.zoom_to_selection)
        b_all = QPushButton("ВЕСЬ ФАЙЛ")
        b_all.clicked.connect(self.wave.zoom_all)
        b_reset = QPushButton("СБРОС")
        b_reset.setToolTip("Сбросить обрезку")
        b_reset.clicked.connect(self._reset_trim)
        tr.addWidget(self.btn_play)
        tr.addSpacing(6)
        tr.addWidget(caption("In"))
        tr.addWidget(self.t_start)
        tr.addWidget(caption("Out"))
        tr.addWidget(self.t_end)
        tr.addWidget(self.len_label)
        tr.addStretch(1)
        tr.addWidget(b_reset)
        fl.addLayout(tr)
        pr = QHBoxLayout()
        pr.setSpacing(6)
        pr.addWidget(caption("Громкость"))
        self.gain = QSlider(Qt.Horizontal)
        self.gain.setRange(-240, 120)
        self.gain_label = QLabel()
        self.gain_label.setFixedWidth(84)
        self.gain.valueChanged.connect(lambda v: self.gain_label.setText(f"{v / 10:+.1f} дБ"))
        self.gain_label.setText("+0.0 дБ")
        pr.addWidget(self.gain, 1)
        pr.addWidget(self.gain_label)
        pr.addSpacing(10)
        pr.addWidget(caption("Масштаб"))
        pr.addWidget(b_zoom)
        pr.addWidget(b_all)
        fl.addLayout(pr)
        lay.addWidget(self.file_box)

        # ---- живой вход
        self.live_box = Panel("Источник: живой вход")
        ll = QFormLayout()
        self.live_form = ll
        ll.setLabelAlignment(Qt.AlignRight)
        ll.setVerticalSpacing(6)
        self.live_box.body.addLayout(ll)
        self.src_type = QComboBox()
        for k, v in LIVE_TYPES.items():
            self.src_type.addItem(v, k)
        self.src_type.currentIndexChanged.connect(self._src_changed)
        ll.addRow(caption("Источник"), self.src_type)
        self.src_device = QComboBox()
        ll.addRow(caption("Устройство"), self.src_device)
        proc_row = QHBoxLayout()
        self.src_proc = QComboBox()
        self.src_proc.setEditable(True)
        b_ref = QPushButton("ОБНОВИТЬ")
        b_ref.setToolTip("Обновить список программ")
        b_ref.clicked.connect(self._fill_processes)
        proc_row.addWidget(self.src_proc, 1)
        proc_row.addWidget(b_ref)
        self.proc_row_w = QWidget()
        self.proc_row_w.setLayout(proc_row)
        proc_row.setContentsMargins(0, 0, 0, 0)
        ll.addRow(caption("Программа"), self.proc_row_w)
        self.live_hint = QLabel()
        self.live_hint.setObjectName("small")
        self.live_hint.setWordWrap(True)
        ll.addRow("", self.live_hint)
        self.autostart = QCheckBox("Включать сразу при запуске программы")
        ll.addRow("", self.autostart)
        g2 = QHBoxLayout()
        self.live_gain = QSlider(Qt.Horizontal)
        self.live_gain.setRange(-240, 120)
        self.live_gain_label = QLabel()
        self.live_gain_label.setFixedWidth(84)
        self.live_gain.valueChanged.connect(lambda v: self.live_gain_label.setText(f"{v / 10:+.1f} дБ"))
        self.live_gain_label.setText("+0.0 дБ")
        g2.addWidget(self.live_gain, 1)
        g2.addWidget(self.live_gain_label)
        ll.addRow(caption("Усиление"), g2)
        lay.addWidget(self.live_box)

        # ---- воспроизведение
        play = Panel("Воспроизведение")
        pl = QGridLayout()
        play.body.addLayout(pl)
        pl.setHorizontalSpacing(10)
        pl.setVerticalSpacing(6)
        self.channel = QComboBox()
        for c in self.ctx.project.channels:
            self.channel.addItem(c.name, c.id)
        self.channel.currentIndexChanged.connect(self._fill_outputs)
        self.mode = QComboBox()
        for k, v in PAD_MODES.items():
            self.mode.addItem(v, k)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.retrig = QComboBox()
        for k, v in RETRIGGER.items():
            self.retrig.addItem(v, k)
        self.loop = QCheckBox("Повторять по кругу")
        self.fade_in = QSpinBox()
        self.fade_out = QSpinBox()
        for s in (self.fade_in, self.fade_out):
            s.setRange(0, 20000)
            s.setSingleStep(50)
            s.setSuffix(" мс")
        pl.addWidget(caption("Канал"), 0, 0)
        pl.addWidget(self.channel, 0, 1)
        pl.addWidget(caption("Режим"), 0, 2)
        pl.addWidget(self.mode, 0, 3)
        self.retrig_label = caption("Повторное нажатие")
        pl.addWidget(self.retrig_label, 1, 0)
        pl.addWidget(self.retrig, 1, 1)
        pl.addWidget(self.loop, 1, 3)
        pl.addWidget(caption("Появление"), 2, 0)
        pl.addWidget(self.fade_in, 2, 1)
        pl.addWidget(caption("Затухание"), 2, 2)
        pl.addWidget(self.fade_out, 2, 3)
        pl.setColumnStretch(1, 1)
        pl.setColumnStretch(3, 1)
        hint = QLabel("Переходы между звуками одного канала (кроссфейд, наложение) — во вкладке «Микшер».")
        hint.setObjectName("cap")
        hint.setWordWrap(True)
        play.body.addWidget(hint)
        lay.addWidget(play)

        outs = Panel("Маршрут: куда идёт этот звук")
        self.outputs_box = QWidget()
        self.outputs_lay = QHBoxLayout(self.outputs_box)
        self.outputs_lay.setContentsMargins(0, 0, 0, 0)
        outs.body.addWidget(self.outputs_box)
        lay.addWidget(outs)

        # ---- клавиши
        keys = Panel("Клавиши и кнопки макропада")
        kl = keys.body
        self.chips = QWidget()
        self.chips_lay = QHBoxLayout(self.chips)
        self.chips_lay.setContentsMargins(0, 0, 0, 0)
        self.chips_lay.setSpacing(4)
        kl.addWidget(self.chips)
        self.capture = CaptureButton(self.ctx)
        self.capture.captured.connect(self._add_trigger)
        kl.addWidget(self.capture)
        if not self.is_new:
            extra = [b for b in self.ctx.project.bindings if b.action.get("pad") == self.orig.id
                     and b.action.get("type") in ("pad_random", "reverse")]
            if extra:
                from .texts import describe_action
                lab = QLabel("\n".join(f"{b.trigger.label().upper()} — {describe_action(self.ctx.project, b.action)}"
                                       for b in extra))
                lab.setObjectName("small")
                kl.addWidget(lab)
            row = QHBoxLayout()
            b_rnd = QPushButton("+ КЛАВИША: СЛУЧАЙНЫЕ ЭФФЕКТЫ")
            b_rnd.setToolTip("Каждое нажатие — этот звук через N случайных эффектов со случайными настройками")
            b_rnd.clicked.connect(lambda: self._extra_key({"type": "pad_random", "pad": self.orig.id, "count": 3,
                                                           "strength": 0.7, "pool": "all"}))
            b_rev = QPushButton("+ КЛАВИША: ЗАДОМ НАПЕРЁД")
            b_rev.setToolTip("Пока держите — этот звук играет задом наперёд с текущего места")
            b_rev.clicked.connect(lambda: self._extra_key({"type": "reverse", "scope": "pad", "pad": self.orig.id,
                                                           "mode": "hold"}))
            b_try = QPushButton("▶ ПОПРОБОВАТЬ")
            b_try.setToolTip("Сыграть один раз с тремя случайными эффектами")
            b_try.clicked.connect(lambda: self.ctx.engine.play_random_fx(self.orig.id, 3, 0.7))
            row.addWidget(b_rnd)
            row.addWidget(b_rev)
            row.addWidget(b_try)
            row.addStretch(1)
            kl.addLayout(row)
        if self.ctx.router.keyboard_error:
            w = QLabel("! Глобальные клавиши сейчас недоступны: " + self.ctx.router.keyboard_error)
            w.setObjectName("err")
            w.setWordWrap(True)
            kl.addWidget(w)
        lay.addWidget(keys)
        lay.addStretch(1)

        # ---- кнопки
        foot = QWidget()
        foot.setObjectName("foot")
        foot.setAttribute(Qt.WA_StyledBackground, True)
        foot.setStyleSheet(f"QWidget#foot {{ background: {theme.BG}; border-top: 2px solid {theme.ACCENT}; }}")
        bottom = QHBoxLayout(foot)
        bottom.setContentsMargins(10, 8, 10, 8)
        if not self.is_new:
            b_del = QPushButton("УДАЛИТЬ ЗВУК")
            b_del.setStyleSheet(f"QPushButton {{ color:{theme.DANGER}; border-color: {theme.DANGER}; background: {theme.BG}; }}"
                                f"QPushButton:hover {{ background: {theme.DANGER}; color: {theme.INK}; }}")
            b_del.clicked.connect(self._delete)
            bottom.addWidget(b_del)
        bottom.addStretch(1)
        b_cancel = QPushButton("ОТМЕНА")
        b_cancel.clicked.connect(self.reject)
        b_ok = QPushButton("СОХРАНИТЬ")
        b_ok.setObjectName("primary")
        b_ok.clicked.connect(self._save)
        bottom.addWidget(b_cancel)
        bottom.addWidget(b_ok)
        outer.addWidget(foot)

    # ------------------------------------------------------------ load
    def _load(self):
        p = self.pad
        self.name.setText(p.name)
        self.kind.setCurrentIndex(0 if p.kind == "file" else 1)
        self._set_combo(self.channel, p.channel)
        self._set_combo(self.mode, p.mode)
        self._set_combo(self.retrig, p.retrigger)
        self.loop.setChecked(p.loop)
        self.fade_in.setValue(p.fade_in_ms)
        self.fade_out.setValue(p.fade_out_ms)
        self.gain.setValue(int(p.gain_db * 10))
        self.live_gain.setValue(int(p.gain_db * 10))
        self.autostart.setChecked(p.autostart)
        self._set_combo(self.src_type, p.source.type)
        self._src_changed()
        self._kind_changed()
        self._mode_changed()
        self._fill_outputs()
        self._refresh_chips()
        self._load_wave()

    def _load_wave(self):
        p = self.pad
        self.file_label.setText(p.file or "Файл не выбран")
        if p.kind != "file" or not p.file:
            self.wave.set_data(None)
            return
        data = self.ctx.engine.cache.get(p.file)
        if data is None:
            try:
                data = self.ctx.engine.cache.load_now(p.file)
            except Exception as e:  # noqa: BLE001
                self.file_label.setText(f"Не удалось открыть: {e}")
                self.wave.set_data(None)
                return
        self.wave.set_data(data, p.trim_start, p.trim_end)
        dur = len(data) / SR
        for s in (self.t_start, self.t_end):
            s.blockSignals(True)
            s.setMaximum(dur)
        self.t_start.setValue(p.trim_start)
        self.t_end.setValue(p.trim_end if p.trim_end > 0 else dur)
        for s in (self.t_start, self.t_end):
            s.blockSignals(False)
        self._update_len()

    @staticmethod
    def _set_combo(combo, data):
        i = combo.findData(data)
        if i >= 0:
            combo.setCurrentIndex(i)

    # ------------------------------------------------------------ handlers
    def _kind_changed(self):
        live = self.kind.currentData() == "live"
        self.file_box.setVisible(not live)
        self.live_box.setVisible(live)

    def _mode_changed(self):
        one = self.mode.currentData() == "oneshot"
        self.retrig.setVisible(one)
        self.retrig_label.setVisible(one)

    def _src_changed(self):
        t = self.src_type.currentData()
        self.src_device.clear()
        if t == "tab":
            from app.audio.tabaudio import HUB
            self.live_form.setRowVisible(self.src_device, True)
            self.live_form.setRowVisible(self.proc_row_w, False)
            cur = (self.pad.source.device, self.pad.source.process)
            tabs = HUB.snapshot()
            for it in tabs:
                self.src_device.addItem(f"{it['label']}  [{it['sub']}]", (it["device"], it["process"]))
            if cur[0] and not any(it["device"] == cur[0] for it in tabs):
                self.src_device.addItem(f"(не подключена) сайт: {cur[1] or '?'}", cur)
            for i in range(self.src_device.count()):
                if self.src_device.itemData(i)[0] == cur[0]:
                    self.src_device.setCurrentIndex(i)
            if not self.src_device.count():
                self.src_device.addItem("— нет подключённых вкладок —", ("", ""))
            self.live_hint.setText("Звук одной вкладки браузера (Chrome, Edge, Яндекс, Opera, Brave). Нужно расширение "
                                   "«Мяукало — звук вкладки» («Устройства» → «Расширение для браузера»): откройте "
                                   "вкладку и нажмите значок расширения. Если вкладку закрыть и открыть снова, источник "
                                   "сам подхватит вкладку того же сайта.")
            return
        if t == "process":
            self.live_form.setRowVisible(self.src_device, False)
            self.live_form.setRowVisible(self.proc_row_w, True)
            self._fill_processes()
            self.live_hint.setText(
                "Захватывается звук только этой программы (Windows 10 2004+ / 11). Например, вкладка YouTube "
                "в браузере. Вы и так слышите браузер в своих колонках, поэтому в блоке «Куда идёт этот звук» "
                "по умолчанию стоит только Дискорд/кабель.")
        else:
            self.live_form.setRowVisible(self.src_device, True)
            self.live_form.setRowVisible(self.proc_row_w, False)
            kind = "input" if t == "input" else "output"
            self.src_device.addItem("Устройство по умолчанию", ("", ""))
            for d in devices.list_devices(kind, self.ctx.project.settings.show_all_hostapis):
                label = d["name"] + (f"  [{d['hostapi']}]" if self.ctx.project.settings.show_all_hostapis else "")
                self.src_device.addItem(label, (d["name"], d["hostapi"]))
            cur = (self.pad.source.device, self.pad.source.hostapi)
            for i in range(self.src_device.count()):
                dn, ha = self.src_device.itemData(i)
                if dn == cur[0] and (not cur[1] or ha == cur[1] or not ha):
                    self.src_device.setCurrentIndex(i)
                    break
            if t == "input":
                self.live_hint.setText("Микрофон или другой вход. Чтобы друзья в Дискорде слышали микрофон вместе со "
                                       "звуками, направьте канал микрофона в VB-Cable и выберите «CABLE Output» "
                                       "микрофоном в Дискорде.")
            else:
                self.live_hint.setText("Захватывается всё, что играет на выбранном устройстве вывода.")

    def _fill_processes(self):
        cur = self.src_proc.currentText() or self.pad.source.process or "chrome.exe"
        self.src_proc.clear()
        apps = running_apps()
        if cur and cur not in apps:
            apps.insert(0, cur)
        self.src_proc.addItems(apps)
        self.src_proc.setCurrentText(cur)

    def _set_own(self, on):
        self.pad.route_own = on
        if on and not self.pad.outputs:
            ch = self.ctx.project.channel(self.channel.currentData())
            self.pad.outputs = [o for o in (ch.outputs if ch else []) if o not in self.pad.exclude_outputs]
        self._fill_outputs()

    def _fill_outputs(self):
        while self.outputs_lay.count():
            w = self.outputs_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        ch = self.ctx.project.channel(self.channel.currentData())
        self.out_checks = {}
        if self.pad.route_own:
            tag = QLabel("СВОЯ РАЗВОДКА")
            tag.setStyleSheet(f"background: {theme.ACCENT2}; color: {theme.INK}; font-weight: 700; padding: 2px 6px;")
            self.outputs_lay.addWidget(tag)
            for o in self.ctx.project.outputs:
                cb = QCheckBox(o.name)
                cb.setChecked(o.id in self.pad.outputs)
                self.out_checks[o.id] = cb
                self.outputs_lay.addWidget(cb)
            self.outputs_lay.addStretch(1)
            back = QPushButton("КАК У ГРУППЫ")
            back.setToolTip("Снова идти туда же, куда весь канал")
            back.clicked.connect(lambda: self._set_own(False))
            self.outputs_lay.addWidget(back)
            return
        if ch is None or not ch.outputs:
            lab = QLabel("! У канала не выбрано ни одного выхода — настройте во вкладке «Микшер».")
            lab.setObjectName("err")
            self.outputs_lay.addWidget(lab)
            return
        for oid in ch.outputs:
            o = self.ctx.project.output(oid)
            if not o:
                continue
            cb = QCheckBox(o.name)
            cb.setChecked(oid not in self.pad.exclude_outputs)
            self.out_checks[oid] = cb
            self.outputs_lay.addWidget(cb)
        self.outputs_lay.addStretch(1)
        own = QPushButton("СВОЯ РАЗВОДКА…")
        own.setToolTip("Этот звук пойдёт только туда, куда отметите, — независимо от проводов канала")
        own.clicked.connect(lambda: self._set_own(True))
        self.outputs_lay.addWidget(own)

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите звук", "", AUDIO_FILTER)
        if not path:
            return
        self.pad.file = path
        self.pad.trim_start = 0.0
        self.pad.trim_end = 0.0
        if not self.name.text().strip() or self.name.text() == "Новый звук":
            from pathlib import Path
            self.name.setText(Path(path).stem[:40])
        self._load_wave()

    def _wave_trim(self, s, e):
        for sp, v in ((self.t_start, s), (self.t_end, e)):
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)
        self._update_len()

    def _spin_trim(self):
        s, e = self.t_start.value(), self.t_end.value()
        if e <= s:
            e = s + 0.01
        self.wave.set_trim(s, e)
        self._update_len()

    def _reset_trim(self):
        if self.wave.total:
            self.wave.set_trim(0, self.wave.total / SR)
            self._wave_trim(0, self.wave.total / SR)

    def _update_len(self):
        self.len_label.setText(f"длина {fmt_time(self.t_end.value() - self.t_start.value())}")

    # ------------------------------------------------------------ preview
    def _toggle_preview(self):
        if self.ctx.engine.preview_pos() is not None:
            self.ctx.engine.stop_preview()
        else:
            self._seek_preview(self.wave.start / SR)

    def _seek_preview(self, sec):
        if self.wave.data is None:
            return
        start = int(sec * SR)
        if not (self.wave.start <= start < self.wave.end):
            start = self.wave.start
        self.ctx.engine.preview(self.wave.data, start, self.wave.end, self.gain.value() / 10)

    def _tick(self):
        pos = self.ctx.engine.preview_pos()
        self.wave.play_pos = pos
        self.btn_play.setText("■ СТОП" if pos is not None else "► СЛУШАТЬ")
        self.wave.update()

    # ------------------------------------------------------------ hotkeys
    def _refresh_chips(self):
        while self.chips_lay.count():
            w = self.chips_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not self.bindings:
            lab = QLabel("— НЕ НАЗНАЧЕНО —")
            lab.setObjectName("cap")
            self.chips_lay.addWidget(lab)
        for b in self.bindings:
            chip = QPushButton(f"{b.trigger.label().upper()}  ✕")
            chip.setObjectName("chip")
            chip.setToolTip("Убрать")
            chip.clicked.connect(lambda _=False, b=b: self._remove_trigger(b))
            self.chips_lay.addWidget(chip)
        self.chips_lay.addStretch(1)

    def _extra_key(self, preset):
        from .binding_dialog import BindingDialog
        BindingDialog(self.ctx, None, self, preset=preset).exec()

    def _add_trigger(self, trig):
        if any(b.trigger.key() == trig.key() for b in self.bindings):
            return
        ignore = {b.id for b in self.bindings}
        if not confirm_trigger_conflict(self, self.ctx, trig, ignore):
            return
        self.bindings.append(Binding(new_id("bind"), trig, {"type": "pad", "pad": self.pad.id}))
        self._refresh_chips()

    def _remove_trigger(self, b):
        self.bindings.remove(b)
        self.removed_binding_ids.add(b.id)
        self._refresh_chips()

    # ------------------------------------------------------------ save
    def _collect(self):
        p = self.pad
        p.name = self.name.text().strip() or "Звук"
        p.color = self.color.color
        p.kind = self.kind.currentData()
        p.channel = self.channel.currentData()
        p.mode = self.mode.currentData()
        p.retrigger = self.retrig.currentData()
        p.loop = self.loop.isChecked()
        p.fade_in_ms = self.fade_in.value()
        p.fade_out_ms = self.fade_out.value()
        checks = getattr(self, "out_checks", {})
        if p.route_own:
            p.outputs = [oid for oid, cb in checks.items() if cb.isChecked()]
        else:
            p.exclude_outputs = [oid for oid, cb in checks.items() if not cb.isChecked()]
        if p.kind == "file":
            p.gain_db = self.gain.value() / 10
            if self.wave.total:
                p.trim_start = round(self.wave.start / SR, 4)
                p.trim_end = 0.0 if self.wave.end >= self.wave.total else round(self.wave.end / SR, 4)
        else:
            p.gain_db = self.live_gain.value() / 10
            p.source.type = self.src_type.currentData()
            p.autostart = self.autostart.isChecked()
            if p.source.type == "process":
                p.source.process = self.src_proc.currentText().strip()
            elif p.source.type == "tab":
                dn, host = self.src_device.currentData() or ("", "")
                p.source.device, p.source.process = dn, host
            else:
                dn, ha = self.src_device.currentData() or ("", "")
                p.source.device, p.source.hostapi = dn, ha

    def _save(self):
        self._collect()
        p = self.pad
        if p.kind == "file":
            if not p.file:
                QMessageBox.warning(self, "Нет файла", "Выберите аудиофайл.")
                return
            try:
                new_path = import_audio_file(p.file)
                if new_path != p.file:
                    data = self.ctx.engine.cache.get(p.file)
                    p.file = new_path
                    if data is not None:
                        self.ctx.engine.cache._data[new_path] = data
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Файл", f"Не удалось скопировать файл в библиотеку: {e}")
        elif p.source.type == "process" and not p.source.process:
            QMessageBox.warning(self, "Программа", "Укажите программу, звук которой захватывать.")
            return
        proj = self.ctx.project
        # если сменился источник — перезапустить живой звук
        if not self.is_new and self.orig.kind == "live":
            self.ctx.engine.stop_pad(self.orig.id, fade_ms=50)
        if self.is_new:
            proj.pads.append(p)
        else:
            i = proj.pads.index(self.orig)
            proj.pads[i] = p
        proj.bindings = [b for b in proj.bindings
                         if not (b.action.get("type") == "pad" and b.action.get("pad") == p.id)]
        proj.bindings.extend(self.bindings)
        self.ctx.engine.stop_preview()
        self.ctx.apply(devices=False)
        self.accept()

    def _delete(self):
        if QMessageBox.question(self, "Удалить", f"Удалить звук «{self.orig.name}»?") != QMessageBox.Yes:
            return
        proj = self.ctx.project
        self.ctx.engine.stop_pad(self.orig.id, fade_ms=50)
        proj.pads.remove(self.orig)
        proj.bindings = [b for b in proj.bindings if b.action.get("pad") != self.orig.id]
        self.ctx.engine.stop_preview()
        self.ctx.apply(devices=False)
        self.accept()

    def reject(self):
        self.ctx.engine.stop_preview()
        super().reject()

    def closeEvent(self, e):
        self.ctx.engine.stop_preview()
        super().closeEvent(e)
