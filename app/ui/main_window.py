"""Главное окно «Мяукало»."""
from __future__ import annotations

from PySide6.QtCore import QDateTime, Qt, QTimer
from PySide6.QtGui import QFont, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMainWindow, QMenu, QPushButton,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from app.model import resource_path
from . import theme
from .editor_page import EditorPage
from .pages import BindingsPage, DevicesPage, MixerPage, SoundsPage
from .player_page import PlayerPage, collect_audio
from .quick_mixer import QuickMixer
from .routing import RoutingPage
from .widgets import FolderTabs, ui_font

APP_TITLE = "Мяукало"
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".webm", ".mp4", ".wma", ".aiff", ".aif"}
MONTHS = ["ЯНВ", "ФЕВ", "МАР", "АПР", "МАЙ", "ИЮН", "ИЮЛ", "АВГ", "СЕН", "ОКТ", "НОЯ", "ДЕК"]
DAYS = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]


class MainWindow(QMainWindow):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._really_quit = False
        self.setWindowTitle(APP_TITLE)
        icon_path = resource_path("assets/icon.png")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1180, 760)
        self.setMinimumSize(880, 560)
        self.setAcceptDrops(True)

        central = QWidget()
        central.setObjectName("page")
        lay = QVBoxLayout(central)
        lay.setContentsMargins(10, 8, 10, 0)
        lay.setSpacing(6)

        # ---- шапка
        header = QHBoxLayout()
        header.setSpacing(10)
        emblem = QLabel()
        pm = QPixmap(str(resource_path("assets/emblem.png")))
        if not pm.isNull():
            emblem.setPixmap(pm.scaledToHeight(34, Qt.SmoothTransformation))
        title = QLabel("МЯУКАЛО")
        tf = ui_font(16, QFont.Bold)
        tf.setLetterSpacing(QFont.AbsoluteSpacing, 4)
        title.setFont(tf)
        title.setStyleSheet(f"color: {theme.ACCENT};")
        sub = QLabel("MIAUKALO")
        sub.setStyleSheet(f"color: {theme.MUTED}; font-size: 8.5pt; font-weight: 600; padding-top: 6px;")
        self.clock = QLabel()
        self.clock.setStyleSheet(f"color: {theme.MUTED}; font-size: 9pt; font-weight: 600;")
        header.addWidget(emblem)
        header.addWidget(title)
        header.addWidget(sub)
        header.addStretch(1)
        header.addWidget(self.clock)
        lay.addLayout(header)

        # ---- вкладки-папки
        self.tabs = FolderTabs()
        from .drum_page import DrumPage, ensure_drum_channel
        had = ctx.project.channel(ctx.project.drum.get("channel", "")) is not None
        ensure_drum_channel(ctx)
        if not had:
            ctx.apply()
        self.sounds = SoundsPage(ctx)
        self.mixer = MixerPage(ctx, self.structure_changed)
        self.routing = RoutingPage(ctx, self.routing_changed)
        self.bindings = BindingsPage(ctx)
        self.devices = DevicesPage(ctx, self.structure_changed, self.open_quick_settings, self.open_tabs_settings)
        self.player = PlayerPage(ctx)
        self.drums = DrumPage(ctx, self.structure_changed)
        self.editor = EditorPage(ctx)
        self.editor.idle.connect(self._finish_close)
        self._close_after_editor = False
        self._tab_pages = [
            ("sounds", self.sounds, "Звуки", None),
            ("player", self.player, "Плеер", None),
            ("drums", self.drums, "Дрампад", None),
            ("mixer", self.mixer, "Микшер", None),
            ("routing", self.routing, "Коммутация", None),
            ("bindings", self.bindings, "Клавиши и макропады", "Клавиши"),
            ("devices", self.devices, "Устройства", None),
            ("editor", self.editor, "Аудиоредактор", "Редактор"),
        ]
        for _, page, title, label in self._tab_pages:
            self.tabs.addTab(page, title, label)
        self._apply_tabs()
        self.tabs.currentChanged.connect(self._tab_changed)
        lay.addWidget(self.tabs, 1)
        for i in range(len(self._tab_pages)):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, lambda i=i: self.tabs.setCurrentIndex(i))

        # ---- нижняя панель софт-клавиш
        soft = QWidget()
        soft.setObjectName("softbar")
        soft.setAttribute(Qt.WA_StyledBackground, True)
        sl = QHBoxLayout(soft)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        stop = QPushButton("■ СТОП ВСЁ")
        stop.setObjectName("softkeyDanger")
        stop.setCursor(Qt.PointingHandCursor)
        stop.clicked.connect(lambda: ctx.engine.stop_all())
        self.hotkeys_btn = QPushButton()
        self.hotkeys_btn.setObjectName("softkey")
        self.hotkeys_btn.setCheckable(True)
        self.hotkeys_btn.setCursor(Qt.PointingHandCursor)
        self.hotkeys_btn.setToolTip("Временно отключить все горячие клавиши (например, чтобы попечатать)")
        self.hotkeys_btn.setChecked(ctx.project.settings.hotkeys_enabled)
        self._hotkeys_text(ctx.project.settings.hotkeys_enabled)
        self.hotkeys_btn.toggled.connect(self._toggle_hotkeys)
        self.msg = QLabel("ГОТОВ")
        self.msg.setObjectName("softmsg")
        self.msg_timer = QTimer(self)
        self.msg_timer.setSingleShot(True)
        self.msg_timer.timeout.connect(lambda: self._set_msg("ГОТОВ"))
        self.activity = QLabel("ВВОД: —")
        self.activity.setObjectName("softcell")
        self.activity.setMinimumWidth(170)
        self.activity_timer = QTimer(self)
        self.activity_timer.setSingleShot(True)
        self.activity_timer.timeout.connect(lambda: self.activity.setText("ВВОД: —"))
        self.midi_label = QLabel()
        self.midi_label.setObjectName("softcell")
        self.load_label = QLabel()
        self.load_label.setObjectName("softcell")
        self.load_label.setMinimumWidth(90)
        quick = QPushButton("БЫСТРЫЙ МИКШЕР")
        quick.setObjectName("softkey")
        quick.setCursor(Qt.PointingHandCursor)
        quick.setToolTip("Маленькое окно поверх всего — громкости, эффекты и звуки с подписанными клавишами")
        quick.clicked.connect(self.toggle_quick)
        for w in (stop, self.hotkeys_btn, quick):
            sl.addWidget(w)
        sl.addWidget(self.msg, 1)
        for w in (self.activity, self.midi_label, self.load_label):
            sl.addWidget(w)
        lay.addWidget(soft)
        self.setCentralWidget(central)
        self._update_midi()

        # сигналы
        b = ctx.bridge
        b.changed.connect(self._engine_changed)
        b.error.connect(self._error)
        b.activity.connect(self._activity)
        b.midi_ports.connect(lambda _p: (self.bindings.rebuild(), self._update_midi()))
        b.ui_action.connect(self._ui_action)
        b.recorded.connect(self._recorded)
        b.info.connect(self._info)
        self.quick = QuickMixer(ctx, self.show_normal)

        self._refresh_pending = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._clock)
        self.clock_timer.start(1000)
        self._clock()
        self._setup_tray()

    # ------------------------------------------------------------ tray
    def _setup_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip(self.windowTitle())
        self.tray_menu = m = QMenu()        # ссылку держим сами: трей меню не «владеет»
        m.addAction("Открыть", self.show_normal)
        m.addAction("Быстрый микшер", self.toggle_quick)
        m.addAction("Стоп всё", lambda: self.ctx.engine.stop_all())
        m.addSeparator()
        m.addAction("Выход", self.quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.show_normal() if r in (
            QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    def show_normal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit(self):
        self._really_quit = True
        app = QApplication.instance()
        # Закрываем модальные окна, чтобы завершить главный цикл.
        for _ in range(5):
            m = app.activeModalWidget()
            if m is None:
                break
            m.reject() if hasattr(m, "reject") else m.close()
        if hasattr(self, "quick"):
            self.quick.hide()
        self.close()

    def shutdown(self):
        """Остановить таймеры и фоновые сканеры до того, как начнут уничтожаться объекты."""
        for t in (getattr(self, "timer", None), getattr(self, "clock_timer", None)):
            if t:
                t.stop()
        try:
            self.routing.palette.scanner.stop()
        except Exception:
            pass
        if self.tray:
            self.tray.hide()

    def closeEvent(self, e):
        if self._really_quit or not (self.ctx.project.settings.minimize_to_tray and self.tray):
            self.quick.hide()
        if not self._really_quit and self.ctx.project.settings.minimize_to_tray and self.tray:
            e.ignore()
            self.hide()
            if not getattr(self, "_tray_told", False):
                self._tray_told = True
                self.tray.showMessage(f"{APP_TITLE} работает в фоне",
                                      "Горячие клавиши продолжают работать. Выход — через значок в трее.",
                                      QSystemTrayIcon.Information, 4000)
            return
        if self.editor.finish_work():
            self._close_after_editor = True
            e.ignore()
            return
        self.ctx.flush()
        e.accept()
        QApplication.quit()

    def _finish_close(self):
        if self._close_after_editor:
            self._close_after_editor = False
            self.close()

    def _apply_tabs(self):
        hidden = self.ctx.project.ui.get("hidden_tabs") or []
        self.tabs.setVisibleTabs([page for key, page, _, _ in self._tab_pages
                                  if key == "devices" or key not in hidden])

    def open_tabs_settings(self):
        self.quick.hide()
        dialog = QDialog(self)
        dialog.setWindowTitle("Видимые вкладки")
        lay = QVBoxLayout(dialog)
        checks = {}
        for key, page, title, _ in self._tab_pages:
            cb = QCheckBox(title)
            cb.setChecked(self.tabs.indexOf(page) >= 0)
            cb.setEnabled(key != "devices")
            checks[key] = cb
            lay.addWidget(cb)
        buttons = QDialogButtonBox()
        buttons.addButton("Применить", QDialogButtonBox.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        lay.addWidget(buttons)
        if dialog.exec():
            self.ctx.project.ui["hidden_tabs"] = [key for key, cb in checks.items() if not cb.isChecked()]
            self._apply_tabs()
            self.ctx.save_later()
        dialog.deleteLater()

    def open_quick_settings(self):
        self.quick.open_settings(self)

    # ------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        from pathlib import Path
        raw = [u.toLocalFile() for u in e.mimeData().urls()]
        if self.tabs.currentWidget() is self.editor:
            self.editor.import_paths(collect_audio(raw))
            return
        if self.tabs.currentWidget() is self.player:          # на вкладке плеера — в плейлист (и папки тоже)
            self.player.add_paths(raw)
            return
        paths = [p for p in raw if Path(p).suffix.lower() in AUDIO_EXT]
        if not paths and self.tabs.indexOf(self.player) >= 0 and collect_audio(raw):
            self.tabs.setCurrentWidget(self.player)
            self.player.add_paths(raw)
            return
        if paths:
            if self.tabs.indexOf(self.sounds) < 0:
                return
            self.tabs.setCurrentWidget(self.sounds)
            self.sounds.add_files(paths)

    # ------------------------------------------------------------ updates
    def toggle_quick(self):
        self.quick.toggle()

    def _ui_action(self, name):
        if name == "quick_mixer":
            self.toggle_quick()

    def _info(self, text):
        self._set_msg(text.upper())
        self.msg.setToolTip(text)
        self.msg_timer.start(8000)

    def _recorded(self, ev: dict):
        """Блок записи закончил: короткий звук — в саундпад, длинная запись — сообщение с файлом."""
        from pathlib import Path
        from app.model import pretty_combo
        name = ev.get("name", "Запись")
        if ev.get("kind") == "error":
            self._error(f"Запись «{name}»: {ev.get('error')}")
            return
        path = ev.get("path", "")
        secs = float(ev.get("seconds", 0.0))
        if not ev.get("add_pad"):
            self._set_msg(f"● ЗАПИСАНО {secs:.0f} С → {Path(path).name.upper()}")
            self.msg.setToolTip(path)
            self.msg_timer.start(12000)
            return
        pad, combo = add_recorded_pad(self.ctx, ev)
        self.sounds.fill_filter()
        self.sounds.rebuild()
        self.bindings.rebuild()
        if self.quick.isVisible():
            self.quick.build()
        key = f" · {pretty_combo(combo).upper()}" if combo else ""
        self._set_msg(f"● ЗАПИСАНО «{pad.name.upper()}» ({secs:.1f} С) → ЗВУКИ{key}")
        self.msg_timer.start(12000)

    def routing_changed(self):
        """В «Коммутации» поменялись провода или добавились источники — обновить остальные вкладки."""
        self.drums.refresh_structure()
        self.mixer.rebuild()
        self.sounds.fill_filter()
        self.sounds.rebuild()

    def go_devices(self):
        self.tabs.setCurrentWidget(self.devices)

    def _tab_changed(self, _i):
        w = self.tabs.currentWidget()
        if w is self.bindings:
            self.bindings.rebuild()
        elif w is self.devices:
            self.devices.rebuild()
        elif w is self.mixer:
            self.mixer.refresh()
        elif w is self.routing:
            self.routing.rebuild()
        elif w is self.player:
            self.player.refresh()
        elif w is self.editor:
            self.editor.refresh_sources()

    def structure_changed(self):
        """Каналы/выходы добавлены или удалены."""
        self.sounds.fill_filter()
        self.sounds.rebuild()
        self.mixer.rebuild()
        self.bindings.rebuild()
        self.routing.rebuild()
        self.drums.refresh_structure()

    def refresh_all(self):
        self.sounds.rebuild()
        self.mixer.refresh()
        self.bindings.rebuild()

    def _engine_changed(self):
        if not self._refresh_pending:
            self._refresh_pending = True
            QTimer.singleShot(100, self._do_refresh)

    def _do_refresh(self):
        self._refresh_pending = False
        if self.tabs.currentWidget() is self.mixer:
            self.mixer.refresh()

    def _tick(self):
        if not self.isVisible():
            return
        w = self.tabs.currentWidget()
        if w is self.sounds:
            self.sounds.tick(self.ctx.engine.pad_states())
        elif w is self.mixer:
            self.mixer.tick(self.ctx.engine.channel_peaks())
        elif w is self.routing:
            e = self.ctx.engine
            self.routing.tick(e.channel_peaks(), e.output_peaks(), e.device_status(), e.pad_states())
        elif w is self.player:
            self.player.tick()
        load = self.ctx.engine.render_time * 100
        self.load_label.setText(f"DSP {load:4.0f}%")

    def _clock(self):
        now = QDateTime.currentDateTime()
        d = now.date()
        self.clock.setText(f"{now.toString('HH:mm:ss')}   {DAYS[d.dayOfWeek() - 1]} {d.day():02d} "
                           f"{MONTHS[d.month() - 1]} {d.year()}")

    def _update_midi(self):
        n = len(self.ctx.router.midi_ports)
        self.midi_label.setText(f"MIDI: {n}" if n else "MIDI: НЕТ")
        self.midi_label.setToolTip(", ".join(self.ctx.router.midi_ports) or "MIDI-устройства не подключены")

    def _set_msg(self, text, error=False):
        self.msg.setText(text)
        self.msg.setStyleSheet(f"color: {theme.DANGER if error else theme.MUTED}; font-weight: {700 if error else 400};")

    def _error(self, text):
        self._set_msg("! " + text, error=True)
        self.msg.setToolTip(text)
        self.msg_timer.start(9000)

    def _activity(self, text):
        from app.model import pretty_combo
        if not text.startswith("MIDI"):
            text = pretty_combo(text)
        self.activity.setText("ВВОД: " + text.upper())
        self.activity.setStyleSheet(f"color: {theme.ACCENT2}; font-weight: 700;")
        self.activity_timer.start(1500)
        QTimer.singleShot(1500, lambda: self.activity.setStyleSheet(""))

    def _hotkeys_text(self, on):
        self.hotkeys_btn.setText("КЛАВИШИ: ВКЛ" if on else "КЛАВИШИ: ВЫКЛ")

    def _toggle_hotkeys(self, on):
        self._hotkeys_text(on)
        self.ctx.project.settings.hotkeys_enabled = on
        self.ctx.apply()


def add_recorded_pad(ctx, ev: dict):
    """Создаёт звук из записи блока: канал из настроек блока, свободная клавиша из набора блока
    (когда все заняты — клавиша переходит от самой старой записи), показ в быстром микшере."""
    from app.audio.recorder import slot_combos
    from app.model import Binding, Pad, Trigger, new_id
    pr = ctx.project
    cfg = ev.get("cfg") or {}
    ch = pr.channel(cfg.get("channel", ""))
    if ch is None:
        ch = next((c for c in pr.channels if "эффект" in c.name.lower()), pr.channels[0])
    prefix = (cfg.get("prefix") or "Запись").strip()
    taken = {p.name for p in pr.pads}
    i = 1 + sum(1 for p in pr.pads if p.name.startswith(prefix + " "))
    while f"{prefix} {i}" in taken:
        i += 1
    pad = Pad(new_id("pad"), f"{prefix} {i}", file=ev["path"], channel=ch.id,
              color=theme.PAD_COLORS[i % len(theme.PAD_COLORS)], retrigger="restart")
    pr.pads.append(pad)
    rec_pads = pr.ui.setdefault("rec_pads", {})
    rec_pads[pad.id] = ev.get("rec", "")
    combo = ""
    combos = slot_combos(cfg.get("slots", ""))
    if combos:
        by_key = {}
        for b in pr.bindings:
            if b.trigger.type == "key":
                by_key.setdefault(b.trigger.combo, []).append(b)
        free = [c for c in combos if c not in by_key]
        if free:
            combo = free[0]
            pr.bindings.append(Binding(new_id("bind"), Trigger("key", combo), {"type": "pad", "pad": pad.id}))
        else:
            # все клавиши набора заняты: забираем у самой старой записи этого блока
            mine = [b for c in combos for b in by_key.get(c, [])
                    if b.action.get("type") == "pad" and rec_pads.get(b.action.get("pad")) == ev.get("rec", "")]
            if mine:
                order = {p.id: k for k, p in enumerate(pr.pads)}
                oldest = min(mine, key=lambda b: order.get(b.action.get("pad"), -1))
                oldest.action = {"type": "pad", "pad": pad.id}
                combo = oldest.trigger.combo
    q = pr.ui.get("quick") or {}
    if isinstance(q.get("pads"), list):          # в быстром микшере выбран список звуков — добавим новый
        q["pads"] = q["pads"] + [pad.id]
    ctx.apply()
    return pad, combo
