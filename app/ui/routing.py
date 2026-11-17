"""Вкладка «Коммутация»: блочный конструктор «источники → выходы → выходы»."""
from __future__ import annotations

import json

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QGraphicsItem, QGraphicsObject,
                               QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSlider, QVBoxLayout,
                               QWidget)

from app.audio import devices
from app.model import Channel, LiveSource, Output, Pad, new_id
from . import theme
from .source_palette import MIME, SourcePalette
from app.audio.effects import effect_title
from .widgets import caption, ui_font

BUS = "__bus__"
SRC_W, SRC_H = 210, 70
OUT_W, OUT_H = 250, 96
PAD_W, PAD_H = SRC_W - 16, 34
SW_W, SW_ROW, SW_HEAD = 210, 18, 22
COMPACT_H = 24
COL_W = 330
PORT = 12


# ===================================================================== устройство выхода

def fill_device_combo(combo: QComboBox, o: Output, show_all=False):
    """Список устройств для выхода: системное, все устройства (кабели подписаны), «только шина»."""
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("Системное по умолчанию", ("", ""))
    found = o.bus
    for d in devices.list_devices("output", show_all):
        label = d["name"]
        if devices.is_virtual_cable(d["name"]):
            label = f"[{devices._cable_label(d['name'])[0]}]  {d['name']}"
        if show_all:
            label += f"  [{d['hostapi']}]"
        combo.addItem(label, (d["name"], d["hostapi"]))
        if not o.bus and d["name"] == o.device and (not o.hostapi or d["hostapi"] == o.hostapi):
            combo.setCurrentIndex(combo.count() - 1)
            found = True
    if o.device and not found:
        combo.addItem(f"! {o.device} (не подключено)", (o.device, o.hostapi))
        combo.setCurrentIndex(combo.count() - 1)
    combo.addItem("— ТОЛЬКО ШИНА (без устройства) —", (BUS, ""))
    if o.bus:
        combo.setCurrentIndex(combo.count() - 1)
    combo.blockSignals(False)


def apply_device_choice(o: Output, data):
    dn, ha = data or ("", "")
    if dn == BUS:
        o.bus = True
    else:
        o.bus = False
        o.device, o.hostapi = dn, ha


def device_caption(o: Output) -> tuple[str, str]:
    """(строка устройства, подсказка «что выбрать в программах»)."""
    if o.bus:
        return "ШИНА · без своего устройства", ""
    if not o.device:
        return "СИСТЕМНОЕ ПО УМОЛЧАНИЮ", ""
    if devices.is_virtual_cable(o.device):
        return o.device, devices.recording_side(o.device)
    return o.device, ""


class OutputDialog(QDialog):
    def __init__(self, ctx, o: Output, parent=None, is_new=False):
        super().__init__(parent)
        self.ctx = ctx
        self.o = o
        self.is_new = is_new
        self.setWindowTitle("Выход")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 12)
        bar = QLabel("  ВЫХОД" + ("  ·  НОВЫЙ" if is_new else ""))
        bar.setStyleSheet(f"background: {theme.ACCENT}; color: {theme.INK}; font-weight: 700; padding: 4px;")
        lay.addWidget(bar)
        form = QFormLayout()
        form.setContentsMargins(12, 8, 12, 0)
        form.setVerticalSpacing(8)
        self.name = QLineEdit(o.name)
        self.dev = QComboBox()
        fill_device_combo(self.dev, o, ctx.project.settings.show_all_hostapis)
        self.dev.currentIndexChanged.connect(self._hint)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 150)
        self.vol.setValue(int(o.volume * 100))
        self.vol_l = QLabel()
        self.vol.valueChanged.connect(lambda v: self.vol_l.setText(f"{v}%"))
        self.vol_l.setText(f"{self.vol.value()}%")
        self.vol_l.setFixedWidth(50)
        vr = QHBoxLayout()
        vr.addWidget(self.vol, 1)
        vr.addWidget(self.vol_l)
        self.en = QCheckBox("Включён")
        self.en.setChecked(o.enabled)
        form.addRow(caption("Название"), self.name)
        form.addRow(caption("Устройство"), self.dev)
        form.addRow(caption("Громкость"), vr)
        form.addRow("", self.en)
        lay.addLayout(form)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.ACCENT2}; padding: 4px 12px;")
        lay.addWidget(self.hint)
        btns = QHBoxLayout()
        btns.setContentsMargins(12, 6, 12, 0)
        if not is_new:
            b_del = QPushButton("УДАЛИТЬ ВЫХОД")
            b_del.setStyleSheet(f"QPushButton {{ color:{theme.DANGER}; border-color:{theme.DANGER}; background:{theme.BG}; }}"
                                f"QPushButton:hover {{ background:{theme.DANGER}; color:{theme.INK}; }}")
            b_del.clicked.connect(self._delete)
            btns.addWidget(b_del)
        b_test = QPushButton("ТЕСТ")
        b_test.setToolTip("Короткий сигнал на выбранное устройство")
        b_test.clicked.connect(self._test)
        btns.addWidget(b_test)
        n_fx = len(o.effects)
        b_fx = QPushButton(f"ЭФФЕКТЫ ({n_fx})" if n_fx else "ЭФФЕКТЫ…")
        b_fx.setToolTip("Эффекты на итоговый звук этого выхода / шины")
        b_fx.clicked.connect(self._fx)
        btns.addWidget(b_fx)
        btns.addStretch(1)
        b_c = QPushButton("ОТМЕНА")
        b_c.clicked.connect(self.reject)
        b_ok = QPushButton("СОХРАНИТЬ")
        b_ok.setObjectName("primary")
        b_ok.clicked.connect(self._save)
        btns.addWidget(b_c)
        btns.addWidget(b_ok)
        lay.addLayout(btns)
        self._hint()

    def _hint(self):
        dn, _ha = self.dev.currentData() or ("", "")
        if dn == BUS:
            self.hint.setText("Шина — точка сведения без своего устройства. Её звук можно переслать дальше "
                              "в другие выходы (провод от правого порта).")
        elif dn and devices.is_virtual_cable(dn):
            self.hint.setText(f"Это виртуальный кабель. В Дискорде / OBS / другой программе выберите микрофоном:\n"
                              f"«{devices.recording_side(dn)}»")
        else:
            self.hint.setText("")

    def _fx(self):
        from .effects_dialog import EffectsDialog
        if self.is_new:
            self._save()               # сначала сохраняем выход, потом открываем его эффекты
            EffectsDialog(self.ctx, self.o, self.parent()).exec()
            return
        EffectsDialog(self.ctx, self.o, self).exec()
        self.ctx.engine.sync_effects(self.o.id)

    def _test(self):
        dn, ha = self.dev.currentData() or ("", "")
        if dn == BUS:
            return
        try:
            devices.play_test_tone(dn, ha)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Проверка", str(e))

    def _save(self):
        o = self.o
        o.name = self.name.text().strip() or "Выход"
        apply_device_choice(o, self.dev.currentData())
        o.volume = self.vol.value() / 100
        o.enabled = self.en.isChecked()
        if self.is_new:
            self.ctx.project.outputs.append(o)
        self.ctx.apply()
        self.accept()

    def _delete(self):
        if QMessageBox.question(self, "Удалить выход", f"Удалить выход «{self.o.name}»?") != QMessageBox.Yes:
            return
        self.ctx.project.outputs = [x for x in self.ctx.project.outputs if x is not self.o]
        self.ctx.apply()
        self.accept()


# ===================================================================== графика

def _tri(p, r: QRectF, down: bool, col):
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(col))
    c = r.center()
    if down:
        pts = [QPointF(c.x() - 4, c.y() - 2), QPointF(c.x() + 4, c.y() - 2), QPointF(c.x(), c.y() + 3)]
    else:
        pts = [QPointF(c.x() - 2, c.y() - 4), QPointF(c.x() - 2, c.y() + 4), QPointF(c.x() + 3, c.y())]
    p.drawPolygon(QPolygonF(pts))


def _dur(sec) -> str:
    sec = int(round(float(sec or 0)))
    return f"{sec // 60}:{sec % 60:02d}" if sec >= 60 else f"{sec} С"


def _meter_pos(v):
    import math
    if v <= 1e-5:
        return 0.0
    return max(0.0, min(1.0, (20 * math.log10(v) + 48) / 48))


class Port(QGraphicsRectItem):
    def __init__(self, node, kind):
        super().__init__(-PORT / 2, -PORT / 2, PORT, PORT, node)
        self.node = node
        self.kind = kind          # in | out
        self.setZValue(3)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CrossCursor)
        self.hover = False
        self.setToolTip("Тяните отсюда к входу выхода, чтобы соединить" if kind == "out"
                        else "Сюда подключаются источники и другие выходы")

    def hoverEnterEvent(self, e):
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()

    def paint(self, p, _opt, _w=None):
        r = self.rect()
        dim = getattr(self.node, "port_dim", False)
        p.setPen(QPen(QColor(theme.INK), 1))
        p.setBrush(QColor("white" if self.hover else (theme.DIM if dim else
                                                      (theme.ACCENT2 if self.kind == "out" else theme.ACCENT))))
        p.drawRect(r)
        if self.kind == "in":
            p.fillRect(r.adjusted(3, 3, -3, -3), QColor(theme.INK))


