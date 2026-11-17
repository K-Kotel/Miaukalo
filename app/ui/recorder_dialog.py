"""Настройки блока записи."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QSpinBox, QVBoxLayout,
                               QWidget)

from app.audio.recorder import SLOT_PRESETS, available_formats, default_folder, rec_defaults
from app.model import Output, data_dir
from . import theme
from .widgets import caption

MODES = {
    "clip": ("КОРОТКИЕ ЗВУКИ → В САУНДПАД",
             "Зажал клавишу — записал — отпустил: тишина по краям обрежется, громкость выровняется, "
             "и звук сразу появится во вкладке «Звуки» (и в быстром микшере). Удобно, чтобы голосом "
             "с эффектами быстро наделать своих звуков."),
    "replay": ("ПОВТОР: ПОСЛЕДНИЕ СЕКУНДЫ → В САУНДПАД",
               "Блок всё время помнит последние секунды (или минуты) того, что в него приходит. "
               "Случилось смешное — жмёте клавишу, и этот кусок сразу становится звуком в «Звуках». "
               "Ничего заранее включать не нужно."),
    "file": ("ДЛИННАЯ ЗАПИСЬ → В ФАЙЛ",
             "Пишет всё, что приходит в блок, в файл — сколько угодно долго (стрим, войс, джем). "
             "Начать и закончить — клавишей или кнопкой ● на блоке."),
}


def open_folder(path: str):
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


def clip_folder() -> str:
    return str(data_dir() / "library" / "Записи")


class RecorderDialog(QDialog):
    def __init__(self, ctx, o: Output, parent=None, is_new=False):
        super().__init__(parent)
        self.ctx = ctx
        self.o = o
        self.is_new = is_new
        self.setWindowTitle("Блок записи")
        self.setMinimumWidth(600)
        cfg = rec_defaults(o.recorder or "clip")
        cfg.update(o.rec or {})
        self.cfg = cfg
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 12)
        bar = QLabel("  ● БЛОК ЗАПИСИ" + ("  ·  НОВЫЙ" if is_new else ""))
        bar.setStyleSheet(f"background: {theme.DANGER}; color: {theme.INK}; font-weight: 700; padding: 4px;")
        lay.addWidget(bar)

        top = QFormLayout()
        top.setContentsMargins(12, 8, 12, 0)
        top.setVerticalSpacing(8)
        self.name = QLineEdit(o.name)
        top.addRow(caption("Название"), self.name)
        mode_row = QVBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_btns = {}
        for k, (title, _hint) in MODES.items():
            rb = QRadioButton(title)
            rb.setChecked((o.recorder or "clip") == k)
            self.mode_group.addButton(rb)
            self.mode_btns[k] = rb
            rb.toggled.connect(self._mode_changed)
            mode_row.addWidget(rb)
        top.addRow(caption("Режим"), mode_row)
        lay.addLayout(top)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.ACCENT2}; padding: 2px 12px 6px 12px;")
        lay.addWidget(self.hint)

        # ---- короткие звуки
        self.clip_box = QWidget()
        cf = QFormLayout(self.clip_box)
        cf.setContentsMargins(12, 0, 12, 0)
        cf.setVerticalSpacing(6)
        self.channel = QComboBox()
        for c in ctx.project.channels:
            self.channel.addItem(c.name, c.id)
        ch = cfg.get("channel") or _guess_sfx_channel(ctx.project)
        self.channel.setCurrentIndex(max(0, self.channel.findData(ch)))
        cf.addRow(caption("Новые звуки — в канал"), self.channel)
        self.prefix = QLineEdit(cfg.get("prefix") or "Запись")
        cf.addRow(caption("Имя новых звуков"), self.prefix)
        self.slots = QComboBox()
        for k, v in SLOT_PRESETS.items():
            self.slots.addItem(v, k)
        self.slots.setCurrentIndex(max(0, self.slots.findData(cfg.get("slots", ""))))
        self.slots.setToolTip("Новой записи сразу дастся первая свободная клавиша из набора. Когда все заняты — "
                              "клавиша переходит от самой старой записи к новой (старая остаётся в «Звуках»).")
        cf.addRow(caption("Клавиши новым звукам"), self.slots)
        r = QHBoxLayout()
        self.trim = QCheckBox("Обрезать тишину по краям")
        self.trim.setChecked(bool(cfg.get("trim", True)))
        self.norm = QCheckBox("Выровнять громкость")
        self.norm.setChecked(bool(cfg.get("normalize", True)))
        r.addWidget(self.trim)
        r.addWidget(self.norm)
        r.addStretch(1)
        cf.addRow("", r)
        lay.addWidget(self.clip_box)

        # ---- повтор
        self.replay_box = QWidget()
        rf = QFormLayout(self.replay_box)
        rf.setContentsMargins(12, 0, 12, 0)
        self.replay_s = QSpinBox()
        self.replay_s.setRange(5, 600)
        self.replay_s.setSingleStep(5)
        self.replay_s.setSuffix(" с")
        self.replay_s.setValue(int(cfg.get("replay_s", 30)))
        self.replay_s.setToolTip("До 10 минут. Минута буфера занимает около 11 МБ памяти.")
        rf.addRow(caption("Помнить последние"), self.replay_s)
        lay.addWidget(self.replay_box)

        # ---- длинная запись
        self.file_box = QWidget()
        ff = QFormLayout(self.file_box)
        ff.setContentsMargins(12, 0, 12, 0)
        ff.setVerticalSpacing(6)
        fr = QHBoxLayout()
        self.folder = QLineEdit(cfg.get("folder") or default_folder())
        b_br = QPushButton("…")
        b_br.setFixedWidth(36)
        b_br.clicked.connect(self._browse)
        fr.addWidget(self.folder, 1)
        fr.addWidget(b_br)
        ff.addRow(caption("Папка"), fr)
        self.fmt = QComboBox()
        for k, (_f, _s, title) in available_formats().items():
            self.fmt.addItem(title, k)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(cfg.get("fmt", "wav"))))
        ff.addRow(caption("Формат"), self.fmt)
        self.add_pad = QCheckBox("Готовую запись тоже добавить в «Звуки»")
        self.add_pad.setChecked(bool(cfg.get("add_pad")) and o.recorder == "file")
        ff.addRow("", self.add_pad)
        lay.addWidget(self.file_box)

        # ---- общее
        common = QFormLayout()
        common.setContentsMargins(12, 6, 12, 0)
        self.pre = QSpinBox()
        self.pre.setRange(0, 2000)
        self.pre.setSingleStep(100)
        self.pre.setSuffix(" мс")
        self.pre.setValue(int(cfg.get("preroll_ms", 300)))
        self.pre.setToolTip("Сколько звука «до нажатия» тоже попадёт в запись — чтобы первый слог не пропал")
        common.addRow(caption("Предзахват"), self.pre)
        self.max = QSpinBox()
        self.max.setRange(0, 24 * 3600)
        self.max.setSuffix(" с")
        self.max.setSpecialValueText("без ограничения")
        self.max.setValue(int(cfg.get("max_s", 30 if (o.recorder or "clip") == "clip" else 0)))
        common.addRow(caption("Длина не больше"), self.max)
        self.common_box = QWidget()
        self.common_box.setLayout(common)
        lay.addWidget(self.common_box)
        tip = QLabel("Что записывать — решают провода: протяните к этому блоку провод от канала (например, "
                     "«Микрофон» — запишется уже с его эффектами), от выхода или шины.")
        tip.setObjectName("small")
        tip.setWordWrap(True)
        tip.setStyleSheet("padding: 6px 12px;")
        lay.addWidget(tip)

        btns = QHBoxLayout()
        btns.setContentsMargins(12, 6, 12, 0)
        if not is_new:
            b_del = QPushButton("УДАЛИТЬ БЛОК")
            b_del.setStyleSheet(f"QPushButton {{ color:{theme.DANGER}; border-color:{theme.DANGER}; background:{theme.BG}; }}"
                                f"QPushButton:hover {{ background:{theme.DANGER}; color:{theme.INK}; }}")
            b_del.clicked.connect(self._delete)
            btns.addWidget(b_del)
        b_open = QPushButton("ОТКРЫТЬ ПАПКУ")
        b_open.clicked.connect(lambda: open_folder(self.folder.text().strip() if self._mode() == "file" else clip_folder()))
        btns.addWidget(b_open)
        btns.addStretch(1)
        b_c = QPushButton("ОТМЕНА")
        b_c.clicked.connect(self.reject)
        b_ok = QPushButton("СОХРАНИТЬ")
        b_ok.setObjectName("primary")
        b_ok.clicked.connect(self._save)
        btns.addWidget(b_c)
        btns.addWidget(b_ok)
        lay.addLayout(btns)
        self._last_mode = self._mode()
        self._mode_changed()

    def _mode(self):
        return next((k for k, b in self.mode_btns.items() if b.isChecked()), "clip")

    def _mode_changed(self):
        m = self._mode()
        self.hint.setText(MODES[m][1])
        self.clip_box.setVisible(m in ("clip", "replay"))
        self.file_box.setVisible(m == "file")
        self.replay_box.setVisible(m == "replay")
        self.common_box.setVisible(m != "replay")
        if m != getattr(self, "_last_mode", m):       # разумные значения длины для режима
            self.max.setValue(30 if m == "clip" else 0)
            pre = self.prefix.text().strip()
            if m == "replay" and pre in ("", "Запись"):
                self.prefix.setText("Повтор")
            elif m != "replay" and pre == "Повтор":
                self.prefix.setText("Запись")
        self._last_mode = m
        self.adjustSize()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Папка для записей", self.folder.text())
        if d:
            self.folder.setText(d)

    def _save(self):
        o = self.o
        mode = self._mode()
        o.name = self.name.text().strip() or "Запись"
        o.recorder = mode
        o.bus = True
        o.rec = {
            "folder": self.folder.text().strip() or default_folder(), "fmt": self.fmt.currentData() or "wav",
            "channel": self.channel.currentData() or "", "prefix": self.prefix.text().strip() or "Запись",
            "trim": self.trim.isChecked(), "normalize": self.norm.isChecked(),
            "preroll_ms": self.pre.value(), "max_s": self.max.value(),
            "add_pad": True if mode in ("clip", "replay") else self.add_pad.isChecked(),
            "slots": self.slots.currentData() or "", "replay_s": self.replay_s.value(),
        }
        if self.is_new:
            self.ctx.project.outputs.append(o)
        self.ctx.apply()
        self.accept()

    def _delete(self):
        if QMessageBox.question(self, "Удалить блок", f"Удалить блок записи «{self.o.name}»?\n"
                                "Уже записанные файлы и звуки останутся.") != QMessageBox.Yes:
            return
        pr = self.ctx.project
        pr.outputs = [x for x in pr.outputs if x is not self.o]
        pr.bindings = [b for b in pr.bindings if not (b.action.get("type") == "record" and b.action.get("rec") == self.o.id)]
        pr.layout.pop(self.o.id, None)
        self.ctx.apply()
        self.accept()


def _guess_sfx_channel(project) -> str:
    for c in project.channels:
        if "эффект" in c.name.lower() or "звук" in c.name.lower():
            return c.id
    return project.channels[0].id if project.channels else ""
