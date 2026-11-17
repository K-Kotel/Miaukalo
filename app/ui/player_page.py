"""Вкладка «Плеер»: плейлист музыки, который играет в выбранный канал."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMenu, QPushButton, QSlider, QVBoxLayout, QWidget)

from app.audio.decoder import AUDIO_FILTER
from app.audio.player import OPS, probe
from . import theme
from .widgets import Panel, caption, fmt_time, ui_font

AUDIO_EXT = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".webm", ".mp4", ".wma", ".aiff", ".aif"}
REPEAT_TXT = {"all": "ПОВТОР: ВСЁ", "one": "ПОВТОР: ОДИН", "off": "ПОВТОР: ВЫКЛ"}


def player_keys(project) -> dict:
    keys = {}
    for b in project.bindings:
        if b.action.get("type") == "player":
            keys.setdefault(b.action.get("op"), []).append(b.trigger.label().upper())
    return keys


class SeekBar(QWidget):
    """Сегментная полоса позиции трека; клик или протяжка — перемотка."""
    seek = Signal(float)

    def __init__(self, height=14, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.pos_s = 0.0
        self.dur = 0.0
        self.setCursor(Qt.PointingHandCursor)

    def set_pos(self, pos, dur):
        if abs(pos - self.pos_s) > 0.02 or dur != self.dur:
            self.pos_s, self.dur = pos, dur
            self.update()

    def _emit(self, x):
        if self.dur > 0:
            self.seek.emit(max(0.0, min(1.0, x / max(1, self.width()))) * self.dur)

    def mousePressEvent(self, e):
        self._emit(e.position().x())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._emit(e.position().x())

    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor(theme.BG))
        frac = self.pos_s / self.dur if self.dur > 0 else 0.0
        fill = int(r.width() * max(0.0, min(1.0, frac)))
        seg = 6
        for x in range(0, r.width(), seg):
            p.fillRect(QRect(x, 1, seg - 2, r.height() - 2), QColor(theme.ACCENT if x < fill else theme.SURFACE3))
        p.setPen(QColor(theme.BORDER))
        p.drawRect(r.adjusted(0, 0, -1, -1))


class PlaylistWidget(QListWidget):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            self.files_dropped.emit([u.toLocalFile() for u in e.mimeData().urls()])
            e.acceptProposedAction()
            return
        super().dropEvent(e)


def collect_audio(paths) -> list:
    out = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            out += sorted(str(x) for x in pp.rglob("*") if x.suffix.lower() in AUDIO_EXT)
        elif pp.suffix.lower() in AUDIO_EXT:
            out.append(str(pp))
    return out


class PlayerPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("page")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # ---- сейчас играет
        now = Panel("Сейчас играет")
        now.setFixedWidth(430)
        b = now.body
        b.setSpacing(6)
        self.title = QLabel("—")
        self.title.setWordWrap(True)
        self.title.setStyleSheet(f"font-size: 14pt; font-weight: 700; color: {theme.ACCENT2};")
        self.title.setMinimumHeight(48)
        self.sub = QLabel()
        self.sub.setObjectName("small")
        self.state_l = QLabel()
        self.state_l.setStyleSheet(f"color: {theme.ACCENT}; font-weight: 700;")
        b.addWidget(self.title)
        b.addWidget(self.sub)
        b.addWidget(self.state_l)
        self.seek = SeekBar(16)
        self.seek.seek.connect(lambda s: ctx.engine.player.seek(s))
        b.addWidget(self.seek)
        self.time_l = QLabel("0:00.00 / 0:00.00")
        self.time_l.setObjectName("muted")
        b.addWidget(self.time_l)

        ctl = QHBoxLayout()
        ctl.setSpacing(4)
        self.b_prev = self._btn("◄◄", "Предыдущий (в середине трека — в начало)", lambda: self._cmd("prev"))
        self.b_play = self._btn("►", "Играть / пауза", lambda: self._cmd("play_pause"), primary=True)
        self.b_stop = self._btn("■", "Стоп", lambda: self._cmd("stop"))
        self.b_next = self._btn("►►", "Следующий", lambda: self._cmd("next"))
        for w in (self.b_prev, self.b_play, self.b_stop, self.b_next):
            ctl.addWidget(w)
        b.addLayout(ctl)
        modes = QHBoxLayout()
        self.b_shuffle = QPushButton("ПЕРЕМЕШАТЬ")
        self.b_shuffle.setCheckable(True)
        self.b_shuffle.clicked.connect(lambda: self._cmd("shuffle"))
        self.b_repeat = QPushButton()
        self.b_repeat.clicked.connect(lambda: self._cmd("repeat"))
        modes.addWidget(self.b_shuffle)
        modes.addWidget(self.b_repeat)
        b.addLayout(modes)

        vr = QHBoxLayout()
        vr.addWidget(caption("Громкость"))
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.valueChanged.connect(self._vol)
        self.vol_l = QLabel()
        self.vol_l.setFixedWidth(44)
        vr.addWidget(self.vol, 1)
        vr.addWidget(self.vol_l)
        b.addLayout(vr)
        cr = QHBoxLayout()
        cr.addWidget(caption("Канал"))
        self.channel = QComboBox()
        self.channel.setToolTip("Плеер играет в этот канал — дальше звук идёт по его проводам, через его эффекты")
        self.channel.currentIndexChanged.connect(self._channel)
        cr.addWidget(self.channel, 1)
        cr.addWidget(caption("Переход"))
        self.xf = QDoubleSpinBox()
        self.xf.setRange(0, 12)
        self.xf.setSingleStep(0.5)
        self.xf.setSuffix(" с")
        self.xf.setToolTip("Плавный переход между треками (0 — без перехода)")
        self.xf.valueChanged.connect(self._xf)
        cr.addWidget(self.xf)
        b.addLayout(cr)
        self.keys_l = QLabel()
        self.keys_l.setObjectName("small")
        self.keys_l.setWordWrap(True)
        b.addWidget(self.keys_l)
        kb = QPushButton("НАЗНАЧИТЬ КЛАВИШИ…")
        km = QMenu(kb)
        for op, text in OPS.items():
            km.addAction(text, lambda op=op: self._bind(op))
        kb.setMenu(km)
        b.addWidget(kb)
        hint = QLabel("Трек, запущенный звуком из того же канала, подчиняется правилу перехода канала "
                      "(например, кроссфейду в «Музыке»), а «Стоп всё» ставит плеер на паузу.")
        hint.setObjectName("cap")
        hint.setWordWrap(True)
        b.addWidget(hint)
        b.addStretch(1)
        lay.addWidget(now)

        # ---- плейлист
        lst = Panel("Плейлист")
        self.pl_panel = lst
        lst.add_head_button("+ ФАЙЛЫ", self.add_files)
        lst.add_head_button("+ ПАПКА", self.add_folder)
        lb = lst.body
        self.list = PlaylistWidget()
        self.list.itemDoubleClicked.connect(lambda it: ctx.engine.player.play(self.list.row(it)))
        self.list.files_dropped.connect(self.add_paths)
        self.list.model().rowsMoved.connect(self._moved)
        lb.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, slot in (("ИГРАТЬ ВЫБРАННЫЙ", self._play_sel), ("↑", lambda: self._shift(-1)),
                           ("↓", lambda: self._shift(1)), ("УБРАТЬ", self._remove), ("ОЧИСТИТЬ", self._clear)):
            bt = QPushButton(text)
            bt.clicked.connect(slot)
            row.addWidget(bt)
        row.addStretch(1)
        self.count_l = QLabel()
        self.count_l.setObjectName("small")
        row.addWidget(self.count_l)
        lb.addLayout(row)
        drop = QLabel("Перетащите сюда файлы или папки с музыкой · двойной клик — играть · тяните строки, чтобы поменять порядок")
        drop.setObjectName("cap")
        lb.addWidget(drop)
        lay.addWidget(lst, 1)
        self._last_sig = None
        self.refresh()

    # ------------------------------------------------------------ helpers
    @property
    def cfg(self):
        return self.ctx.project.player

    def _btn(self, text, tip, slot, primary=False):
        b = QPushButton(text)
        b.setToolTip(tip)
        b.setMinimumHeight(38)
        b.setStyleSheet("font-size: 13pt; font-weight: 700;")
        if primary:
            b.setObjectName("primary")
        b.clicked.connect(slot)
        return b

    def _cmd(self, op):
        self.ctx.engine.player.command(op)
        self.ctx.save_later()
        self.tick()

    def _vol(self, v):
        self.ctx.engine.player.set_volume(v / 100)
        self.vol_l.setText(f"{v}%")
        self.ctx.save_later()

    def _channel(self):
        cid = self.channel.currentData()
        if cid and cid != self.cfg.channel:
            self.cfg.channel = cid
            self.ctx.save_later()
            self.window().routing_changed() if hasattr(self.window(), "routing_changed") else None

    def _xf(self, v):
        self.cfg.crossfade = float(v)
        self.ctx.save_later()

    def _bind(self, op):
        from .binding_dialog import BindingDialog
        BindingDialog(self.ctx, None, self, preset={"type": "player", "op": op}).exec()
        self.refresh()

    # ------------------------------------------------------------ плейлист
    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Добавить музыку", "", AUDIO_FILTER)
        if paths:
            self.add_paths(paths)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Папка с музыкой")
        if d:
            self.add_paths([d])

    def add_paths(self, paths):
        files = collect_audio(paths)
        if not files:
            return
        items = [probe(f) for f in files]
        with self.ctx.engine.lock:
            self.cfg.playlist = self.cfg.playlist + items
            self.ctx.engine.player._reorder()
        self.ctx.save_later()
        self.refresh()
        self.window()._set_msg(f"В ПЛЕЙЛИСТ ДОБАВЛЕНО: {len(items)}")

    def _current_path(self):
        cur = self.ctx.engine.player.current()
        return cur["path"] if cur else None

    def _set_playlist(self, new):
        """Новый порядок/состав плейлиста; играющий трек остаётся текущим."""
        cur = self._current_path()
        with self.ctx.engine.lock:
            self.cfg.playlist = new
            idx = next((i for i, it in enumerate(new) if it["path"] == cur), None)
            if idx is not None:
                self.cfg.index = idx
            elif self.cfg.index >= len(new):
                self.cfg.index = max(0, len(new) - 1)
            self.ctx.engine.player._reorder()
        self.ctx.save_later()

    def _moved(self, *_a):
        new = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        self._set_playlist(new)
        self.refresh()

    def _play_sel(self):
        r = self.list.currentRow()
        if r >= 0:
            self.ctx.engine.player.play(r)

    def _shift(self, d):
        r = self.list.currentRow()
        pl = list(self.cfg.playlist)
        j = r + d
        if 0 <= r < len(pl) and 0 <= j < len(pl):
            pl[r], pl[j] = pl[j], pl[r]
            self._set_playlist(pl)
            self.refresh()
            self.list.setCurrentRow(j)

    def _remove(self):
        rows = sorted({self.list.row(it) for it in self.list.selectedItems()})
        if not rows:
            return
        cur = self._current_path()
        new = [it for i, it in enumerate(self.cfg.playlist) if i not in rows]
        if cur and all(it["path"] != cur for it in new):
            self.ctx.engine.player.stop()
        self._set_playlist(new)
        self.refresh()

    def _clear(self):
        self.ctx.engine.player.stop()
        self._set_playlist([])
        self.refresh()

    # ------------------------------------------------------------ отображение
    def refresh(self):
        pr = self.ctx.project
        self.channel.blockSignals(True)
        self.channel.clear()
        for c in pr.channels:
            self.channel.addItem(c.name, c.id)
        self.channel.setCurrentIndex(max(0, self.channel.findData(self.cfg.channel)))
        self.channel.blockSignals(False)
        for w, v in ((self.vol, int(round(self.cfg.volume * 100))), (self.xf, self.cfg.crossfade)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self.vol_l.setText(f"{int(round(self.cfg.volume * 100))}%")
        self.list.blockSignals(True)
        self.list.clear()
        total = 0.0
        for i, it in enumerate(self.cfg.playlist):
            d = float(it.get("dur") or 0)
            total += d
            li = QListWidgetItem(f"{i + 1:>3}  {it['title']}" + (f"   [{fmt_time(d)[:-3]}]" if d else ""))
            li.setData(Qt.UserRole, it)
            li.setToolTip(it["path"])
            if not Path(it["path"]).exists():
                li.setForeground(QColor(theme.DANGER))
                li.setToolTip("Файл не найден: " + it["path"])
            self.list.addItem(li)
        self.list.blockSignals(False)
        tt = (f"{int(total // 3600)}:{int(total % 3600 // 60):02d}:{int(total % 60):02d}" if total >= 3600
              else f"{int(total // 60)}:{int(total % 60):02d}")
        self.count_l.setText(f"ТРЕКОВ: {len(self.cfg.playlist)}" + (f" · {tt}" if total else ""))
        keys = player_keys(pr)
        parts = [f"{OPS[op].upper()}: {' / '.join(k)}" for op, k in keys.items() if op in OPS]
        self.keys_l.setText("КЛАВИШИ — " + "   ·   ".join(parts) if parts else "Клавиши для плеера пока не назначены.")
        self._last_sig = None
        self.tick()

    def tick(self):
        pl = self.ctx.engine.player
        cfg = self.cfg
        cur = pl.current()
        pos, dur = pl.position(), pl.duration()
        sig = (pl.state, cfg.index, cfg.shuffle, cfg.repeat, len(cfg.playlist))
        self.seek.set_pos(pos, dur)
        self.time_l.setText(f"{fmt_time(pos)} / {fmt_time(dur)}")
        if sig == self._last_sig:
            return
        self._last_sig = sig
        self.title.setText(cur["title"] if cur else "ПЛЕЙЛИСТ ПУСТ")
        self.sub.setText(Path(cur["path"]).name if cur else "Добавьте музыку кнопками справа или перетащите файлы")
        st = {"playing": "► ИГРАЕТ", "paused": "❚❚ ПАУЗА", "loading": "… ЗАГРУЗКА", "stopped": "■ ОСТАНОВЛЕН"}[pl.state]
        if pl.error:
            st += "  ·  " + pl.error.upper()
        ch = self.ctx.project.channel(cfg.channel)
        self.state_l.setText(st + (f"   →   КАНАЛ «{ch.name.upper()}»" if ch else ""))
        self.b_play.setText("❚❚" if pl.state in ("playing", "loading") else "►")
        self.b_shuffle.setChecked(cfg.shuffle)
        self.b_repeat.setText(REPEAT_TXT.get(cfg.repeat, ""))
        for i in range(self.list.count()):
            it = self.list.item(i)
            f = it.font()
            f.setBold(i == cfg.index)
            it.setFont(f)
            if i == cfg.index:
                it.setForeground(QColor(theme.ACCENT2))
            elif Path(cfg.playlist[i]["path"]).exists():
                it.setForeground(QColor(theme.TEXT))