class Node(QGraphicsObject):
    """Блок канала (src) или выхода (out)."""

    def __init__(self, page, kind, ref):
        super().__init__()
        self.page = page
        self.kind = kind
        self.ref = ref
        self.compact = ref in page.compact
        self.expanded = kind == "src" and ref in page.expanded
        self.w = SRC_W if kind == "src" else OUT_W
        self.h = COMPACT_H if self.compact else (SRC_H if kind == "src" else OUT_H)
        self.wires: list[Wire] = []
        self.pads: list[PadNode] = []
        self.lane = -1
        self.level = 0.0
        self.status = ""
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setCursor(Qt.SizeAllCursor)
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        o = self.obj()
        self.sw = o.switch if (kind == "out" and o is not None) else ""
        self.rec = o.recorder if (kind == "out" and o is not None) else ""
        self.rec_state: dict = {}
        self._rec_pressed = False
        self.in_ports: dict[str, Port] = {}
        self.out_ports: dict[str, Port] = {}
        if self.sw:
            self._setup_switch(o)
            return
        py = self.h / 2 if self.compact else self.h / 2 + 10
        self.out_port = Port(self, "out")
        self.out_port.setPos(self.w, py)
        self.in_port = None
        if kind == "out":
            self.in_port = Port(self, "in")
            self.in_port.setPos(0, py)
        if self.rec:                              # блок записи — только приёмник
            self.out_port.setVisible(False)
            self.setToolTip("Блок записи. Протяните к нему провода от того, что нужно записать.\n"
                            "● — начать/закончить (у коротких звуков: зажать и говорить). Двойной клик — настройки.")

    # ------------------------------------------------------------ блок записи
    def _btn_rec(self):
        if self.compact:
            return QRectF(self.w - 46, 2, 22, self.h - 4)
        return QRectF(self.w - 46, 26, 38, 38)

    def paint_rec(self, p, o):
        W, H = self.w, self.h
        st = self.rec_state or {}
        on = bool(st.get("on"))
        off = not o.enabled
        p.fillRect(QRectF(0, 0, W, H), QColor(theme.SURFACE))
        head = QRectF(0, 0, W, 20 if not self.compact else H)
        p.fillRect(head, QColor(theme.DANGER if on else (theme.DIM if off else theme.SURFACE3)))
        p.setFont(ui_font(8, QFont.Bold))
        p.setPen(QColor(theme.INK if on else theme.DANGER))
        name = p.fontMetrics().elidedText("● " + o.name.upper(), Qt.ElideRight, int(W - 80))
        p.drawText(QRectF(6, 0, W - 80, head.height()), Qt.AlignVCenter | Qt.AlignLeft, name)
        bc = self._btn_compact()
        bc.moveTop((head.height() - 16) / 2)
        p.setPen(QPen(QColor(theme.INK if on else theme.DANGER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(bc.adjusted(2, 2, -2, -2))
        p.drawLine(bc.center() + QPointF(-3, 0), bc.center() + QPointF(3, 0))
        if self.compact:
            p.drawLine(bc.center() + QPointF(0, -3), bc.center() + QPointF(0, 3))
        t = float(st.get("t", 0.0))
        clock = f"{int(t // 60):02d}:{t % 60:04.1f}"
        # кнопка ●/■
        br = self._btn_rec()
        p.setPen(QPen(QColor(theme.DANGER), 1))
        p.setBrush(QColor(theme.DANGER if on else theme.BG))
        if self.compact:
            p.drawRect(br)
        else:
            p.drawEllipse(br)
        p.setBrush(Qt.NoBrush)
        c = br.center()
        if on:
            p.fillRect(QRectF(c.x() - 5, c.y() - 5, 10, 10), QColor(theme.INK))
        elif self.rec == "replay":               # ⟲ — «сохранить момент»
            p.setPen(QPen(QColor(theme.DANGER), 2))
            p.drawArc(QRectF(c.x() - 7, c.y() - 7, 14, 14), 60 * 16, 290 * 16)
            p.setBrush(QColor(theme.DANGER))
            p.drawPolygon(QPolygonF([QPointF(c.x() + 2, c.y() - 10), QPointF(c.x() + 7, c.y() - 6),
                                     QPointF(c.x() + 1, c.y() - 3)]))
            p.setBrush(Qt.NoBrush)
        else:
            p.setBrush(QColor(theme.DANGER))
            p.drawEllipse(c, 5, 5)
            p.setBrush(Qt.NoBrush)
        if self.compact:
            if on:
                p.setPen(QColor(theme.INK))
                p.drawText(QRectF(W - 120, 0, 70, H), Qt.AlignVCenter | Qt.AlignRight, clock)
        else:
            cfg = o.rec or {}
            p.setFont(ui_font(7.5, QFont.DemiBold))
            p.setPen(QColor(theme.MUTED))
            if self.rec == "clip":
                line = "КОРОТКИЕ ЗВУКИ → САУНДПАД"
            elif self.rec == "replay":
                line = f"ПОВТОР {_dur(st.get('cap', cfg.get('replay_s', 30)))} → САУНДПАД"
            else:
                line = f"ЗАПИСЬ В ФАЙЛ · {str(cfg.get('fmt', 'wav')).upper()}"
            p.drawText(QRectF(8, 24, W - 64, 14), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(line, Qt.ElideRight, int(W - 64)))
            p.setFont(ui_font(9, QFont.Bold))
            if on:
                p.setPen(QColor(theme.DANGER))
                txt = "● REC  " + clock
            elif st.get("busy"):
                p.setPen(QColor(theme.ACCENT2))
                txt = "СОХРАНЯЮ…"
            elif self.rec == "replay" and not off:
                p.setPen(QColor(theme.ACCENT2))
                txt = f"БУФЕР {_dur(st.get('buf', 0))}"
            elif st.get("error"):
                p.setPen(QColor(theme.DANGER))
                txt = "! ОШИБКА"
            else:
                p.setPen(QColor(theme.DIM if off else theme.ACCENT2))
                txt = "[ВЫКЛ]" if off else "ГОТОВ"
            p.drawText(QRectF(8, 38, W - 64, 18), Qt.AlignLeft | Qt.AlignVCenter, txt)
            keys = [b.trigger.label().upper() for b in self.page.ctx.project.bindings
                    if b.action.get("type") == "record" and b.action.get("rec") == o.id]
            p.setFont(ui_font(7.5, QFont.DemiBold))
            p.setPen(QColor(theme.ACCENT2 if keys else theme.DIM))
            p.drawText(QRectF(8, 58, W - 64, 14), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(("КЛАВИША: " + " / ".join(keys)) if keys else "ПКМ — НАЗНАЧИТЬ КЛАВИШУ",
                                                  Qt.ElideRight, int(W - 64)))
            y = H - 12
            segs = int((W - 16) // 6)
            lit = int(round(_meter_pos(self.level) * segs))
            for i in range(segs):
                p.fillRect(QRectF(8 + i * 6, y, 4, 5), QColor(theme.DANGER if i < lit else theme.SURFACE3))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.DANGER if (self.isUnderMouse() or on) else theme.BORDER), 1))
        p.drawRect(QRectF(0, 0, W, H))

    # ------------------------------------------------------------ переключатель
    def _setup_switch(self, o):
        """Порт на каждое положение: у 1→N — выходы справа, у N→1 — входы слева. Плюс свободный порт «+»."""
        pr = self.page.ctx.project
        self.ports_list = pr.switch_ports(o)
        self.compact = False
        self.w = SW_W
        rows = len(self.ports_list) + 1
        self.h = SW_HEAD + rows * SW_ROW + 22
        if self.sw == "split":
            self.in_port = Port(self, "in")
            self.in_port.setPos(0, SW_HEAD + SW_ROW / 2)
            for i, pid in enumerate(self.ports_list):
                pt = Port(self, "out")
                pt.setPos(self.w, SW_HEAD + i * SW_ROW + SW_ROW / 2)
                self.out_ports[pid] = pt
            self.out_port = Port(self, "out")         # «+» — новое положение
            self.out_port.setPos(self.w, SW_HEAD + len(self.ports_list) * SW_ROW + SW_ROW / 2)
        else:
            for i, pid in enumerate(self.ports_list):
                pt = Port(self, "in")
                pt.setPos(0, SW_HEAD + i * SW_ROW + SW_ROW / 2)
                self.in_ports[pid] = pt
            self.in_port = Port(self, "in")           # «+» — новый вход
            self.in_port.setPos(0, SW_HEAD + len(self.ports_list) * SW_ROW + SW_ROW / 2)
            self.out_port = Port(self, "out")
            self.out_port.setPos(self.w, SW_HEAD + SW_ROW / 2)

    def switch_row_at(self, pos):
        if not self.sw:
            return None
        i = int((pos.y() - SW_HEAD) // SW_ROW)
        if pos.y() >= SW_HEAD and 0 <= i < len(self.ports_list) and 6 < pos.x() < self.w - 6:
            return i
        return None

    def paint_switch(self, p, o):
        W, H = self.w, self.h
        pr = self.page.ctx.project
        p.fillRect(QRectF(0, 0, W, H), QColor(theme.SURFACE))
        p.fillRect(QRectF(0, 0, W, SW_HEAD), QColor(theme.ACCENT2))
        p.setFont(ui_font(8, QFont.Bold))
        p.setPen(QColor(theme.INK))
        tag = "1→N" if self.sw == "split" else "N→1"
        p.fillRect(QRectF(0, 0, 34, SW_HEAD), QColor(theme.INK))
        p.setPen(QColor(theme.ACCENT2))
        p.drawText(QRectF(0, 0, 34, SW_HEAD), Qt.AlignCenter, tag)
        p.setPen(QColor(theme.INK))
        st = "ВЫКЛ" if o.active < 0 or not self.ports_list else f"#{o.active + 1}"
        p.drawText(QRectF(40, 0, W - 90, SW_HEAD), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(o.name.upper(), Qt.ElideRight, int(W - 96)))
        p.drawText(QRectF(W - 52, 0, 46, SW_HEAD), Qt.AlignVCenter | Qt.AlignRight, st)
        keys = {}
        for b in pr.bindings:
            a = b.action
            if a.get("type") == "switch" and a.get("switch") == o.id:
                keys.setdefault(a.get("op") if a.get("op") not in ("set", "toggle", "hold") else int(a.get("index", 0)),
                                []).append(b.trigger.label().upper())
        p.setFont(ui_font(7.5, QFont.DemiBold))
        for i, pid in enumerate(self.ports_list):
            r = QRectF(6, SW_HEAD + i * SW_ROW + 1, W - 12, SW_ROW - 2)
            act = i == o.active
            if act:
                p.fillRect(r, QColor(theme.ACCENT))
            p.setPen(QColor(theme.INK if act else theme.TEXT))
            p.drawText(r.adjusted(4, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(f"{i + 1}  {pr.node_name(pid).upper()}", Qt.ElideRight, int(r.width() - 60)))
            k = keys.get(i)
            if k:
                p.setPen(QColor(theme.INK if act else theme.ACCENT2))
                p.drawText(r.adjusted(0, 0, -4, 0), Qt.AlignVCenter | Qt.AlignRight, k[0])
        r = QRectF(6, SW_HEAD + len(self.ports_list) * SW_ROW + 1, W - 12, SW_ROW - 2)
        p.setPen(QColor(theme.DIM))
        p.drawText(r.adjusted(4, 0, 0, 0), Qt.AlignVCenter | (Qt.AlignRight if self.sw == "split" else Qt.AlignLeft),
                   "+ ВЫХОД  " if self.sw == "split" else "+ ВХОД")
        foot = []
        for op, sym in (("next", "▸"), ("prev", "◂"), ("off", "■")):
            if keys.get(op):
                foot.append(f"{sym} {keys[op][0]}")
        p.setPen(QColor(theme.ACCENT2 if foot else theme.DIM))
        p.drawText(QRectF(8, H - 20, W - 16, 18), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText("   ".join(foot) if foot else "ПКМ — ГОРЯЧИЕ КЛАВИШИ", Qt.ElideRight, int(W - 16)))
        lvl = _meter_pos(self.level)
        p.fillRect(QRectF(0, H - 2, W * lvl, 2), QColor(theme.ACCENT))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.ACCENT2 if self.isUnderMouse() else theme.BORDER), 1))
        p.drawRect(QRectF(0, 0, W, H))

    # геометрия
    def total_h(self):
        return self.h + (len(self.pads) * (PAD_H + 3) + 4 if self.pads else 0)

    def _btn_compact(self):
        return QRectF(self.w - 20, 2, 16, 16)

    def _btn_expand(self):
        return QRectF(self.w - 58, 2, 36, 16)

    def obj(self):
        pr = self.page.ctx.project
        return pr.channel(self.ref) if self.kind == "src" else pr.output(self.ref)

    def boundingRect(self):
        return QRectF(-1, -1, self.w + 2, self.h + 2)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            g = 10
            return QPointF(round(value.x() / g) * g, round(value.y() / g) * g)
        if change == QGraphicsItem.ItemPositionHasChanged:
            for w in self.wires:
                w.update_path()
            for pn in self.pads:
                for w in pn.wires:
                    w.update_path()
            self.page.moved = True
        return super().itemChange(change, value)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.sw:
            i = self.switch_row_at(e.pos())
            if i is not None:
                o = self.obj()
                self.page.ctx.engine.switch_set(o.id, -1 if o.active == i else i)
                self.update()
                e.accept()
                return
            super().mousePressEvent(e)
            return
        if e.button() == Qt.LeftButton and self.rec and self._btn_rec().contains(e.pos()):
            eng = self.page.ctx.engine
            if self.rec == "clip":                # короткие звуки: пишем, пока держишь кнопку
                self._rec_pressed = True
                eng.rec_command(self.ref, "start")
            elif self.rec == "replay":            # повтор: сохранить последние секунды
                eng.rec_command(self.ref, "save")
            else:
                eng.rec_command(self.ref, "toggle")
            e.accept()
            return
        if e.button() == Qt.LeftButton:
            if self._btn_compact().contains(e.pos()):
                self.page.toggle_compact(self)
                e.accept()
                return
            if self.kind == "src" and self.page.pad_count(self.ref) and self._btn_expand().contains(e.pos()):
                self.page.toggle_expand(self)
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._rec_pressed:
            self._rec_pressed = False
            self.page.ctx.engine.rec_command(self.ref, "stop")
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def paint(self, p, _opt, _w=None):
        o = self.obj()
        if o is None:
            return
        if self.sw:
            self.paint_switch(p, o)
            return
        if self.rec:
            self.paint_rec(p, o)
            return
        W, H = self.w, self.h
        off = self.kind == "out" and not o.enabled
        bus = self.kind == "out" and o.bus
        p.fillRect(QRectF(0, 0, W, H), QColor(theme.SURFACE))
        head = QRectF(0, 0, W, 20 if not self.compact else H)
        if bus:
            p.fillRect(head, QColor(theme.BG))
        else:
            p.fillRect(head, QColor(theme.DIM if off else (theme.ACCENT if self.kind == "src" else theme.ACCENT2)))
        ink = QColor(theme.ACCENT if bus else theme.INK)
        tx = 6
        if self.kind == "src":
            p.fillRect(QRectF(0, 0, 18, head.height()), QColor(o.color))
            tx = 24
        right = 24 + (40 if (self.kind == "src" and self.page.pad_count(self.ref)) else 0)
        p.setFont(ui_font(8, QFont.Bold))
        p.setPen(ink)
        label = o.name.upper() + ("  · ШИНА" if bus else "")
        name = p.fontMetrics().elidedText(label, Qt.ElideRight, int(W - tx - right))
        p.drawText(QRectF(tx, 0, W - tx - right, head.height()), Qt.AlignVCenter | Qt.AlignLeft, name)
        # кнопка «свернуть в строку»
        bc = self._btn_compact()
        bc.moveTop((head.height() - 16) / 2)
        p.setPen(QPen(ink, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(bc.adjusted(2, 2, -2, -2))
        if self.compact:
            p.drawLine(bc.center() + QPointF(-3, 0), bc.center() + QPointF(3, 0))
            p.drawLine(bc.center() + QPointF(0, -3), bc.center() + QPointF(0, 3))
        else:
            p.drawLine(bc.center() + QPointF(-3, 0), bc.center() + QPointF(3, 0))
        # кнопка «раскрыть звуки»
        if self.kind == "src":
            n = self.page.pad_count(self.ref)
            if n:
                be = self._btn_expand()
                be.moveTop((head.height() - 16) / 2)
                p.fillRect(be, QColor(theme.INK))
                _tri(p, QRectF(be.left(), be.top(), 14, 16), self.expanded, theme.ACCENT)
                p.setPen(QColor(theme.ACCENT))
                p.setFont(ui_font(7.5, QFont.Bold))
                p.drawText(QRectF(be.left() + 13, be.top(), 22, 16), Qt.AlignCenter, str(n))
        if self.compact:
            seg_w = W
            lvl = _meter_pos(self.level)
            p.fillRect(QRectF(0, H - 3, seg_w * lvl, 3), QColor(theme.INK if not bus else theme.ACCENT))
        else:
            p.setFont(ui_font(7.5, QFont.DemiBold))
            if self.kind == "src":
                n = self.page.pad_count(self.ref)
                own = sum(1 for x in self.page.ctx.project.pads if x.channel == o.id and x.route_own)
                fx = sum(1 for s in o.effects if s.enabled)
                line = f"ГРОМК {int(o.volume * 100)}%  ·  ЗВУКОВ {n}" + (f"  ·  FX {fx}" if fx else "")
                if self.page.ctx.project.player.channel == o.id:
                    line += "  ·  ♪ ПЛЕЕР"
                p.setPen(QColor(theme.DANGER if o.muted else theme.MUTED))
                p.drawText(QRectF(8, 24, W - 16, 14), Qt.AlignLeft | Qt.AlignVCenter, "ЗАГЛУШЕН" if o.muted else line)
                if own:
                    p.setPen(QColor(theme.ACCENT2))
                    p.drawText(QRectF(8, 38, W - 16, 14), Qt.AlignLeft | Qt.AlignVCenter, f"СВОЯ РАЗВОДКА: {own}")
            else:
                dev, mic = device_caption(o)
                p.setPen(QColor(theme.MUTED))
                p.drawText(QRectF(8, 24, W - 16, 14), Qt.AlignLeft | Qt.AlignVCenter,
                           p.fontMetrics().elidedText("→ " + dev, Qt.ElideRight, int(W - 16)))
                if mic:
                    p.setPen(QColor(theme.ACCENT2))
                    p.drawText(QRectF(8, 39, W - 16, 14), Qt.AlignLeft | Qt.AlignVCenter,
                               p.fontMetrics().elidedText("МИКРОФОН: " + mic, Qt.ElideRight, int(W - 16)))
                st = "[ВЫКЛ]" if off else ("[ТИХО]" if o.muted else ("[ШИНА]" if bus else ("[СБОЙ]" if self.status else "[ OK ]")))
                p.setPen(QColor(theme.DIM if off else (theme.DANGER if (self.status or o.muted) else theme.ACCENT2)))
                p.drawText(QRectF(8, 55, 60, 14), Qt.AlignLeft | Qt.AlignVCenter, st)
                if o.effects:
                    on = [effect_title(s.type) for s in o.effects if s.enabled]
                    p.setPen(QColor(theme.ACCENT if on else theme.DIM))
                    p.drawText(QRectF(66, 55, 100, 14), Qt.AlignLeft | Qt.AlignVCenter,
                               p.fontMetrics().elidedText(f"FX {len(on)}/{len(o.effects)}", Qt.ElideRight, 100))
                p.setPen(QColor(theme.MUTED))
                p.drawText(QRectF(70, 55, W - 78, 14), Qt.AlignRight | Qt.AlignVCenter, f"ГРОМК {int(o.volume * 100)}%")
            y = H - 12
            segs = int((W - 16) // 6)
            lit = int(round(_meter_pos(self.level) * segs))
            for i in range(segs):
                frac = i / segs
                col = theme.DANGER if frac > 0.88 else (theme.ACCENT2 if frac > 0.7 else theme.ACCENT)
                p.fillRect(QRectF(8 + i * 6, y, 4, 5), QColor(col if i < lit else theme.SURFACE3))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.ACCENT if self.isUnderMouse() else theme.BORDER), 1))
        p.drawRect(QRectF(0, 0, W, H))

    def hoverEnterEvent(self, e):
        self.update()

    def hoverLeaveEvent(self, e):
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.page.edit_node(self)

    def contextMenuEvent(self, e):
        self.page.node_menu(self, e.screenPos())


