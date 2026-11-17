"""Вкладка «Дрампад»: 16 пэдов, петля, живая запись по метроному, сетка, наборы, проекты битов."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
                               QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget)

from app.audio import drumkit
from app.audio.drums import GRID_TITLES, GRIDS, METRO, default_pad, delete_beat, list_beats, load_beat, save_beat
from app.model import Channel, Pad, data_dir, new_id, note_name, pretty_combo
from . import theme
from .widgets import Panel, caption, ui_font

AUDIO_EXT = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".aiff", ".aif", ".wma"}
ROWS = [[12, 13, 14, 15], [8, 9, 10, 11], [4, 5, 6, 7], [0, 1, 2, 3]]   # сверху вниз, как на MPC


def ensure_drum_channel(ctx) -> Channel:
    """У дрампада свой канал (эффекты, громкость, разводка) — создаём при первом запуске."""
    pr = ctx.project
    cfg = pr.drum
    ch = pr.channel(cfg.get("channel", ""))
    if ch is None:
        ch = next((c for c in pr.channels if c.name == "Дрампад"), None)
    if ch is None:
        music = next((c for c in pr.channels if "муз" in c.name.lower()), None)
        outs = list(music.outputs) if music else [o.id for o in pr.outputs if not o.bus]
        ch = Channel(new_id("ch"), "Дрампад", volume=0.85, outputs=outs, transition="overlay", color="#b9e35a")
        pr.channels.append(ch)
    cfg["channel"] = ch.id
    return ch


def _key_label(k: str) -> str:
    return pretty_combo(k).upper() if k else "—"


# ===================================================================== пэд
class DrumPadButton(QWidget):
    clicked = Signal(int, int)        # пэд, сила
    released = Signal(int)
    selected = Signal(int)
    dropped = Signal(int, str)

    def __init__(self, page, idx):
        super().__init__()
        self.page = page
        self.idx = idx
        self.setMinimumSize(90, 64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.glow = 0.0

    def paintEvent(self, _e):
        cfg = self.page.cfg
        pad = cfg["pads"][self.idx]
        p = QPainter(self)
        r = QRectF(0, 0, self.width() - 1, self.height() - 1)
        col = QColor(pad.get("color", theme.ACCENT))
        sel = self.page.sel == self.idx
        p.fillRect(r, QColor(theme.SURFACE2))
        if self.glow > 0:
            c = QColor(col)
            c.setAlphaF(min(1.0, 0.25 + 0.75 * self.glow))
            p.fillRect(r, c)
        p.fillRect(QRectF(0, 0, r.width(), 5), col)
        has = any(e[1] == self.idx for e in cfg["events"])
        ink = QColor(theme.INK if self.glow > 0.5 else theme.TEXT)
        p.setPen(ink)
        p.setFont(ui_font(8.5, QFont.Bold))
        name = p.fontMetrics().elidedText(pad.get("name", "").upper(), Qt.ElideRight, int(r.width() - 12))
        p.drawText(QRectF(6, 9, r.width() - 12, 16), Qt.AlignLeft | Qt.AlignVCenter, name)
        p.setFont(ui_font(7, QFont.DemiBold))
        p.setPen(QColor(theme.INK if self.glow > 0.5 else theme.MUTED))
        p.drawText(QRectF(6, 25, r.width() - 12, 14), Qt.AlignLeft | Qt.AlignVCenter, f"ПЭД {self.idx + 1}"
                   + ("  · ТИХО" if pad.get("mute") else "") + ("  · ●" if has else ""))
        key = cfg["keys"][self.idx]
        p.setFont(ui_font(14, QFont.Bold))
        p.setPen(QColor(theme.INK if self.glow > 0.5 else theme.ACCENT2))
        p.drawText(QRectF(6, r.height() - 30, r.width() - 12, 26), Qt.AlignRight | Qt.AlignVCenter, _key_label(key))
        p.setPen(QPen(QColor("white" if sel else theme.BORDER), 2 if sel else 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.selected.emit(self.idx)
            return
        if e.button() == Qt.LeftButton:
            # сила удара — от высоты клика: внизу пэда тише, вверху громче (как на настоящем пэде)
            frac = 1.0 - e.position().y() / max(1, self.height())
            vel = int(50 + 77 * max(0.0, min(1.0, frac * 1.25)))
            self.clicked.emit(self.idx, vel)
            self.selected.emit(self.idx)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.released.emit(self.idx)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            f = u.toLocalFile()
            if Path(f).suffix.lower() in AUDIO_EXT:
                self.dropped.emit(self.idx, f)
                e.acceptProposedAction()
                return


# ===================================================================== сетка петли
class LoopView(QWidget):
    """16 дорожек × петля. Клик — поставить/убрать удар по сетке, бегунок показывает позицию."""
    LABEL_W = 120

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setMinimumHeight(16 * 15 + 20)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.hover = None

    def _geom(self):
        top = 18
        rh = (self.height() - top - 2) / 16
        w = self.width() - self.LABEL_W - 4
        return top, rh, w

    def paintEvent(self, _e):
        cfg = self.page.cfg
        dr = self.page.dr
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG))
        top, rh, w = self._geom()
        x0 = self.LABEL_W
        lb = dr.loop_beats()
        g = GRIDS.get(cfg["grid"]) or 0.25
        # сетка
        steps = int(round(lb / g))
        for i in range(steps + 1):
            beat = i * g
            x = x0 + w * beat / lb
            is_bar = abs(beat / cfg["beats"] - round(beat / cfg["beats"])) < 1e-6
            is_beat = abs(beat - round(beat)) < 1e-6
            p.setPen(QPen(QColor(theme.ACCENT if is_bar else (theme.BORDER if is_beat else theme.SURFACE3)),
                          2 if is_bar else 1))
            p.drawLine(int(x), top, int(x), self.height() - 2)
        p.setFont(ui_font(7, QFont.Bold))
        for bar in range(cfg["bars"]):
            x = x0 + w * bar * cfg["beats"] / lb
            p.setPen(QColor(theme.ACCENT2))
            p.drawText(QRectF(x + 3, 0, 60, 16), Qt.AlignLeft | Qt.AlignVCenter, f"ТАКТ {bar + 1}")
        # дорожки
        for row in range(16):
            pad = 15 - row
            y = top + row * rh
            pc = cfg["pads"][pad]
            if row % 2 == 0:
                p.fillRect(QRectF(x0, y, w, rh), QColor(255, 154, 31, 10))
            p.fillRect(QRectF(0, y + 1, 4, rh - 2), QColor(pc.get("color", theme.ACCENT)))
            p.setPen(QColor(theme.DIM if pc.get("mute") else (theme.ACCENT2 if self.page.sel == pad else theme.MUTED)))
            p.setFont(ui_font(7, QFont.DemiBold))
            p.drawText(QRectF(8, y, x0 - 12, rh), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(f"{pad + 1:>2} {pc.get('name', '').upper()}", Qt.ElideRight, x0 - 14))
        # удары
        cw = max(3.0, w * g / lb - 2)
        for e in cfg["events"]:
            if e[0] >= lb:
                continue
            row = 15 - e[1]
            pc = cfg["pads"][e[1]]
            c = QColor(pc.get("color", theme.ACCENT))
            c.setAlphaF(0.35 + 0.65 * e[2] / 127)
            if pc.get("mute"):
                c = QColor(theme.DIM)
            x = x0 + w * dr.swung(e[0]) / lb
            p.fillRect(QRectF(x + 1, top + row * rh + 2, cw, rh - 4), c)
        if self.hover:
            pad, beat = self.hover
            x = x0 + w * beat / lb
            p.setPen(QPen(QColor("white"), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(x + 1, top + (15 - pad) * rh + 2, cw, rh - 4))
        # бегунок
        if dr.playing and dr.countin == 0:
            x = x0 + w * dr.pos / max(1, dr.loop_len())
            p.setPen(QPen(QColor(theme.DANGER if dr.recording else "white"), 2))
            p.drawLine(int(x), top, int(x), self.height())

    def _at(self, pos):
        top, rh, w = self._geom()
        if pos.x() < self.LABEL_W or pos.y() < top:
            return None
        row = int((pos.y() - top) // rh)
        if not 0 <= row < 16:
            return None
        lb = self.page.dr.loop_beats()
        g = GRIDS.get(self.page.cfg["grid"]) or 0.25
        beat = (pos.x() - self.LABEL_W) / w * lb
        beat = int(beat / g) * g
        return 15 - row, round(beat, 6)

    def mouseMoveEvent(self, e):
        self.hover = self._at(e.position())
        self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        at = self._at(e.position())
        if at is None:
            return
        pad, beat = at
        if e.button() == Qt.LeftButton:
            self.page.dr.toggle_step(pad, beat, int(self.page.cfg.get("kb_vel", 100)))
            self.page.after_edit()
        elif e.button() == Qt.RightButton:
            self.page.select(pad)


# ===================================================================== вкладка
class DrumPage(QWidget):
    def __init__(self, ctx, on_structure=None):
        super().__init__()
        self.ctx = ctx
        self.on_structure = on_structure
        self.dr = ctx.engine.drums
        self.setObjectName("page")
        self.sel = 0
        self._tap = []
        self._loading = False
        self.dr.on_change = lambda: QTimer.singleShot(0, self._sync_transport)
        ctx.bridge.sample_loaded.connect(lambda _p: (self.dr.refresh(), self.grid_host.update()))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        from PySide6.QtWidgets import QScrollArea
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        body.setObjectName("page")
        body.setMinimumHeight(720)
        sc.setWidget(body)
        outer.addWidget(sc)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        lay.addWidget(self._transport())
        top = QHBoxLayout()
        top.setSpacing(8)
        pads = self._pads_panel()
        pads.setFixedWidth(470)
        top.addWidget(pads)
        loop = Panel("Петля — клик по клетке ставит/убирает удар, ПКМ — выбрать пэд")
        self.loop_view = LoopView(self)
        loop.body.addWidget(self.loop_view, 1)
        top.addWidget(loop, 1)
        lay.addLayout(top, 1)
        low = QHBoxLayout()
        low.setSpacing(8)
        ps = self._pad_settings()
        ps.setFixedWidth(470)
        low.addWidget(ps)
        low.addWidget(self._bottom(), 1)
        lay.addLayout(low)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30)
        self.load_all()

    @property
    def cfg(self):
        return self.dr.cfg

    # ------------------------------------------------------------ сборка
    def _transport(self):
        w = QWidget()
        w.setObjectName("drumbar")
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet(f"QWidget#drumbar {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; }}")
        g = QGridLayout(w)
        g.setContentsMargins(8, 6, 8, 6)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(4)
        self.b_play = QPushButton("▶ ПУСК")
        self.b_play.setObjectName("primary")
        self.b_play.setToolTip("Пуск / стоп петли (пробел, пока открыта вкладка)")
        self.b_play.clicked.connect(lambda: self.dr.play_stop())
        self.b_rec = QPushButton("● ЗАПИСЬ")
        self.b_rec.setToolTip("Запись в петлю поверх уже записанного (клавиша ` / Ё). Если петля стоит — "
                              "сначала такт отсчёта")
        self.b_rec.clicked.connect(lambda: self.dr.toggle_rec())
        b_undo = QPushButton("↶ ОТМЕНИТЬ ДУБЛЬ")
        b_undo.setToolTip("Убрать всё, что записано за последний проход записи (Backspace)")
        b_undo.clicked.connect(lambda: (self.dr.undo(), self.after_edit()))
        b_clear = QPushButton("ОЧИСТИТЬ")
        b_clear.clicked.connect(self._clear_all)
        b_dbl = QPushButton("×2")
        b_dbl.setToolTip("Удвоить петлю: такты ×2, записанное повторится во второй половине")
        b_dbl.clicked.connect(lambda: (self.dr.double(), self.load_all()))
        h0 = QHBoxLayout()
        h0.setSpacing(6)
        for b in (self.b_play, self.b_rec, b_undo, b_clear, b_dbl):
            b.setFocusPolicy(Qt.NoFocus)
            h0.addWidget(b)
        h0.addSpacing(14)
        g.addLayout(h0, 0, 0)
        self.bpm = QDoubleSpinBox()
        self.bpm.setRange(40, 240)
        self.bpm.setDecimals(1)
        self.bpm.setSuffix(" BPM")
        self.bpm.valueChanged.connect(lambda v: (self.dr.set_bpm(v), self._save()) if not self._loading else None)
        b_tap = QPushButton("TAP")
        b_tap.setToolTip("Простучите темп мышкой")
        b_tap.setFocusPolicy(Qt.NoFocus)
        b_tap.clicked.connect(self._tap_tempo)
        self.bars = QComboBox()
        for n in (1, 2, 4, 8, 16):
            self.bars.addItem(f"{n} такт" + ("а" if 2 <= n <= 4 else ("ов" if n > 4 else "")), n)
        self.bars.currentIndexChanged.connect(lambda: self._set("bars", self.bars.currentData()))
        self.beats = QComboBox()
        for n in (3, 4, 5, 6, 7):
            self.beats.addItem(f"{n}/4", n)
        self.beats.currentIndexChanged.connect(lambda: self._set("beats", self.beats.currentData()))
        self.grid = QComboBox()
        for k, v in GRID_TITLES.items():
            self.grid.addItem(v, k)
        self.grid.setToolTip("К какой сетке притягивать записанные удары")
        self.grid.currentIndexChanged.connect(lambda: self._set("grid", self.grid.currentData()))
        self.qs = QSpinBox()
        self.qs.setRange(0, 100)
        self.qs.setSingleStep(10)
        self.qs.setSuffix(" %")
        self.qs.setToolTip("Сила выравнивания: 100 % — точно в сетку, меньше — остаётся живая неровность")
        self.qs.valueChanged.connect(lambda v: self._set("qstrength", v))
        self.swing = QSpinBox()
        self.swing.setRange(50, 75)
        self.swing.setSuffix(" %")
        self.swing.setToolTip("Свинг как на MPC: 50 % — ровно, 66 % — триольная «раскачка»")
        self.swing.valueChanged.connect(lambda v: self._set("swing", v))
        row2 = [(caption("Темп"), self.bpm), (None, b_tap), (caption("Длина"), self.bars), (caption("Размер"), self.beats),
                (caption("Сетка"), self.grid), (caption("Сила"), self.qs), (caption("Свинг"), self.swing)]
        h = QHBoxLayout()
        h.setSpacing(6)
        for cap, wd in row2:
            if cap is not None:
                h.addWidget(cap)
            h.addWidget(wd)
        h.addStretch(1)
        g.addLayout(h, 1, 0)
        self.metro = QComboBox()
        for k, v in {"off": "выкл", "rec": "при записи", "on": "всегда"}.items():
            self.metro.addItem(v, k)
        self.metro.setToolTip("Метроном слышно только в «прослушивании» (ваших наушниках), в Дискорд он не идёт")
        self.metro.currentIndexChanged.connect(lambda: self._set("metro", self.metro.currentData()))
        self.metro_vol = QSlider(Qt.Horizontal)
        self.metro_vol.setRange(0, 100)
        self.metro_vol.setFixedWidth(70)
        self.metro_vol.setToolTip("Громкость метронома")
        self.metro_vol.valueChanged.connect(lambda v: self._set("metro_vol", v / 100))
        self.countin = QCheckBox("Такт отсчёта")
        self.countin.toggled.connect(lambda on: self._set("countin", on))
        self.repeat = QCheckBox("Повтор ноты")
        self.repeat.setToolTip("Пока держите пэд, он повторяется по сетке (как Note Repeat на MPC)")
        self.repeat.toggled.connect(lambda on: self._set("repeat", on))
        self.rgrid = QComboBox()
        for k in ("1/4", "1/8", "1/8T", "1/16", "1/16T", "1/32"):
            self.rgrid.addItem(GRID_TITLES[k], k)
        self.rgrid.currentIndexChanged.connect(lambda: self._set("repeat_grid", self.rgrid.currentData()))
        self.status = QLabel()
        self.status.setStyleSheet(f"color: {theme.ACCENT2}; font-weight: 700;")
        for wd in (caption("Метроном"), self.metro, self.metro_vol, self.countin, self.repeat, self.rgrid):
            h0.addWidget(wd)
        h0.addStretch(1)
        h0.addWidget(self.status)
        return w

    def _pads_panel(self):
        panel = Panel("Пэды — клавиши, MIDI или мышь (выше клик — громче)")
        self.grid_host = QWidget()
        self.grid_host.setFocusPolicy(Qt.StrongFocus)
        gl = QGridLayout(self.grid_host)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(5)
        self.pads = [None] * 16
        for r, row in enumerate(ROWS):
            for c, idx in enumerate(row):
                b = DrumPadButton(self, idx)
                b.clicked.connect(lambda i, v: self.dr.hit(i, v))
                b.released.connect(self.dr.release)
                b.selected.connect(self.select)
                b.dropped.connect(self._drop_file)
                gl.addWidget(b, r, c)
                self.pads[idx] = b
        panel.body.addWidget(self.grid_host, 1)
        tip = QLabel("Пробел — пуск/стоп · ` (Ё) — запись · Backspace — отменить дубль · Shift + пэд — акцент. "
                     "Работает, пока открыта эта вкладка (или включите «клавиши везде» внизу).")
        tip.setObjectName("small")
        tip.setWordWrap(True)
        panel.body.addWidget(tip)
        return panel

    def _pad_settings(self):
        self.ps = Panel("Пэд 1")
        f = QGridLayout()
        f.setVerticalSpacing(5)
        f.setHorizontalSpacing(6)
        self.p_name = QLineEdit()
        self.p_name.editingFinished.connect(lambda: self._pad_set("name", self.p_name.text().strip() or "Пэд"))
        self.p_src = QComboBox()
        self.p_src.setMaxVisibleItems(24)
        self.p_src.activated.connect(self._src_chosen)
        b_file = QPushButton("ФАЙЛ…")
        b_file.setToolTip("Свой звук на этот пэд (можно и просто перетащить файл на пэд)")
        b_file.clicked.connect(self._pick_file)
        f.addWidget(caption("Имя"), 0, 0)
        f.addWidget(self.p_name, 0, 1, 1, 3)
        f.addWidget(caption("Звук"), 1, 0)
        f.addWidget(self.p_src, 1, 1, 1, 2)
        f.addWidget(b_file, 1, 3)
        self.sliders = {}
        specs = [("vol", "Громк.", 0, 150, 100), ("pan", "Панор.", -100, 100, 100),
                 ("pitch", "Высота", -24, 24, 2), ("length_ms", "Длина", 0, 3000, 1)]
        for i, (key, label, lo, hi, scale) in enumerate(specs):
            sl = QSlider(Qt.Horizontal)
            sl.setRange(lo, hi)
            sl.setFocusPolicy(Qt.NoFocus)
            lab = QLabel()
            lab.setFixedWidth(62)
            lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            def changed(v, key=key, scale=scale, lab=lab):
                val = v / scale
                if key == "pan":
                    lab.setText("центр" if v == 0 else (f"Л {-v}" if v < 0 else f"П {v}"))
                elif key == "length_ms":
                    lab.setText("целиком" if v == 0 else f"{v} мс")
                elif key == "pitch":
                    lab.setText(f"{val:+.1f} пт")
                else:
                    lab.setText(f"{v}%")
                if not self._loading:
                    self._pad_set(key, val, prep=key in ("pitch", "length_ms"))
            sl.valueChanged.connect(changed)
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(sl, 1)
            row.addWidget(lab)
            f.addWidget(caption(label), 2 + i // 2, (i % 2) * 2)
            f.addLayout(row, 2 + i // 2, (i % 2) * 2 + 1)
            self.sliders[key] = (sl, scale, changed)
        self.p_rev = QCheckBox("Задом наперёд")
        self.p_rev.toggled.connect(lambda on: self._pad_set("reverse", on, prep=True) if not self._loading else None)
        self.p_mute = QCheckBox("Не играть в петле")
        self.p_mute.toggled.connect(lambda on: self._pad_set("mute", on, sched=True) if not self._loading else None)
        self.p_choke = QComboBox()
        self.p_choke.addItem("не глушит", 0)
        for n in range(1, 5):
            self.p_choke.addItem(f"группа {n}" + (" (хэты)" if n == 1 else ""), n)
        self.p_choke.setToolTip("Пэды одной группы глушат друг друга — закрытый хэт обрывает открытый")
        self.p_choke.currentIndexChanged.connect(
            lambda: self._pad_set("choke", self.p_choke.currentData()) if not self._loading else None)
        cr = QHBoxLayout()
        cr.addWidget(self.p_rev)
        cr.addWidget(self.p_mute)
        cr.addWidget(self.p_choke, 1)
        f.addLayout(cr, 4, 0, 1, 4)
        kr = QHBoxLayout()
        self.p_key = QPushButton()
        self.p_key.setToolTip("Нажмите и затем клавишу (без Ctrl/Alt) — она станет клавишей пэда")
        self.p_key.clicked.connect(self._capture_key)
        self.p_midi = QLabel()
        self.p_midi.setObjectName("small")
        b_clr = QPushButton("ОЧИСТИТЬ")
        b_clr.setToolTip("Стереть все удары этого пэда из петли")
        b_clr.clicked.connect(lambda: (self.dr.clear(self.sel), self.after_edit()))
        b_def = QPushButton("ИЗ НАБОРА")
        b_def.setToolTip("Вернуть этому пэду звук текущего набора")
        b_def.clicked.connect(self._pad_default)
        for wd in (self.p_key, self.p_midi, b_clr, b_def):
            kr.addWidget(wd)
        f.addLayout(kr, 5, 0, 1, 4)
        f.setColumnStretch(1, 1)
        f.setColumnStretch(3, 1)
        self.ps.body.addLayout(f)
        return self.ps

    def _bottom(self):
        w = Panel("Набор, биты и результат")
        g = QGridLayout()
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(6)
        self.kit = QComboBox()
        for k, (title, _f) in drumkit.KITS.items():
            self.kit.addItem(title, k)
        b_kit = QPushButton("ЗАГРУЗИТЬ НАБОР")
        b_kit.setToolTip("Поставить на все 16 пэдов звуки выбранного набора (записанные удары останутся)")
        b_kit.clicked.connect(self._load_kit)
        self.beat = QComboBox()
        self.beat.setToolTip("Сохранённые биты (проекты)")
        self.beat.activated.connect(self._open_beat)
        b_new = QPushButton("НОВЫЙ")
        b_new.clicked.connect(self._new_beat)
        b_save = QPushButton("СОХРАНИТЬ")
        b_save.setToolTip("Сохранить бит: пэды, звуки, темп и петлю — чтобы потом открыть и доделать")
        b_save.clicked.connect(self._save_beat)
        b_del = QPushButton("✕")
        b_del.setObjectName("ghost")
        b_del.setFixedWidth(30)
        b_del.setToolTip("Удалить сохранённый бит")
        b_del.clicked.connect(self._del_beat)
        b_pad = QPushButton("→ В САУНДПАД")
        b_pad.setObjectName("primary")
        b_pad.setToolTip("Петля станет звуком во вкладке «Звуки» (по кругу, нажал — играет / нажал — стоп)")
        b_pad.clicked.connect(self._to_soundpad)
        b_exp = QPushButton("ЭКСПОРТ ФАЙЛА…")
        b_exp.setToolTip("WAV / FLAC / MP3, несколько повторов петли")
        b_exp.clicked.connect(self._export)
        self.loops = QSpinBox()
        self.loops.setRange(1, 64)
        self.loops.setPrefix("×")
        self.loops.setToolTip("Сколько раз повторить петлю в файле")
        self.loops.valueChanged.connect(lambda v: self._set("export_loops", v, sched=False))
        self.channel = QComboBox()
        self.channel.setToolTip("В какой канал Мяукала идёт звук дрампада: его эффекты, громкость и провода")
        self.channel.activated.connect(self._channel_chosen)
        self.everywhere = QCheckBox("Клавиши пэдов работают везде")
        self.everywhere.setToolTip("Играть из любой программы (например, в Дискорд во время игры). "
                                   "Выключите, когда печатаете!")
        self.everywhere.toggled.connect(lambda on: self._set("keys_everywhere", on, sched=False))
        self.kbv = QSpinBox()
        self.kbv.setRange(20, 127)
        self.kbv.setToolTip("Сила удара с клавиатуры (Shift — всегда 127)")
        self.kbv.valueChanged.connect(lambda v: self._set("kb_vel", v, sched=False))
        self.comp = QSpinBox()
        self.comp.setRange(-200, 200)
        self.comp.setSuffix(" мс")
        self.comp.setToolTip("Поправка записи. Задержка звука учитывается сама; если удары всё равно ложатся "
                             "позже, чем играли, — увеличьте, раньше — уменьшите")
        self.comp.valueChanged.connect(lambda v: self._set("comp_ms", v, sched=False))
        self.midi_base = QSpinBox()
        self.midi_base.setRange(0, 112)
        self.midi_base.setToolTip("MIDI-нота пэда 1 (у большинства пэд-контроллеров 36 = C1)")
        self.midi_base.valueChanged.connect(lambda v: (self._set("midi_base", v, sched=False), self._fill_pad()))

        def row(*items):
            h = QHBoxLayout()
            h.setSpacing(6)
            for it in items:
                if it == "|":
                    h.addSpacing(12)
                elif isinstance(it, tuple):
                    h.addWidget(*it)
                else:
                    h.addWidget(it)
            h.addStretch(1)
            return h
        g.addLayout(row(caption("Набор"), (self.kit, 1), b_kit, "|", caption("Канал"), (self.channel, 1)), 0, 0)
        g.addLayout(row(caption("Бит"), (self.beat, 1), b_new, b_save, b_del), 1, 0)
        g.addLayout(row(b_pad, b_exp, self.loops, "|", self.everywhere), 2, 0)
        g.addLayout(row(caption("Сила клавиш"), self.kbv, caption("Поправка записи"), self.comp,
                        caption("MIDI пэда 1"), self.midi_base), 3, 0)
        w.body.addLayout(g)
        return w

    # ------------------------------------------------------------ данные → интерфейс
    def load_all(self):
        self._loading = True
        c = self.cfg
        self.bpm.setValue(c["bpm"])
        self.bars.setCurrentIndex(max(0, self.bars.findData(c["bars"])))
        self.beats.setCurrentIndex(max(0, self.beats.findData(c["beats"])))
        self.grid.setCurrentIndex(max(0, self.grid.findData(c["grid"])))
        self.qs.setValue(int(c.get("qstrength", 100)))
        self.swing.setValue(int(c.get("swing", 50)))
        self.metro.setCurrentIndex(max(0, self.metro.findData(c.get("metro", "rec"))))
        self.metro_vol.setValue(int(float(c.get("metro_vol", 0.6)) * 100))
        self.countin.setChecked(bool(c.get("countin", True)))
        self.repeat.setChecked(bool(c.get("repeat")))
        self.rgrid.setCurrentIndex(max(0, self.rgrid.findData(c.get("repeat_grid", "1/16"))))
        self.kit.setCurrentIndex(max(0, self.kit.findData(c.get("kit", "808"))))
        self.loops.setValue(int(c.get("export_loops", 4)))
        self.everywhere.setChecked(bool(c.get("keys_everywhere")))
        self.kbv.setValue(int(c.get("kb_vel", 100)))
        self.comp.setValue(int(c.get("comp_ms", 0)))
        self.midi_base.setValue(int(c.get("midi_base", 36)))
        self._fill_beats()
        self._fill_channels()
        self._loading = False
        self._fill_sources()
        self.select(self.sel)
        self._sync_transport()
        self.loop_view.update()

    def _fill_beats(self):
        self.beat.blockSignals(True)
        self.beat.clear()
        self.beat.addItem(f"● {self.cfg.get('name', 'Новый бит')} (текущий)", "")
        for n in list_beats():
            self.beat.addItem(n, n)
        self.beat.setCurrentIndex(0)
        self.beat.blockSignals(False)

    def _fill_channels(self):
        self.channel.blockSignals(True)
        self.channel.clear()
        for c in self.ctx.project.channels:
            self.channel.addItem(c.name, c.id)
        self.channel.setCurrentIndex(max(0, self.channel.findData(self.cfg.get("channel"))))
        self.channel.blockSignals(False)

    def _fill_sources(self):
        cb = self.p_src
        cb.blockSignals(True)
        cb.clear()
        for code, title in drumkit.all_sources():
            cb.addItem(title, code)
        files = [p for p in self.ctx.project.pads if p.kind == "file"]
        if files:
            cb.insertSeparator(cb.count())
            for p in files:
                cb.addItem(f"Саундпад · {p.name}", f"pad:{p.id}")
        cb.blockSignals(False)

    def refresh_structure(self):
        """Звали снаружи: поменялись каналы/звуки саундпада."""
        self._fill_channels()
        self._fill_sources()
        self._fill_pad()

    def select(self, idx):
        self.sel = idx
        self._fill_pad()
        for b in self.pads:
            b.update()
        self.loop_view.update()

    def _fill_pad(self):
        i = self.sel
        p = self.cfg["pads"][i]
        self._loading = True
        self.ps.title.setText(f"ПЭД {i + 1} — {p.get('name', '').upper()}")
        self.p_name.setText(p.get("name", ""))
        src = p.get("src", "")
        k = self.p_src.findData(src)
        if k < 0:
            label = ("Файл · " + Path(src[5:]).name) if src.startswith("file:") else src
            self.p_src.addItem(label, src)
            k = self.p_src.count() - 1
        self.p_src.setCurrentIndex(k)
        for key, (sl, scale, changed) in self.sliders.items():
            v = float(p.get(key, 0))
            sl.setValue(int(round(v * scale)))
            changed(sl.value())
        self.p_rev.setChecked(bool(p.get("reverse")))
        self.p_mute.setChecked(bool(p.get("mute")))
        self.p_choke.setCurrentIndex(max(0, self.p_choke.findData(int(p.get("choke", 0)))))
        self.p_key.setText("КЛАВИША " + _key_label(self.cfg["keys"][i]))
        note = int(self.cfg.get("midi_base", 36)) + i
        self.p_midi.setText(f"MIDI {note_name(note)}")
        self._loading = False

    # ------------------------------------------------------------ правки
    def _save(self):
        self.ctx.save_later()

    def after_edit(self):
        self.dr.changed()
        self.loop_view.update()
        for b in self.pads:
            b.update()
        self._save()

    def _set(self, key, value, sched=True):
        if self._loading:
            return
        self.cfg[key] = value
        if sched:
            self.dr.changed()
        self.loop_view.update()
        self._save()

    def _pad_set(self, key, value, prep=False, sched=False):
        p = self.cfg["pads"][self.sel]
        p[key] = value
        if prep:
            self.dr.refresh()
        if sched:
            self.dr.changed()
        if key == "name":
            self.ps.title.setText(f"ПЭД {self.sel + 1} — {str(value).upper()}")
        self.pads[self.sel].update()
        self.loop_view.update()
        self._save()

    def _set_src(self, idx, src, name):
        p = self.cfg["pads"][idx]
        p["src"] = src
        p["name"] = name[:24]
        self.dr.refresh()
        self.after_edit()
        if idx == self.sel:
            self._fill_pad()
        self.dr.hit(idx, 100, record=False)

    def _src_chosen(self, _i):
        src = self.p_src.currentData()
        if not src:
            return
        name = self.p_src.currentText().split("·")[-1].strip()
        self._set_src(self.sel, src, name)

    def _pick_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Звук на пэд", str(Path.home()),
                                           "Аудио (" + " ".join("*" + e for e in sorted(AUDIO_EXT)) + ")")
        if f:
            self._drop_file(self.sel, f)

    def _drop_file(self, idx, path):
        from app.model import import_audio_file
        try:
            path = import_audio_file(path)
        except Exception:
            pass
        self.ctx.engine.cache.request(path)
        self.select(idx)
        self._set_src(idx, "file:" + path, Path(path).stem)

    def _pad_default(self):
        kit = self.cfg.get("kit", "808")
        self.cfg["pads"][self.sel] = dict(default_pad(kit, self.sel), color=self.cfg["pads"][self.sel].get("color"))
        self.dr.refresh()
        self.after_edit()
        self._fill_pad()

    def _load_kit(self):
        kit = self.kit.currentData()
        self.cfg["kit"] = kit
        for i in range(16):
            old = self.cfg["pads"][i]
            new = default_pad(kit, i)
            new["color"] = old.get("color", new["color"])
            self.cfg["pads"][i] = new
        self.dr.refresh()
        self.after_edit()
        self._fill_pad()
        self.window()._set_msg(f"НАБОР «{self.kit.currentText().upper()}» НА ПЭДАХ")

    def _channel_chosen(self, _i):
        self.cfg["channel"] = self.channel.currentData()
        self._save()

    def _capture_key(self):
        self.p_key.setText("НАЖМИТЕ КЛАВИШУ…")
        idx = self.sel

        def got(trig):
            QTimer.singleShot(0, lambda: self._got_key(idx, trig))
        self.ctx.router.start_capture(got)

    def _got_key(self, idx, trig):
        combo = trig.combo if trig.type == "key" else ""
        if not combo or "+" in combo:
            self.window()._error("Для пэда нужна одна клавиша без Ctrl/Alt/Shift")
        else:
            keys = self.cfg["keys"]
            if combo in keys:                    # клавиша была у другого пэда — меняем местами
                keys[keys.index(combo)] = keys[idx]
            keys[idx] = combo
            self._save()
        self._fill_pad()
        for b in self.pads:
            b.update()

    def _clear_all(self):
        if self.cfg["events"] and QMessageBox.question(self, "Очистить", "Стереть всю петлю?") != QMessageBox.Yes:
            return
        self.dr.clear()
        self.after_edit()

    def _tap_tempo(self):
        now = time.perf_counter()
        self._tap = [t for t in self._tap if now - t < 2.5] + [now]
        if len(self._tap) >= 3:
            d = (self._tap[-1] - self._tap[0]) / (len(self._tap) - 1)
            self.bpm.setValue(round(60 / d, 1))

    # ------------------------------------------------------------ биты
    def _new_beat(self):
        if self.cfg["events"] and QMessageBox.question(
                self, "Новый бит", "Начать новый бит? Несохранённая петля сотрётся.") != QMessageBox.Yes:
            return
        self.dr.stop()
        self.cfg["events"] = []
        self.cfg["name"] = "Новый бит"
        self.after_edit()
        self.load_all()

    def _save_beat(self):
        name, ok = QInputDialog.getText(self, "Сохранить бит", "Название:", text=self.cfg.get("name", "Новый бит"))
        if not ok or not name.strip():
            return
        self.cfg["name"] = name.strip()
        save_beat(self.cfg, name.strip())
        self._save()
        self._fill_beats()
        self.window()._set_msg(f"БИТ «{name.strip().upper()}» СОХРАНЁН")

    def _open_beat(self, _i):
        name = self.beat.currentData()
        if not name:
            return
        self.dr.stop()
        try:
            load_beat(self.cfg, name)
        except Exception as e:  # noqa: BLE001
            self.window()._error(f"Не удалось открыть бит: {e}")
            return
        self.dr.refresh()
        self.dr.changed()
        self._save()
        self.load_all()
        self.window()._set_msg(f"ОТКРЫТ БИТ «{name.upper()}»")

    def _del_beat(self):
        name = self.beat.currentData()
        if not name:
            return
        if QMessageBox.question(self, "Удалить бит", f"Удалить сохранённый бит «{name}»?") == QMessageBox.Yes:
            delete_beat(name)
            self._fill_beats()

    # ------------------------------------------------------------ результат
    def _to_soundpad(self):
        if not self.cfg["events"]:
            self.window()._error("Петля пустая — сначала что-нибудь сыграйте")
            return
        import soundfile as sf
        from app.audio.recorder import _safe, unique_path
        y = self.dr.render_offline(1, "loop")
        name = self.cfg.get("name") or "Бит"
        path = unique_path(data_dir() / "library" / "Биты", _safe(f"{name} {int(self.cfg['bpm'])}bpm"), "wav")
        sf.write(str(path), y, 48000, subtype="PCM_16")
        pr = self.ctx.project
        ch = next((c for c in pr.channels if "муз" in c.name.lower()), None) or pr.channel(self.cfg["channel"])
        pad = Pad(new_id("pad"), f"Бит: {name}", file=str(path), channel=ch.id, mode="toggle", loop=True,
                  color="#b9e35a", fade_out_ms=40)
        pr.pads.append(pad)
        self.ctx.apply()
        if self.on_structure:
            self.on_structure()
        self.window()._set_msg(f"ПЕТЛЯ «{name.upper()}» ТЕПЕРЬ В «ЗВУКАХ» (КАНАЛ «{ch.name.upper()}») — НАЗНАЧЬТЕ ЕЙ КЛАВИШУ")

    def _export(self):
        if not self.cfg["events"]:
            self.window()._error("Петля пустая — сначала что-нибудь сыграйте")
            return
        import soundfile as sf
        from app.audio.recorder import _safe, available_formats, default_folder
        folder = Path(default_folder())
        folder.mkdir(parents=True, exist_ok=True)
        fmts = available_formats()
        filters = ";;".join(f"{k.upper()} (*.{k})" for k in fmts)
        name = _safe(self.cfg.get("name") or "Бит")
        f, flt = QFileDialog.getSaveFileName(self, "Экспорт бита", str(folder / f"{name}.wav"), filters)
        if not f:
            return
        ext = Path(f).suffix.lower().lstrip(".")
        if ext not in fmts:
            ext = flt.split()[0].lower() if flt else "wav"
            f = f + "." + ext
        y = self.dr.render_offline(int(self.cfg.get("export_loops", 4)), "file")
        fmt, sub, _t = fmts.get(ext, fmts["wav"])
        try:
            sf.write(f, y, 48000, format=fmt, subtype=sub)
        except Exception as e:  # noqa: BLE001
            self.window()._error(f"Не удалось сохранить: {e}")
            return
        self.window()._set_msg(f"СОХРАНЕНО: {Path(f).name.upper()} ({len(y) / 48000:.1f} С)")

    # ------------------------------------------------------------ живое
    def _sync_transport(self):
        dr = self.dr
        self.b_play.setText("■ СТОП" if dr.playing else "▶ ПУСК")
        if dr.recording:
            self.b_rec.setStyleSheet(f"background: {theme.DANGER}; color: {theme.INK}; border-color: {theme.DANGER};")
        else:
            self.b_rec.setStyleSheet("")
        self.everywhere.blockSignals(True)
        self.everywhere.setChecked(bool(self.cfg.get("keys_everywhere")))
        self.everywhere.blockSignals(False)
        self.loop_view.update()

    def _typing(self) -> bool:
        from PySide6.QtWidgets import QApplication
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QAbstractSpinBox))

    def tick(self):
        dr = self.dr
        vis = self.isVisible() and self.window().isActiveWindow()
        dr.ui_focus = bool(vis and not self._typing())
        if not self.isVisible():
            return
        now = time.perf_counter()
        for i, b in enumerate(self.pads):
            g = max(0.0, 1.0 - (now - dr.flash[i]) / 0.18)
            if abs(g - b.glow) > 0.01 or (g == 0 and b.glow != 0):
                b.glow = g
                b.update()
        if dr.playing:
            self.loop_view.update()
        if dr.countin > 0:
            beats_left = int(dr.countin / dr.spb()) + 1
            self.status.setText(f"ОТСЧЁТ… {beats_left}")
        elif dr.playing:
            beat = dr.pos / dr.spb()
            bar = int(beat // self.cfg["beats"]) + 1
            self.status.setText(f"{'● ЗАПИСЬ' if dr.recording else '▶'}  ТАКТ {bar}.{int(beat % self.cfg['beats']) + 1}")
        else:
            n = len(self.cfg["events"])
            self.status.setText(f"УДАРОВ В ПЕТЛЕ: {n}" if n else "ПЕТЛЯ ПУСТАЯ")

    def keyPressEvent(self, e):
        """Запасной путь, если глобальные клавиши недоступны: играем по клавишам окна."""
        if not self.ctx.router.keyboard_error or e.isAutoRepeat():
            return super().keyPressEvent(e)
        k = e.text().lower()
        if k in self.cfg["keys"]:
            self.dr.hit(self.cfg["keys"].index(k), 127 if e.modifiers() & Qt.ShiftModifier else int(self.cfg["kb_vel"]))
        elif e.key() == Qt.Key_Space:
            self.dr.play_stop()
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if self.ctx.router.keyboard_error and not e.isAutoRepeat():
            k = e.text().lower()
            if k in self.cfg["keys"]:
                self.dr.release(self.cfg["keys"].index(k))
                return
        super().keyReleaseEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        self.grid_host.setFocus()
        self.dr.refresh()

    def hideEvent(self, e):
        self.dr.ui_focus = False
        super().hideEvent(e)