class PadNode(QGraphicsObject):
    """Отдельный звук внутри раскрытого канала."""

    def __init__(self, page, parent: Node, pad, index: int):
        super().__init__(parent)
        self.page = page
        self.parent_node = parent
        self.ref = pad.id
        self.kind = "pad"
        self.w, self.h = PAD_W, PAD_H
        self.wires: list[Wire] = []
        self.lane = -1
        self.playing = False
        self.setPos(16, parent.h + 4 + index * (PAD_H + 3))
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self.out_port = Port(self, "out")
        self.out_port.setPos(self.w, self.h / 2)
        self.in_port = None
        self.setToolTip("Тяните от правого ■ к выходу — у звука будет своя разводка (провода канала для него "
                        "перестанут действовать). Двойной клик — настройки звука.")

    @property
    def port_dim(self):
        pad = self.obj()
        return not (pad and pad.route_own)

    def obj(self):
        return self.page.ctx.project.pad(self.ref)

    def boundingRect(self):
        return QRectF(-12, -4, self.w + 13, self.h + 8)

    def paint(self, p, _opt, _w=None):
        pad = self.obj()
        if pad is None:
            return
        W, H = self.w, self.h
        # «ветка» дерева от канала
        p.setPen(QPen(QColor(theme.DIM), 1))
        p.drawLine(QPointF(-8, -4), QPointF(-8, H / 2))
        p.drawLine(QPointF(-8, H / 2), QPointF(0, H / 2))
        p.fillRect(QRectF(0, 0, W, H), QColor(theme.SURFACE3 if self.playing else theme.SURFACE2))
        p.fillRect(QRectF(0, 0, 6, H), QColor(pad.color))
        p.setFont(ui_font(8, QFont.Bold))
        p.setPen(QColor(theme.ACCENT if self.playing else theme.TEXT))
        p.drawText(QRectF(12, 2, W - 20, 15), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(("► " if self.playing else "") + pad.name.upper(), Qt.ElideRight, int(W - 20)))
        p.setFont(ui_font(7, QFont.DemiBold))
        if pad.route_own:
            names = [self.page.ctx.project.output(o).name for o in pad.outputs if self.page.ctx.project.output(o)]
            line = "СВОЯ → " + (", ".join(names) if names else "НИКУДА")
            p.setPen(QColor(theme.ACCENT2))
        else:
            line = "КАК ГРУППА" + (f" (БЕЗ {len(pad.exclude_outputs)})" if pad.exclude_outputs else "")
            p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(12, 17, W - 20, 14), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(line, Qt.ElideRight, int(W - 20)))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.ACCENT if (self.playing or self.isUnderMouse()) else theme.BORDER), 1))
        p.drawRect(QRectF(0, 0, W, H))

    def hoverEnterEvent(self, e):
        self.update()

    def hoverLeaveEvent(self, e):
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.page.edit_pad(self.ref)

    def contextMenuEvent(self, e):
        pad = self.obj()
        m = QMenu()
        m.addAction("Настроить звук…", lambda: self.page.edit_pad(self.ref))
        m.addAction("Сыграть / остановить", lambda: (self.page.ctx.engine.pad_event(self.ref, "press"),
                                                     self.page.ctx.engine.pad_event(self.ref, "release")))
        if pad and pad.route_own:
            m.addSeparator()
            m.addAction("Вернуть разводку группы", lambda: self.page.set_pad_own(self.ref, False))
        m.exec(e.screenPos())


class Frame(QGraphicsObject):
    """Рамка-группа блоков. Свёрнутая рамка показывается одним блоком, провода идут в её край."""
    HEAD = 22

    def __init__(self, page, d: dict):
        super().__init__()
        self.page = page
        self.d = d
        self.setZValue(2.5 if d.get("collapsed") else -1)   # свёрнутая — поверх блоков
        self.setAcceptHoverEvents(True)
        self.setPos(d["x"], d["y"])
        self._mode = None
        self._start = None

    @property
    def collapsed(self):
        return bool(self.d.get("collapsed"))

    def rect(self):
        if self.collapsed:
            n = len(self.d.get("members", []))
            return QRectF(0, 0, 230, self.HEAD + 8 + 14 * min(n, 6) + (14 if n > 6 else 0))
        return QRectF(0, 0, self.d["w"], self.d["h"])

    def boundingRect(self):
        return self.rect().adjusted(-8, -2, 8, 2)

    def port_pos(self, kind):
        r = self.rect()
        x = r.left() if kind == "in" else r.right()
        return self.mapToScene(QPointF(x, self.HEAD + 8))

    def _toggle_rect(self):
        r = self.rect()
        return QRectF(r.right() - 22, 3, 16, 16)

    def _grip(self):
        r = self.rect()
        return QRectF(r.right() - 14, r.bottom() - 14, 14, 14)

    def paint(self, p, _opt, _w=None):
        r = self.rect()
        members = self.d.get("members", [])
        if self.collapsed:
            p.fillRect(r, QColor(theme.SURFACE))
            p.fillRect(QRectF(0, 0, r.width(), self.HEAD), QColor(theme.ACCENT2))
            p.setPen(QColor(theme.INK))
            ink = theme.INK
        else:
            pen = QPen(QColor(theme.DIM), 1, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(QColor(255, 154, 31, 10))
            p.drawRect(r)
            p.fillRect(QRectF(0, 0, r.width(), self.HEAD), QColor(theme.SURFACE3))
            ink = theme.ACCENT2
        p.setFont(ui_font(8, QFont.Bold))
        p.fillRect(QRectF(7, 7, 8, 8), QColor(ink))
        p.setPen(QColor(ink))
        title = str(self.d.get("name") or "ГРУППА").upper()
        if self.collapsed:
            title += f"  [{len(members)}]"
        p.drawText(QRectF(20, 0, r.width() - 46, self.HEAD), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(title, Qt.ElideRight, int(r.width() - 46)))
        _tri(p, self._toggle_rect(), not self.collapsed, ink)
        if self.collapsed:
            p.setFont(ui_font(7.5, QFont.DemiBold))
            p.setPen(QColor(theme.MUTED))
            for i, mid in enumerate(members[:6]):
                n = self.page.nodes.get(mid)
                o = n.obj() if n else None
                name = (o.name if o else "?").upper()
                p.drawText(QRectF(10, self.HEAD + 4 + i * 14, r.width() - 20, 14), Qt.AlignVCenter | Qt.AlignLeft,
                           p.fontMetrics().elidedText(("◂ " if n and n.kind == "src" else "▸ ") + name, Qt.ElideRight,
                                                      int(r.width() - 20)))
            if len(members) > 6:
                p.drawText(QRectF(10, self.HEAD + 4 + 6 * 14, r.width() - 20, 14), Qt.AlignVCenter, f"… ещё {len(members) - 6}")
            for kind in ("in", "out"):
                pp = self.mapFromScene(self.port_pos(kind))
                p.fillRect(QRectF(pp.x() - 5, pp.y() - 5, 10, 10), QColor(theme.ACCENT2))
            p.setPen(QPen(QColor(theme.ACCENT2), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
        else:
            g = self._grip()
            p.setPen(QPen(QColor(theme.DIM), 1))
            for k in (4, 8, 12):
                p.drawLine(QPointF(g.right() - k, g.bottom()), QPointF(g.right(), g.bottom() - k))

    def hoverMoveEvent(self, e):
        if not self.collapsed and self._grip().contains(e.pos()):
            self.setCursor(Qt.SizeFDiagCursor)
        elif e.pos().y() <= self.HEAD:
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.unsetCursor()

    def shape(self):
        path = QPainterPath()
        r = self.rect()
        if self.collapsed:
            path.addRect(r)
        else:          # ловим мышь только шапкой и уголком, чтобы не мешать блокам внутри
            path.addRect(QRectF(0, 0, r.width(), self.HEAD))
            path.addRect(self._grip())
        return path

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            e.ignore()
            return
        if self._toggle_rect().contains(e.pos()):
            self.page.toggle_frame(self)
            return
        if not self.collapsed and self._grip().contains(e.pos()):
            self._mode = "resize"
            self._start = (e.scenePos(), self.d["w"], self.d["h"])
            return
        self._mode = "move"
        members = self.d.get("members", []) if self.collapsed else self.page.frame_members(self)
        self._start = (e.scenePos(), self.pos(), {m: self.page.nodes[m].pos() for m in members if m in self.page.nodes})

    def mouseMoveEvent(self, e):
        if self._mode == "move":
            p0, pos0, mem = self._start
            d = e.scenePos() - p0
            d = QPointF(round(d.x() / 10) * 10, round(d.y() / 10) * 10)
            self.setPos(pos0 + d)
            for mid, mp in mem.items():
                self.page.nodes[mid].setPos(mp + d)
            self.page.update_wires()
        elif self._mode == "resize":
            p0, w0, h0 = self._start
            d = e.scenePos() - p0
            self.prepareGeometryChange()
            self.d["w"] = max(200, round((w0 + d.x()) / 10) * 10)
            self.d["h"] = max(100, round((h0 + d.y()) / 10) * 10)
            self.update()

    def mouseReleaseEvent(self, e):
        if self._mode:
            self.d["x"], self.d["y"] = self.pos().x(), self.pos().y()
            self._mode = None
            self.page.save_layout()

    def mouseDoubleClickEvent(self, e):
        if e.pos().y() <= self.HEAD:
            self.page.rename_frame(self)

    def contextMenuEvent(self, e):
        m = QMenu()
        m.addAction("Переименовать…", lambda: self.page.rename_frame(self))
        m.addAction("Развернуть" if self.collapsed else "Свернуть", lambda: self.page.toggle_frame(self))
        m.addSeparator()
        m.addAction("Убрать рамку (блоки останутся)", lambda: self.page.delete_frame(self))
        m.exec(e.screenPos())


class Wire(QGraphicsPathItem):
    def __init__(self, page, src, dst: Node, kind: str, pad_id: str = ""):
        super().__init__()
        self.page = page
        self.src = src
        self.dst = dst
        self.kind = kind            # ch | send | pad
        self.pad_id = pad_id
        self.active = False
        self.hover = False
        self.setZValue(1)
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        tip = "Клик — выбрать, Delete или правая кнопка — отключить"
        if kind == "pad":
            pad = page.ctx.project.pad(pad_id)
            tip = f"Звук «{pad.name if pad else '?'}» — своя разводка.\n" + tip
        self.setToolTip(tip)
        src.wires.append(self)
        dst.wires.append(self)
        self.update_path()

    @staticmethod
    def route(a: QPointF, b: QPointF, lane: int = -1) -> QPainterPath:
        path = QPainterPath(a)
        if b.x() - a.x() >= 40:
            if lane >= 0:
                mx = min(a.x() + 16 + (lane % 10) * 9, b.x() - 16)
            else:
                mx = round((a.x() + b.x()) / 2 / 5) * 5
            path.lineTo(mx, a.y())
            path.lineTo(mx, b.y())
        else:
            ymid = max(a.y(), b.y()) + 70
            path.lineTo(a.x() + 20, a.y())
            path.lineTo(a.x() + 20, ymid)
            path.lineTo(b.x() - 20, ymid)
            path.lineTo(b.x() - 20, b.y())
        path.lineTo(b)
        return path

    def update_path(self):
        a, fa = self.page.anchor(self.src, "out", self.dst.ref)
        b, fb = self.page.anchor(self.dst, "in", self.pad_id if self.kind == "pad" else self.src.ref)
        if fa is not None and fa is fb:       # оба конца спрятаны в одной свёрнутой рамке
            self.setVisible(False)
            return
        self.setVisible(True)
        self.setPath(self.route(a, b, getattr(self.src, "lane", -1)))

    def shape(self):
        s = QPainterPathStroker()
        s.setWidth(12)
        return s.createStroke(self.path())

    def hoverEnterEvent(self, e):
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()

    def paint(self, p, _opt, _w=None):
        if self.kind == "ch":
            ch = self.src.obj()
            col = QColor(ch.color if ch else theme.ACCENT)
        elif self.kind == "pad":
            pad = self.page.ctx.project.pad(self.pad_id)
            col = QColor(pad.color if pad else theme.ACCENT2)
        else:
            col = QColor(theme.ACCENT2)
        sel = self.isSelected()
        if not self.active and not sel and not self.hover:
            col.setAlpha(120)
        pen = QPen(QColor("white") if sel else col, 3 if (sel or self.hover) else (1.5 if self.kind == "pad" else 2))
        pen.setCapStyle(Qt.FlatCap)
        pen.setJoinStyle(Qt.MiterJoin)
        if self.kind == "send":
            pen.setDashPattern([3, 3])
        elif self.kind == "pad":
            pen.setDashPattern([8, 2, 2, 2])
        elif self.active:
            pen.setDashPattern([6, 3])
        if self.active:
            pen.setDashOffset(-self.page.phase)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(self.path())
        b = self.path().pointAtPercent(1.0)
        p.fillRect(QRectF(b.x() - 9, b.y() - 3, 3, 6), QColor("white") if sel else col)

    def contextMenuEvent(self, e):
        m = QMenu()
        m.addAction("Отключить", lambda: self.page.disconnect(self))
        m.exec(e.screenPos())


class RoutingView(QGraphicsView):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setRenderHint(QPainter.Antialiasing, False)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setStyleSheet(f"QGraphicsView {{ background: {theme.BG}; border: 1px solid {theme.BORDER}; }}")
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setAcceptDrops(True)
        self._drag = None
        self._temp = None
        self._drop_hint = None

    def drawBackground(self, p, rect):
        p.fillRect(rect, QColor(theme.BG))
        step = 20
        x0 = int(rect.left()) - int(rect.left()) % step
        y0 = int(rect.top()) - int(rect.top()) % step
        p.setPen(QPen(QColor(theme.SURFACE3), 1))
        for x in range(x0, int(rect.right()) + step, step):
            for y in range(y0, int(rect.bottom()) + step, step):
                p.drawPoint(x, y)

    def _port_at(self, pos):
        for it in self.items(pos):
            if isinstance(it, Port) and it.isVisible():
                return it
        return None

    def _node_at(self, pos, kinds=("src", "out", "pad")):
        for it in self.items(pos):
            if isinstance(it, Port):
                it = it.node
            if isinstance(it, (Node, PadNode)) and it.isVisible() and it.kind in kinds:
                return it
        return None

    # --- провода
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            port = self._port_at(e.position().toPoint())
            if port and port.kind == "out":
                self._drag = port.node
                self._drag_port = port
                self._temp = QGraphicsPathItem()
                self._temp.setPen(QPen(QColor("white"), 2, Qt.DashLine))
                self._temp.setZValue(10)
                self.scene().addItem(self._temp)
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag:
            a = self._drag_port.scenePos()
            b = self.mapToScene(e.position().toPoint())
            self._temp.setPath(Wire.route(a, b))
            tgt = self._node_at(e.position().toPoint(), ("out",))
            ok = tgt is not None and self.page.can_connect(self._drag, tgt)
            self._temp.setPen(QPen(QColor(theme.ACCENT2 if ok else "white"), 2, Qt.SolidLine if ok else Qt.DashLine))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag:
            tgt = self._node_at(e.position().toPoint(), ("out",))
            src = self._drag
            self._drag = None
            self.scene().removeItem(self._temp)
            self._temp = None
            if tgt is not None:
                self.page.connect(src, tgt)
            return
        super().mouseReleaseEvent(e)
        if self.page.moved:
            self.page.save_layout()

    # --- перетаскивание источников из панели
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
            tgt = self._node_at(e.position().toPoint(), ("src",))
            self.page.highlight_drop(tgt)
        else:
            super().dragMoveEvent(e)

    def dragLeaveEvent(self, e):
        self.page.highlight_drop(None)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(MIME):
            return super().dropEvent(e)
        self.page.highlight_drop(None)
        item = json.loads(bytes(e.mimeData().data(MIME)).decode("utf-8"))
        pos = e.position().toPoint()
        tgt = self._node_at(pos, ("src",))
        self.page.add_source(item, self.mapToScene(pos), tgt.obj() if tgt else None)
        e.acceptProposedAction()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            for it in list(self.scene().selectedItems()):
                if isinstance(it, Wire):
                    self.page.disconnect(it, rebuild=False)
            self.page.rebuild()
            return
        super().keyPressEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            f = 1.12 if e.angleDelta().y() > 0 else 1 / 1.12
            t = self.transform().m11() * f
            if 0.4 <= t <= 2.0:
                self.scale(f, f)
            return
        super().wheelEvent(e)

    def contextMenuEvent(self, e):
        it = self.itemAt(e.pos())
        if it is not None:
            super().contextMenuEvent(e)
            return
        sp = self.mapToScene(e.pos())
        m = QMenu()
        m.addAction("+ Выход…", self.page.add_output)
        m.addAction("+ Шина", self.page.add_bus)
        m.addAction("+ Переключатель 1→N", lambda: self.page.add_switch("split"))
        m.addAction("+ Переключатель N→1", lambda: self.page.add_switch("merge"))
        m.addAction("+ Запись: короткие звуки в саундпад", lambda: self.page.add_recorder("clip"))
        m.addAction("+ Запись: повтор последних секунд", lambda: self.page.add_recorder("replay"))
        m.addAction("+ Запись: длинная в файл", lambda: self.page.add_recorder("file"))
        m.addAction("+ Рамка-группа здесь", lambda: self.page.add_frame(sp))
        m.addAction("Найти виртуальные кабели", self.page.find_cables)
        m.addSeparator()
        m.addAction("Расставить блоки заново", self.page.auto_layout)
        m.exec(e.globalPos())


class RoutingPage(QWidget):
    def __init__(self, ctx, on_structure, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.on_structure = on_structure
        self.setObjectName("page")
        self.nodes: dict[str, Node] = {}
        self.padnodes: dict[str, PadNode] = {}
        self.frames: list[Frame] = []
        self.wires: list[Wire] = []
        self.moved = False
        self.phase = 0.0
        self._drop_node = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.b_palette = QPushButton("◂ ИСТОЧНИКИ")
        self.b_palette.setCheckable(True)
        self.b_palette.setChecked(not self.ui.get("palette_hidden", False))
        self.b_palette.setToolTip("Показать/скрыть панель найденных источников")
        self.b_palette.toggled.connect(self._toggle_palette)
        bar.addWidget(self.b_palette)
        for text, slot, tip, primary in (
            ("+ ВЫХОД", self.add_output, "Наушники, колонки, виртуальный кабель…", True),
            ("+ ШИНА", self.add_bus, "Точка сведения без устройства — чтобы собрать звук и разослать дальше", False),
            ("+ 1→N", lambda: self.add_switch("split"), "Переключатель: один вход → выбор одного из нескольких выходов", False),
            ("+ N→1", lambda: self.add_switch("merge"), "Переключатель: выбор одного из нескольких входов → один выход", False),
            ("+ ЗАПИСЬ", self.add_recorder_menu, "Блок записи: короткие звуки голосом прямо в саундпад или длинная запись в файл", False),
            ("+ РАМКА", lambda: self.add_frame(None), "Рамка-группа: объединить блоки и сворачивать их вместе", False),
            ("НАЙТИ КАБЕЛИ", self.find_cables, "Виртуальные кабели: VB-Cable, Voicemeeter, VAC, Voicemod, Wave Link, Sonar…", False),
            ("РАССТАВИТЬ", self.auto_layout, "Разложить блоки по колонкам заново", False),
        ):
            b = QPushButton(text)
            if primary:
                b.setObjectName("primary")
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
        self.b_compact = QPushButton("КОМПАКТНО")
        self.b_compact.setCheckable(True)
        self.b_compact.setToolTip("Свернуть все блоки в одну строку / развернуть обратно")
        self.b_compact.toggled.connect(self._compact_all)
        bar.addWidget(self.b_compact)
        bar.addStretch(1)
        legend = QLabel("<span style='color:%s'>━━</span> канал &nbsp; <span style='color:%s'>━ ┅ ━</span> звук &nbsp; "
                        "<span style='color:%s'>┅┅</span> копия" % (theme.ACCENT, theme.ACCENT2, theme.ACCENT2))
        legend.setObjectName("small")
        bar.addWidget(legend)
        lay.addLayout(bar)
        hint = QLabel("ТЯНИТЕ ОТ ПРАВОГО ■ К ЛЕВОМУ ■ ВЫХОДА — СОЕДИНИТЬ · ▸ N НА КАНАЛЕ — РАСКРЫТЬ ЗВУКИ · "
                      "□ — СВЕРНУТЬ БЛОК · DELETE / ПКМ ПО ПРОВОДУ — ОТКЛЮЧИТЬ · ДВОЙНОЙ КЛИК — НАСТРОИТЬ")
        hint.setObjectName("cap")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.palette = SourcePalette(ctx, self.add_source)
        self.palette.setVisible(self.b_palette.isChecked())
        row.addWidget(self.palette)
        self.scene = QGraphicsScene(self)
        self.view = RoutingView(self)
        self.view.setScene(self.scene)
        row.addWidget(self.view, 1)
        lay.addLayout(row, 1)
        self._rebuild_now()

    # ------------------------------------------------------------ состояние схемы
    @property
    def ui(self) -> dict:
        return self.ctx.project.ui

    @property
    def expanded(self) -> set:
        return set(self.ui.get("expanded", []))

    @property
    def compact(self) -> set:
        return set(self.ui.get("compact", []))

    def _set_ui(self, key, value):
        self.ui[key] = value
        self.ctx.save_later()

    def pad_count(self, cid):
        return sum(1 for p in self.ctx.project.pads if p.channel == cid)

    def _toggle_palette(self, on):
        self.palette.setVisible(on)
        self.b_palette.setText("◂ ИСТОЧНИКИ" if on else "▸ ИСТОЧНИКИ")
        self._set_ui("palette_hidden", not on)

    # ------------------------------------------------------------ построение
    def _node_height(self, kind, rid):
        o = self.ctx.project.output(rid) if kind == "out" else None
        if o is not None and o.switch:
            return SW_HEAD + (len(self.ctx.project.switch_ports(o)) + 1) * SW_ROW + 22
        comp = rid in self.compact
        h = COMPACT_H if comp else (SRC_H if kind == "src" else OUT_H)
        if kind == "src" and rid in self.expanded:
            n = self.pad_count(rid)
            if n:
                h += n * (PAD_H + 3) + 4
        return h

    def _default_positions(self):
        pr = self.ctx.project
        depth = {o.id: 1 for o in pr.outputs}
        for o in pr.output_order():
            for t in o.sends:
                if t in depth:
                    depth[t] = max(depth[t], depth[o.id] + 1)
        pos = {}
        y = 20
        for c in pr.channels:
            pos[c.id] = [20, y]
            y += self._node_height("src", c.id) + 30
        cols: dict[int, int] = {}
        for o in pr.output_order():
            d = depth[o.id]
            yy = cols.get(d, 20)
            pos[o.id] = [20 + d * COL_W, yy]
            cols[d] = yy + self._node_height("out", o.id) + 24
        return pos

    def rebuild(self):
        """Перестроить схему. Отложено до конца текущего события: кнопки на блоках вызывают перестройку,
        а удалять блок прямо внутри его же обработчика мыши нельзя."""
        if getattr(self, "_rebuild_pending", False):
            return
        self._rebuild_pending = True
        QTimer.singleShot(0, self._rebuild_now)

    def _rebuild_now(self):
        self._rebuild_pending = False
        self.scene.clear()
        self.nodes.clear()
        self.padnodes.clear()
        self.frames = []
        self.wires = []
        self._drop_node = None
        pr = self.ctx.project
        for d in self.ui.get("frames", []):
            f = Frame(self, d)
            self.scene.addItem(f)
            self.frames.append(f)
        defaults = None
        for kind, items in (("src", pr.channels), ("out", pr.outputs)):
            for x in items:
                n = Node(self, kind, x.id)
                xy = pr.layout.get(x.id)
                if xy is None:
                    defaults = defaults or self._default_positions()
                    xy = defaults[x.id]
                n.setPos(*xy)
                self.scene.addItem(n)
                self.nodes[x.id] = n
                if kind == "src" and n.expanded:
                    for i, pad in enumerate(p for p in pr.pads if p.channel == x.id):
                        pn = PadNode(self, n, pad, i)
                        n.pads.append(pn)
                        self.padnodes[pad.id] = pn
        # свёрнутые рамки прячут свои блоки
        for f in self.frames:
            if f.collapsed:
                f.d["members"] = [m for m in f.d.get("members", []) if m in self.nodes]
                for m in f.d["members"]:
                    self.nodes[m].setVisible(False)
        # отдельные «полосы» проводов для каждого источника
        lane = 0
        for c in sorted(pr.channels, key=lambda c: self.nodes[c.id].pos().y()):
            self.nodes[c.id].lane = lane
            lane += 1
            for pn in self.nodes[c.id].pads:
                pn.lane = lane
                lane += 1
        for i, o in enumerate(sorted(pr.outputs, key=lambda o: (self.nodes[o.id].pos().x(), self.nodes[o.id].pos().y()))):
            self.nodes[o.id].lane = i
        # провода
        for c in pr.channels:
            for oid in c.outputs:
                if oid in self.nodes:
                    self._add_wire(self.nodes[c.id], self.nodes[oid], "ch")
        for pad in pr.pads:
            if pad.route_own and pad.channel in self.nodes:
                src = self.padnodes.get(pad.id) or self.nodes[pad.channel]
                for oid in pad.outputs:
                    if oid in self.nodes:
                        self._add_wire(src, self.nodes[oid], "pad", pad.id)
        for o in pr.outputs:
            for t in o.sends:
                if t in self.nodes:
                    self._add_wire(self.nodes[o.id], self.nodes[t], "send")
        r = self.scene.itemsBoundingRect().adjusted(-40, -40, 400, 300)
        self.scene.setSceneRect(r.united(QRectF(0, 0, 1200, 700)))
        self.moved = False
        self.palette.refresh_used()

    def _add_wire(self, a, b, kind, pad_id=""):
        w = Wire(self, a, b, kind, pad_id)
        self.scene.addItem(w)
        self.wires.append(w)

    def update_wires(self):
        for w in self.wires:
            w.update_path()

    def anchor(self, node, kind, peer=None):
        """Точка подключения провода: порт блока (у переключателя — порт конкретного положения)
        или край свёрнутой рамки, в которой блок спрятан."""
        top = node.parent_node if isinstance(node, PadNode) else node
        for f in self.frames:
            if f.collapsed and top.ref in f.d.get("members", []):
                return f.port_pos(kind), f
        port = node.out_port if kind == "out" else node.in_port
        if getattr(node, "sw", ""):
            if kind == "out" and node.sw == "split":
                port = node.out_ports.get(peer, port)
            elif kind == "in" and node.sw == "merge":
                port = node.in_ports.get(peer, port)
        return port.scenePos(), None

    def save_layout(self):
        self.moved = False
        for rid, n in self.nodes.items():
            self.ctx.project.layout[rid] = [n.pos().x(), n.pos().y()]
        self._set_ui("frames", [f.d for f in self.frames])
        r = self.scene.itemsBoundingRect().adjusted(-40, -40, 400, 300)
        self.scene.setSceneRect(r.united(self.scene.sceneRect()))

    def auto_layout(self):
        pos = self._default_positions()
        for rid, n in self.nodes.items():
            n.setPos(*pos[rid])
        self.save_layout()
        self.update_wires()

    # ------------------------------------------------------------ свернуть / раскрыть
    def _shift_below(self, node: Node, delta: float):
        """Сдвигает блоки той же колонки ниже node — чтобы раскрытый канал никого не накрыл."""
        x, y = node.pos().x(), node.pos().y()
        for n in self.nodes.values():
            if n is not node and abs(n.pos().x() - x) < 80 and n.pos().y() > y:
                self.ctx.project.layout[n.ref] = [n.pos().x(), max(0, n.pos().y() + delta)]

    def _store_positions(self):
        for rid, n in self.nodes.items():
            self.ctx.project.layout[rid] = [n.pos().x(), n.pos().y()]

    def toggle_expand(self, node: Node):
        self._store_positions()
        exp = self.expanded
        extra = self.pad_count(node.ref) * (PAD_H + 3) + 4
        if node.ref in exp:
            exp.discard(node.ref)
            self._shift_below(node, -extra)
        else:
            exp.add(node.ref)
            self._shift_below(node, extra)
        self._set_ui("expanded", sorted(exp))
        self.rebuild()

    def toggle_compact(self, node: Node):
        self._store_positions()
        comp = self.compact
        full = SRC_H if node.kind == "src" else OUT_H
        if node.ref in comp:
            comp.discard(node.ref)
            self._shift_below(node, full - COMPACT_H)
        else:
            comp.add(node.ref)
            self._shift_below(node, COMPACT_H - full)
        self._set_ui("compact", sorted(comp))
        self.rebuild()

    def _compact_all(self, on):
        self._set_ui("compact", sorted(self.nodes) if on else [])
        for rid in self.nodes:
            self.ctx.project.layout.pop(rid, None)        # высоты поменялись — раскладываем заново
        self.rebuild()
        self.save_layout()

    # ------------------------------------------------------------ рамки
    def add_frame(self, at=None):
        if at is None:
            at = self.view.mapToScene(self.view.viewport().rect().center()) - QPointF(200, 140)
        n = len(self.frames) + 1
        d = {"id": new_id("fr"), "name": f"Группа {n}", "x": round(at.x() / 10) * 10, "y": round(at.y() / 10) * 10,
             "w": 400, "h": 280, "collapsed": False, "members": []}
        frames = self.ui.get("frames", []) + [d]
        self._set_ui("frames", frames)
        self.rebuild()

    def frame_members(self, f: Frame) -> list:
        r = f.mapRectToScene(f.rect())
        return [rid for rid, n in self.nodes.items() if n.isVisible() and r.contains(n.sceneBoundingRect().center())]

    def toggle_frame(self, f: Frame):
        if f.collapsed:
            f.d["collapsed"] = False
        else:
            f.d["members"] = self.frame_members(f)
            if not f.d["members"]:
                self.window()._set_msg("В РАМКЕ НЕТ БЛОКОВ — ПЕРЕТАЩИТЕ ИХ ВНУТРЬ")
                return
            f.d["collapsed"] = True
        self._store_positions()
        self._set_ui("frames", [x.d for x in self.frames])
        self.rebuild()

    def rename_frame(self, f: Frame):
        name, ok = QInputDialog.getText(self, "Рамка", "Название:", text=f.d.get("name", ""))
        if ok and name.strip():
            f.d["name"] = name.strip()
            self._set_ui("frames", [x.d for x in self.frames])
            f.update()

    def delete_frame(self, f: Frame):
        self._set_ui("frames", [x.d for x in self.frames if x is not f])
        self.rebuild()

    # ------------------------------------------------------------ источники из панели
    def highlight_drop(self, node):
        if node is self._drop_node:
            return
        if self._drop_node is not None:
            self._drop_node.setOpacity(1.0)
        self._drop_node = node
        if node is not None:
            node.setOpacity(0.6)

    def add_source(self, item: dict, scene_pos=None, channel=None):
        """Создаёт живой источник: на пустом месте — новый канал, на блоке канала — звук в этом канале."""
        pr = self.ctx.project
        src = LiveSource(type=item["type"], device=item.get("device", ""), hostapi=item.get("hostapi", ""),
                         process=item.get("process", ""))
        label = item.get("label", "Источник")
        if channel is None:
            color = theme.PAD_COLORS[len(pr.channels) % len(theme.PAD_COLORS)]
            channel = Channel(new_id("ch"), label[:32], volume=0.8, outputs=[], color=color)
            pr.channels.append(channel)
            if scene_pos is None:
                ys = [n.pos().y() + n.total_h() for n in self.nodes.values() if n.kind == "src"]
                scene_pos = QPointF(20, (max(ys) + 30) if ys else 20)
            pr.layout[channel.id] = [round(scene_pos.x() / 10) * 10, round(scene_pos.y() / 10) * 10]
        pad = Pad(new_id("pad"), label[:40], kind="live", channel=channel.id, mode="toggle", source=src,
                  autostart=True, fade_out_ms=200, color=channel.color)
        pr.pads.append(pad)
        self.ctx.apply()
        self.ctx.engine.pad_event(pad.id, "press")
        self.rebuild()
        self.on_structure()
        if not channel.outputs:
            self.window()._set_msg(f"ДОБАВЛЕНО: {label.upper()} — ПРОТЯНИТЕ ПРОВОД К НУЖНЫМ ВЫХОДАМ")
        else:
            self.window()._set_msg(f"ДОБАВЛЕНО В КАНАЛ «{channel.name.upper()}»: {label.upper()}")

    # ------------------------------------------------------------ связи
    def can_connect(self, src, dst) -> bool:
        if not isinstance(dst, Node) or dst.kind != "out" or src is dst:
            return False
        pr = self.ctx.project
        if isinstance(src, PadNode):
            pad = src.obj()
            return not (pad.route_own and dst.ref in pad.outputs)
        if src.kind == "src":
            return dst.ref not in pr.channel(src.ref).outputs
        so = pr.output(src.ref)
        return dst.ref not in so.sends and not pr.send_makes_loop(src.ref, dst.ref)

    def connect(self, src, dst):
        pr = self.ctx.project
        if not self.can_connect(src, dst):
            if isinstance(src, Node) and src.kind == "out" and pr.send_makes_loop(src.ref, dst.ref):
                self.window()._error("Так нельзя: получится петля — звук пойдёт по кругу")
            return
        if isinstance(src, PadNode):
            pad = src.obj()
            if not pad.route_own:
                # своя разводка: провода канала для этого звука больше не действуют, поэтому начинаем
                # с того, куда звук шёл до сих пор, и добавляем новый выход (раньше звук оставался
                # только в новом выходе — например, микрофон пропадал из Дискорда при подключении наушников)
                ch = pr.channel(pad.channel)
                was = [o for o in (ch.outputs if ch else []) if o not in pad.exclude_outputs]
                pad.route_own = True
                pad.outputs = list(dict.fromkeys(was + [dst.ref]))
                names = ", ".join(pr.output(o).name for o in pad.outputs if pr.output(o))
                self.window()._set_msg(f"«{pad.name.upper()}» — СВОЯ РАЗВОДКА: {names.upper()}")
            else:
                pad.outputs = pad.outputs + [dst.ref]
        elif src.kind == "src":
            ch = pr.channel(src.ref)
            ch.outputs = ch.outputs + [dst.ref]
        else:
            so = pr.output(src.ref)
            so.sends = so.sends + [dst.ref]
        self.ctx.apply()
        self.rebuild()
        self.on_structure()

    def disconnect(self, w: Wire, rebuild=True):
        pr = self.ctx.project
        if w.kind == "ch":
            ch = pr.channel(w.src.ref)
            if ch:
                ch.outputs = [o for o in ch.outputs if o != w.dst.ref]
        elif w.kind == "pad":
            pad = pr.pad(w.pad_id)
            if pad:
                pad.outputs = [o for o in pad.outputs if o != w.dst.ref]
        else:
            so = pr.output(w.src.ref)
            if so:
                so.sends = [o for o in so.sends if o != w.dst.ref]
        self.ctx.apply()
        if rebuild:
            self.rebuild()
        self.on_structure()

    def set_pad_own(self, pid, own: bool):
        pad = self.ctx.project.pad(pid)
        if pad:
            pad.route_own = own
            self.ctx.apply()
            self.rebuild()
            self.on_structure()

    # ------------------------------------------------------------ блоки
    def add_output(self):
        o = Output(new_id("out"), f"Выход {len(self.ctx.project.outputs) + 1}")
        d = devices.default_device("output")
        if d:
            o.device, o.hostapi = d["name"], d["hostapi"]
        if OutputDialog(self.ctx, o, self, is_new=True).exec():
            self._after_structure()

    def add_switch(self, mode):
        n = sum(1 for o in self.ctx.project.outputs if o.switch) + 1
        o = Output(new_id("out"), f"Переключатель {n}", bus=True, switch=mode, active=0)
        at = self.view.mapToScene(self.view.viewport().rect().center())
        self.ctx.project.outputs.append(o)
        self.ctx.project.layout[o.id] = [round(at.x() / 10) * 10 - 100, round(at.y() / 10) * 10 - 40]
        self.ctx.apply()
        self._after_structure()
        self.window()._set_msg("ПЕРЕКЛЮЧАТЕЛЬ ДОБАВЛЕН — " + ("ПОДКЛЮЧИТЕ ВХОД СЛЕВА И НЕСКОЛЬКО ВЫХОДОВ СПРАВА"
                               if mode == "split" else "ПОДКЛЮЧИТЕ НЕСКОЛЬКО ВХОДОВ СЛЕВА И ВЫХОД СПРАВА"))

    def switch_hotkey(self, sw, op, index=0):
        from .binding_dialog import BindingDialog
        BindingDialog(self.ctx, None, self, preset={"type": "switch", "switch": sw.id, "op": op, "index": index}).exec()
        self.rebuild()
        self.on_structure()

    def add_recorder_menu(self):
        m = QMenu(self)
        m.addAction("● Короткие звуки → в саундпад (зажал — сказал — отпустил)", lambda: self.add_recorder("clip"))
        m.addAction("⟲ Повтор: последние секунды → в саундпад (по клавише)", lambda: self.add_recorder("replay"))
        m.addAction("● Длинная запись → в файл", lambda: self.add_recorder("file"))
        m.exec(self.cursor().pos())

    def add_recorder(self, mode):
        from app.audio.recorder import rec_defaults
        from .recorder_dialog import RecorderDialog, _guess_sfx_channel
        pr = self.ctx.project
        n = sum(1 for o in pr.outputs if o.recorder == mode) + 1
        cfg = rec_defaults(mode)
        if mode in ("clip", "replay"):
            cfg["channel"] = _guess_sfx_channel(pr)
            cfg["slots"] = "ctrl+alt+digits" if mode == "clip" else ""
        title = {"clip": "Голос в звуки", "replay": "Повтор", "file": "Запись в файл"}[mode]
        o = Output(new_id("out"), title + (f" {n}" if n > 1 else ""), bus=True, recorder=mode, rec=cfg)
        if not RecorderDialog(self.ctx, o, self, is_new=True).exec():
            return
        at = self.view.mapToScene(self.view.viewport().rect().center())
        pr.layout[o.id] = [round(at.x() / 10) * 10 - 120, round(at.y() / 10) * 10 - 40]
        msg = "БЛОК ЗАПИСИ ДОБАВЛЕН — ПРОТЯНИТЕ К НЕМУ ПРОВОД ОТ ТОГО, ЧТО НУЖНО ЗАПИСАТЬ"
        if o.recorder == "clip":
            mic = next((c for c in pr.channels if "микро" in c.name.lower()), None)
            if mic is not None:                    # сразу подключаем микрофон — запишется с его эффектами
                mic.outputs = mic.outputs + [o.id]
                for pad in pr.pads:
                    if pad.channel == mic.id and pad.route_own:
                        pad.outputs = pad.outputs + [o.id]
                msg = f"БЛОК ЗАПИСИ ДОБАВЛЕН И ПОДКЛЮЧЁН К «{mic.name.upper()}»"
            from app.model import Binding, Trigger
            used = {b.trigger.key() for b in pr.bindings}
            for combo in ("ctrl+num 6", "ctrl+num 1", "ctrl+num 2", "ctrl+num 3"):
                if "key:" + combo not in used:      # клавиша «зажал — записал»
                    pr.bindings.append(Binding(new_id("bind"), Trigger("key", combo),
                                               {"type": "record", "rec": o.id, "op": "hold"}))
                    from app.model import pretty_combo
                    msg += f" · ЗАЖМИТЕ {pretty_combo(combo).upper()} И ГОВОРИТЕ"
                    break
        if o.recorder == "replay":
            from app.model import Binding, Trigger, pretty_combo
            # что слышат в Дискорде (микрофон + звуки), а если такого выхода нет — микрофон
            ds = next((x for x in pr.outputs if not x.bus and devices.is_virtual_cable(x.device) and x.enabled), None)
            mic = next((c for c in pr.channels if "микро" in c.name.lower()), None)
            if ds is not None:
                ds.sends = ds.sends + [o.id]
                msg = f"ПОВТОР ДОБАВЛЕН: ПОМНИТ ТО, ЧТО УХОДИТ В «{ds.name.upper()}»"
            elif mic is not None:
                mic.outputs = mic.outputs + [o.id]
                msg = f"ПОВТОР ДОБАВЛЕН И ПОДКЛЮЧЁН К «{mic.name.upper()}»"
            used = {b.trigger.key() for b in pr.bindings}
            for combo in ("ctrl+num 3", "ctrl+num 2", "ctrl+num 1", "ctrl+shift+s"):
                if "key:" + combo not in used:
                    pr.bindings.append(Binding(new_id("bind"), Trigger("key", combo),
                                               {"type": "record", "rec": o.id, "op": "save"}))
                    msg += f" · {pretty_combo(combo).upper()} — СОХРАНИТЬ МОМЕНТ"
                    break
        self.ctx.apply()
        self._after_structure()
        self.window()._set_msg(msg)

    def mute_hotkey(self, o):
        from .binding_dialog import BindingDialog
        BindingDialog(self.ctx, None, self, preset={"type": "mute_out", "output": o.id, "mode": "toggle"}).exec()
        self.rebuild()
        self.on_structure()

    def rec_hotkey(self, o, op):
        from .binding_dialog import BindingDialog
        BindingDialog(self.ctx, None, self, preset={"type": "record", "rec": o.id, "op": op}).exec()
        self.rebuild()
        self.on_structure()

    def add_bus(self):
        n = sum(1 for o in self.ctx.project.outputs if o.bus) + 1
        self.ctx.project.outputs.append(Output(new_id("out"), f"Шина {n}", bus=True))
        self.ctx.apply()
        self._after_structure()

    def find_cables(self):
        from .cables_dialog import CablesDialog
        d = CablesDialog(self.ctx, self)
        if d.exec() and d.added:
            self._after_structure()
            self.window()._set_msg(f"ДОБАВЛЕНО: {len(d.added)} — " + ", ".join(o.name for o in d.added))

    def _after_structure(self):
        self.rebuild()
        self.on_structure()

    def edit_node(self, node: Node):
        if getattr(node, "sw", ""):
            o = node.obj()
            name, ok = QInputDialog.getText(self, "Переключатель", "Название:", text=o.name)
            if ok and name.strip():
                o.name = name.strip()
                self.ctx.save_later()
                node.update()
            return
        if getattr(node, "rec", ""):
            from .recorder_dialog import RecorderDialog
            if RecorderDialog(self.ctx, node.obj(), self).exec():
                self._after_structure()
            return
        if node.kind == "out":
            if OutputDialog(self.ctx, node.obj(), self).exec():
                self._after_structure()
        else:
            from .effects_dialog import EffectsDialog
            EffectsDialog(self.ctx, node.obj(), self).exec()
            node.update()

    def edit_fx(self, node):
        from .effects_dialog import EffectsDialog
        EffectsDialog(self.ctx, node.obj(), self).exec()
        self.ctx.engine.sync_effects(node.ref)
        node.update()
        self.on_structure()

    def edit_pad(self, pid):
        from .pad_editor import PadEditor
        pad = self.ctx.project.pad(pid)
        if pad and PadEditor(self.ctx, pad, parent=self).exec():
            self._after_structure()

    def node_menu(self, node: Node, pos):
        m = QMenu()
        o = node.obj()
        if getattr(node, "sw", ""):
            pr = self.ctx.project
            ports = pr.switch_ports(o)
            hk = m.addMenu("Горячие клавиши")
            hk.addAction("Следующее положение…", lambda: self.switch_hotkey(o, "next"))
            hk.addAction("Предыдущее положение…", lambda: self.switch_hotkey(o, "prev"))
            hk.addAction("Выключить (никуда)…", lambda: self.switch_hotkey(o, "off"))
            if ports:
                hk.addSeparator()
                for i, pid in enumerate(ports):
                    sub = hk.addMenu(f"Положение {i + 1}: {pr.node_name(pid)}")
                    sub.addAction("Выбрать по нажатию…", lambda i=i: self.switch_hotkey(o, "set", i))
                    sub.addAction("Вкл/выкл по нажатию…", lambda i=i: self.switch_hotkey(o, "toggle", i))
                    sub.addAction("Только пока держишь…", lambda i=i: self.switch_hotkey(o, "hold", i))
            pos_m = m.addMenu("Положение")
            for i, pid in enumerate(ports):
                a = pos_m.addAction(f"{i + 1}: {pr.node_name(pid)}", lambda i=i: (self.ctx.engine.switch_set(o.id, i), node.update()))
                a.setCheckable(True)
                a.setChecked(o.active == i)
            pos_m.addAction("Выключить", lambda: (self.ctx.engine.switch_set(o.id, -1), node.update()))
            fm = m.addMenu("Плавность переключения")
            for ms in (0, 60, 120, 300, 800, 2000):
                a = fm.addAction("мгновенно" if ms == 0 else f"{ms} мс",
                                 lambda ms=ms: (setattr(o, "fade_ms", ms), self.ctx.save_later()))
                a.setCheckable(True)
                a.setChecked(o.fade_ms == ms)
            m.addAction("Эффекты на выходе переключателя…", lambda: self.edit_fx(node))
            m.addAction("Переименовать…", lambda: self.edit_node(node))
            m.addSeparator()
            m.addAction("Отключить все провода", lambda: self._drop_wires(node))
            m.addAction("Удалить переключатель", lambda: self._delete_output(node))
            m.exec(pos)
            return
        if getattr(node, "rec", ""):
            from .recorder_dialog import clip_folder, open_folder
            st = self.ctx.engine.rec_states().get(o.id) or {}
            if node.rec == "file":
                m.addAction("■ Закончить запись" if st.get("on") else "● Начать запись",
                            lambda: self.ctx.engine.rec_command(o.id, "toggle"))
            if node.rec == "replay":
                m.addAction("⟲ Сохранить последние секунды", lambda: self.ctx.engine.rec_command(o.id, "save"))
                m.addAction("Горячая клавиша «сохранить момент»…", lambda: self.rec_hotkey(o, "save"))
            else:
                hk = m.addMenu("Горячие клавиши")
                hk.addAction("Записывать, пока держишь…", lambda: self.rec_hotkey(o, "hold"))
                hk.addAction("Начать / закончить по нажатию…", lambda: self.rec_hotkey(o, "toggle"))
            m.addAction("Настроить…", lambda: self.edit_node(node))
            m.addAction("Эффекты перед записью…", lambda: self.edit_fx(node))
            m.addAction("Открыть папку с записями",
                        lambda: open_folder(((o.rec or {}).get("folder") or "") if node.rec == "file" else clip_folder()))
            a = m.addAction("Включён")
            a.setCheckable(True)
            a.setChecked(o.enabled)
            a.toggled.connect(lambda on: (setattr(o, "enabled", on), self.ctx.apply(), node.update()))
            m.addAction("Развернуть блок" if node.compact else "Свернуть в строку", lambda: self.toggle_compact(node))
            m.addSeparator()
            m.addAction("Отключить все провода", lambda: self._drop_wires(node))
            m.addAction("Удалить блок записи", lambda: self._delete_output(node))
            m.exec(pos)
            return
        if node.kind == "out":
            m.addAction("Эффекты выхода…" if not o.bus else "Эффекты шины…", lambda: self.edit_fx(node))
            a = m.addAction("Заглушить")
            a.setCheckable(True)
            a.setChecked(o.muted)
            a.toggled.connect(lambda on: (self.ctx.engine.set_out_muted(o.id, on), node.update()))
            m.addAction("Горячая клавиша «заглушить»…", lambda: self.mute_hotkey(o))
            m.addAction("Настроить…", lambda: self.edit_node(node))
            a = m.addAction("Включён")
            a.setCheckable(True)
            a.setChecked(o.enabled)
            a.toggled.connect(lambda on: (setattr(o, "enabled", on), self.ctx.apply(), node.update()))
        else:
            m.addAction("Эффекты канала…", lambda: self.edit_node(node))
            if self.pad_count(node.ref):
                m.addAction("Свернуть звуки" if node.expanded else "Раскрыть звуки", lambda: self.toggle_expand(node))
            a = m.addAction("Заглушить")
            a.setCheckable(True)
            a.setChecked(o.muted)
            a.toggled.connect(lambda on: (self.ctx.engine.set_muted(o.id, on), node.update()))
        m.addAction("Развернуть блок" if node.compact else "Свернуть в строку", lambda: self.toggle_compact(node))
        m.addSeparator()
        m.addAction("Отключить все провода", lambda: self._drop_wires(node))
        if node.kind == "out":
            m.addAction("Удалить выход", lambda: self._delete_output(node))
        else:
            m.addAction("Удалить канал", lambda: self._delete_channel(node))
        m.exec(pos)

    def _drop_wires(self, node):
        for w in list(node.wires):
            if w.src is node or node.kind == "out":
                self.disconnect(w, rebuild=False)
        self.rebuild()

    def _delete_output(self, node):
        o = node.obj()
        if QMessageBox.question(self, "Удалить выход", f"Удалить выход «{o.name}»?") != QMessageBox.Yes:
            return
        self.ctx.project.outputs = [x for x in self.ctx.project.outputs if x is not o]
        self.ctx.project.layout.pop(o.id, None)
        if o.recorder:
            self.ctx.project.bindings = [b for b in self.ctx.project.bindings
                                         if not (b.action.get("type") == "record" and b.action.get("rec") == o.id)]
        self.ctx.apply()
        self._after_structure()

    def _delete_channel(self, node):
        pr = self.ctx.project
        ch = node.obj()
        if len(pr.channels) <= 1:
            QMessageBox.information(self, "Канал", "Нужен хотя бы один канал.")
            return
        pads = [p for p in pr.pads if p.channel == ch.id]
        msg = f"Удалить канал «{ch.name}»?"
        if pads:
            msg += f"\nЕго звуки ({len(pads)}) будут удалены вместе с ним."
        if QMessageBox.question(self, "Удалить канал", msg) != QMessageBox.Yes:
            return
        for p in pads:
            self.ctx.engine.stop_pad(p.id, fade_ms=30)
        ids = {p.id for p in pads}
        pr.pads = [p for p in pr.pads if p.id not in ids]
        pr.bindings = [b for b in pr.bindings if b.action.get("pad") not in ids and b.action.get("channel") != ch.id]
        pr.channels = [c for c in pr.channels if c is not ch]
        pr.layout.pop(ch.id, None)
        self.ctx.apply()
        self._after_structure()

    # ------------------------------------------------------------ живые данные
    def tick(self, ch_peaks, out_peaks, status, pad_states=None):
        self.phase = (self.phase + 0.6) % 900
        pad_states = pad_states or {}
        recs = self.ctx.engine.rec_states() if any(n.rec for n in self.nodes.values()) else {}
        for rid, n in self.nodes.items():
            if not n.isVisible():
                continue
            if n.rec:
                st = recs.get(rid) or {}
                if st != n.rec_state:
                    n.rec_state = st
                    n.update()
            lvl = ch_peaks.get(rid, 0.0) if n.kind == "src" else out_peaks.get(rid, 0.0)
            st = status.get(rid, "") if n.kind == "out" else ""
            if abs(lvl - n.level) > 0.002 or st != n.status or lvl > 0.002:
                n.level = lvl
                n.status = st or ""
                n.update()
        for pid, pn in self.padnodes.items():
            pl = pid in pad_states
            if pl != pn.playing:
                pn.playing = pl
                pn.update()
        for w in self.wires:
            if not w.isVisible():
                continue
            if w.kind == "ch":
                lvl = ch_peaks.get(w.src.ref, 0.0)
            elif w.kind == "pad":
                pad = self.ctx.project.pad(w.pad_id)
                lvl = ch_peaks.get(pad.channel, 0.0) if (pad and w.pad_id in pad_states) else 0.0
            else:
                lvl = out_peaks.get(w.src.ref, 0.0)
            act = lvl > 0.003
            if act or act != w.active:
                w.active = act
                w.update()

    def refresh(self):
        for n in self.nodes.values():
            n.update()
