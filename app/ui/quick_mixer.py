"""Быстрый микшер: маленькое окно поверх всего, открывается горячей клавишей. Настраивается (⚙)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout,
                               QWidget)

from app.audio.effects import effect_title
from . import theme
from .widgets import Meter, caption

DEFAULTS = {
    "scale": 1.0, "opacity": 100, "position": "center", "pos": None,
    "channels": None, "outputs": [], "switches": None, "pads": None,      # None = показывать все
    "show_pads": True, "show_fx": True, "show_mute": True, "show_keys": True, "show_stop": True,
    "show_switches": True, "show_player": True, "show_rec": True, "pad_cols": 2,
    "strip_w": 168, "fader_h": 96, "pad_w": 150, "pad_h": 40,          # размеры блоков, пиксели
}
SCALES = [(0.65, "Микро (65%)"), (0.8, "Мини (80%)"), (1.0, "Обычный"), (1.2, "Крупный (120%)")]
SIZE_PRESETS = {      # быстрые наборы размеров: ширина полоски, высота фейдера, кнопка звука
    "Микро": (96, 60, 96, 26), "Компакт": (124, 76, 120, 32), "Обычный": (168, 96, 150, 40),
}
POSITIONS = {"center": "По центру экрана", "cursor": "Возле курсора", "remember": "Где оставили в прошлый раз"}


def quick_cfg(project) -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(project.ui.get("quick") or {})
    try:
        cfg["scale"] = min(1.2, max(0.65, float(cfg["scale"])))   # «огромный» больше не предлагаем
    except (TypeError, ValueError):
        cfg["scale"] = 1.0
    return cfg


def keys_for(project, pred) -> str:
    ks = [b.trigger.label().upper() for b in project.bindings if pred(b.action)]
    return " / ".join(ks)


class _KeyLabel(QLabel):
    def __init__(self, text="", s=1.0):
        super().__init__(text)
        self.setStyleSheet(f"color: {theme.ACCENT2}; font-size: {7.5 * s:.1f}pt; font-weight: 700;")
        self.setWordWrap(True)


class MiniStrip(QWidget):
    """Полоска канала или выхода/шины: громкость, индикатор, заглушить/выключить, эффекты."""

    def __init__(self, ctx, owner, cfg, is_output=False):
        super().__init__()
        self.ctx = ctx
        self.ch = owner
        self.is_output = is_output
        s = cfg["scale"]
        sw = int(cfg["strip_w"])
        self.setFixedWidth(sw)
        self.setObjectName("mini")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#mini {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; }}")
        pr = ctx.project
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(4)
        tag = ("ШИНА · " if getattr(owner, "bus", False) else "ВЫХ · ") if is_output else ""
        head = QLabel(f"  {tag}{owner.name.upper()}")
        head.setToolTip(owner.name)
        border = theme.ACCENT2 if is_output else owner.color
        head.setStyleSheet(f"background: {theme.ACCENT2 if is_output else theme.ACCENT}; color: {theme.INK};"
                           f" font-weight: 700; padding: 2px; border-left: 8px solid {border};")
        lay.addWidget(head)
        body = QVBoxLayout()
        body.setContentsMargins(6, 2, 6, 0)
        body.setSpacing(3)
        lay.addLayout(body)

        row = QHBoxLayout()
        self.slider = QSlider(Qt.Vertical)
        self.slider.setRange(0, 150 if is_output else 100)
        fh = int(cfg["fader_h"])
        self.slider.setFixedHeight(fh)
        self.slider.valueChanged.connect(self._vol)
        self.meter = Meter(vertical=True)
        self.meter.setFixedHeight(fh)
        col = QVBoxLayout()
        self.vol = QLabel()
        vfs = 16 * s * (1.0 if sw >= 140 else max(0.6, sw / 140))      # узкая полоска — цифры мельче
        self.vol.setStyleSheet(f"font-size: {vfs:.1f}pt; font-weight: 700; color: {theme.ACCENT2};")
        col.addWidget(self.vol)
        if cfg["show_keys"] and not is_output:
            for b in pr.bindings:
                a = b.action
                if a.get("channel") == owner.id and a.get("type") == "volume_step":
                    st = float(a.get("step", 0.05))
                    col.addWidget(_KeyLabel(f"{'+' if st > 0 else '−'} {b.trigger.label().upper()}", s))
                elif a.get("channel") == owner.id and a.get("type") == "volume_set":
                    col.addWidget(_KeyLabel(f"◆ {b.trigger.label().upper()}", s))
        col.addStretch(1)
        row.addWidget(self.slider)
        row.addWidget(self.meter)
        row.addSpacing(4)
        row.addLayout(col, 1)
        body.addLayout(row)

        self.mute = None
        if cfg["show_mute"]:
            self.mute = QPushButton("ЗАГЛУШИТЬ")
            self.mute.setCheckable(True)
            self.mute.setStyleSheet(f"QPushButton:checked {{ background: {theme.DANGER}; border-color: {theme.DANGER}; color: {theme.INK}; }}")
            self.mute.toggled.connect(self._mute)
            body.addWidget(self.mute)
            if cfg["show_keys"]:
                mk = keys_for(pr, lambda a: (a.get("type") == "mute" and a.get("channel") == owner.id)
                              or (a.get("type") == "mute_out" and a.get("output") == owner.id))
                if mk:
                    body.addWidget(_KeyLabel("  " + mk, s))

        self.fx = {}
        if cfg["show_fx"]:
            for slot in owner.effects:
                b = QPushButton(effect_title(slot.type).upper())
                b.setCheckable(True)
                b.setStyleSheet(f"text-align: left; padding: 3px 6px; font-size: {8.5 * s:.1f}pt;")
                b.toggled.connect(lambda on, slot=slot: ctx.engine.set_effect(owner.id, slot.id, on))
                k = keys_for(pr, lambda a, slot=slot: a.get("type") == "effect" and a.get("effect") == slot.id)
                if k:
                    b.setToolTip("Клавиша: " + k)
                    if cfg["show_keys"]:
                        b.setText(f"{effect_title(slot.type).upper()}\n{k}")
                self.fx[slot.id] = b
                body.addWidget(b)
        lay.addStretch(1)
        self.sync()

    def _vol(self, v):
        if self.is_output:
            self.ch.volume = v / 100
            self.ctx.save_later()
        else:
            self.ctx.engine.set_volume(self.ch.id, v / 100)
        self.vol.setText(f"{v}%")

    def _mute(self, on):
        if self.is_output:
            self.ctx.engine.set_out_muted(self.ch.id, on)
        else:
            self.ctx.engine.set_muted(self.ch.id, on)

    def sync(self):
        ch = self.ch
        v = int(round(ch.volume * 100))
        if self.slider.value() != v:
            self.slider.blockSignals(True)
            self.slider.setValue(v)
            self.slider.blockSignals(False)
        self.vol.setText(f"{v}%")
        if self.mute is not None:
            m = ch.muted
            if self.mute.isChecked() != m:
                self.mute.blockSignals(True)
                self.mute.setChecked(m)
                self.mute.blockSignals(False)
        for slot in ch.effects:
            b = self.fx.get(slot.id)
            if b and b.isChecked() != slot.enabled:
                b.blockSignals(True)
                b.setChecked(slot.enabled)
                b.blockSignals(False)


class PlayerStrip(QWidget):
    """Плеер в мини-микшере: трек, позиция (клик — перемотка), кнопки, громкость."""

    def __init__(self, ctx, cfg):
        super().__init__()
        from .player_page import SeekBar
        self.ctx = ctx
        s = cfg["scale"]
        w = max(120, int(cfg["strip_w"] * 1.25))
        self.setFixedWidth(w)
        self.setObjectName("mini")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#mini {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(4)
        head = QLabel("  ♪ ПЛЕЕР")
        head.setStyleSheet(f"background: {theme.ACCENT}; color: {theme.INK}; font-weight: 700; padding: 2px;")
        lay.addWidget(head)
        body = QVBoxLayout()
        body.setContentsMargins(6, 2, 6, 0)
        body.setSpacing(4)
        lay.addLayout(body)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setStyleSheet(f"color: {theme.ACCENT2}; font-weight: 700; font-size: {8.5 * s:.1f}pt;")
        self.title.setMaximumHeight(int(34 * max(1.0, s)))
        body.addWidget(self.title)
        self.seek = SeekBar(10)
        self.seek.seek.connect(lambda sec: ctx.engine.player.seek(sec))
        body.addWidget(self.seek)
        self.time = QLabel()
        self.time.setObjectName("cap")
        body.addWidget(self.time)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.btns = {}
        for op, txt in (("prev", "◄◄"), ("play_pause", "►"), ("next", "►►"), ("stop", "■")):
            b = QPushButton(txt)
            b.setStyleSheet(f"padding: 2px 0; font-weight: 700; font-size: {9 * s:.1f}pt; min-height: 0;")
            b.setMinimumHeight(int(26 * s))
            b.clicked.connect(lambda _=False, op=op: (ctx.engine.player.command(op), self.sync()))
            self.btns[op] = b
            row.addWidget(b)
        body.addLayout(row)
        vr = QHBoxLayout()
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.valueChanged.connect(lambda v: (ctx.engine.player.set_volume(v / 100), ctx.save_later()))
        vr.addWidget(self.vol, 1)
        body.addLayout(vr)
        if cfg["show_keys"]:
            from .player_page import player_keys
            k = player_keys(ctx.project)
            parts = [f"{sym} {k[op][0]}" for op, sym in (("play_pause", "►"), ("next", "►►"), ("prev", "◄◄")) if k.get(op)]
            if parts:
                body.addWidget(_KeyLabel("  ".join(parts), s))
        lay.addStretch(1)
        self.sync()

    def sync(self):
        pl = self.ctx.engine.player
        cur = pl.current()
        fm = self.title.fontMetrics()
        self.title.setText(fm.elidedText(cur["title"] if cur else "плейлист пуст", Qt.ElideRight, self.width() * 2 - 30))
        pos, dur = pl.position(), pl.duration()
        self.seek.set_pos(pos, dur)
        self.time.setText(f"{int(pos // 60)}:{int(pos % 60):02d} / {int(dur // 60)}:{int(dur % 60):02d}")
        self.btns["play_pause"].setText("❚❚" if pl.state in ("playing", "loading") else "►")
        v = int(round(self.ctx.project.player.volume * 100))
        if self.vol.value() != v:
            self.vol.blockSignals(True)
            self.vol.setValue(v)
            self.vol.blockSignals(False)


class RecStrip(QWidget):
    """Блок записи: кнопка ● (для коротких звуков — зажать и говорить), таймер, клавиши."""

    def __init__(self, ctx, rec, cfg):
        super().__init__()
        self.ctx = ctx
        self.rec = rec
        s = cfg["scale"]
        self.setFixedWidth(int(cfg["strip_w"]))
        self.setObjectName("mini")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#mini {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(3)
        self.clip = rec.recorder == "clip"
        head = QLabel(f"  ● {rec.name.upper()}")
        head.setToolTip(rec.name)
        head.setStyleSheet(f"background: {theme.DANGER}; color: {theme.INK}; font-weight: 700; padding: 2px;")
        lay.addWidget(head)
        body = QVBoxLayout()
        body.setContentsMargins(6, 2, 6, 0)
        body.setSpacing(3)
        lay.addLayout(body)
        self.btn = QPushButton()
        self.btn.setMinimumHeight(int(34 * s))
        self.btn.setToolTip("Зажмите и говорите — отпустите, и звук появится в саундпаде" if self.clip
                            else "Начать / закончить запись в файл")
        self.replay = rec.recorder == "replay"
        if self.replay:
            self.btn.setToolTip("Сохранить последние секунды в «Звуки»")
            self.btn.clicked.connect(lambda: ctx.engine.rec_command(rec.id, "save"))
        elif self.clip:
            self.btn.pressed.connect(lambda: ctx.engine.rec_command(rec.id, "start"))
            self.btn.released.connect(lambda: ctx.engine.rec_command(rec.id, "stop"))
        else:
            self.btn.clicked.connect(lambda: ctx.engine.rec_command(rec.id, "toggle"))
        body.addWidget(self.btn)
        self.meter = Meter(vertical=False)
        body.addWidget(self.meter)
        self.info = QLabel()
        self.info.setObjectName("cap")
        self.info.setStyleSheet(f"font-size: {7.5 * s:.1f}pt;")
        body.addWidget(self.info)
        if cfg["show_keys"]:
            k = keys_for(ctx.project, lambda a: a.get("type") == "record" and a.get("rec") == rec.id)
            if k:
                body.addWidget(_KeyLabel(k, s))
        lay.addStretch(1)
        self._on = None
        self.sync()

    def sync(self):
        st = self.ctx.engine.rec_states().get(self.rec.id) or {}
        on = bool(st.get("on"))
        self.meter.set_level(st.get("level", 0.0))
        t = st.get("t", 0.0)
        if self.replay:
            if self._on is None:
                self._on = False
                self.btn.setText("⟲ СОХРАНИТЬ МОМЕНТ")
            b, c = st.get("buf", 0.0), st.get("cap", 0.0)
            self.info.setText("сохраняю…" if st.get("busy") else f"буфер {int(b)} / {int(c)} с")
            return
        if on != self._on:
            self._on = on
            if on:
                self.btn.setText("■ СТОП" if not self.clip else "● ЗАПИСЬ…")
                self.btn.setStyleSheet(f"background: {theme.DANGER}; color: {theme.INK}; font-weight: 700;")
            else:
                self.btn.setText("● ЗАЖМИ И ГОВОРИ" if self.clip else "● ЗАПИСЬ")
                self.btn.setStyleSheet("")
        if on:
            self.info.setText(f"{int(t // 60):02d}:{t % 60:04.1f}")
        elif st.get("busy"):
            self.info.setText("сохраняю…")
        else:
            self.info.setText("→ в звуки" if self.clip else "→ в файл")


class SwitchStrip(QWidget):
    def __init__(self, ctx, sw, cfg):
        super().__init__()
        self.ctx = ctx
        self.sw = sw
        s = cfg["scale"]
        pr = ctx.project
        self.setFixedWidth(int(cfg["strip_w"]))
        self.setObjectName("mini")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#mini {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(3)
        head = QLabel(f"  {'1→N' if sw.switch == 'split' else 'N→1'} · {sw.name.upper()}")
        head.setStyleSheet(f"background: {theme.ACCENT2}; color: {theme.INK}; font-weight: 700; padding: 2px;")
        lay.addWidget(head)
        keys = {}
        for b in pr.bindings:
            a = b.action
            if a.get("type") == "switch" and a.get("switch") == sw.id:
                keys.setdefault(a.get("op") if a.get("op") not in ("set", "toggle", "hold") else int(a.get("index", 0)),
                                []).append(b.trigger.label().upper())
        self.group = QButtonGroup(self)
        self.group.setExclusive(False)
        self.btns = []
        for i, pid in enumerate(pr.switch_ports(sw)):
            txt = f"{i + 1}  {pr.node_name(pid).upper()}"
            if cfg["show_keys"] and keys.get(i):
                txt += f"\n   {keys[i][0]}"
            b = QPushButton(txt)
            b.setCheckable(True)
            b.setStyleSheet(f"text-align: left; padding: 3px 6px; font-size: {8.5 * s:.1f}pt;")
            b.clicked.connect(lambda _=False, i=i: self._pick(i))
            self.btns.append(b)
            lay.addWidget(b)
        if not self.btns:
            lab = QLabel("  нет подключений")
            lab.setObjectName("cap")
            lay.addWidget(lab)
        off = QPushButton("■ ВЫКЛ" + (f"  ·  {keys['off'][0]}" if cfg["show_keys"] and keys.get("off") else ""))
        off.clicked.connect(lambda: self._pick(-1))
        lay.addWidget(off)
        if cfg["show_keys"]:
            nav = [f"{sym} {keys[op][0]}" for op, sym in (("next", "▸"), ("prev", "◂")) if keys.get(op)]
            if nav:
                lay.addWidget(_KeyLabel("  " + "   ".join(nav), s))
        lay.addStretch(1)
        self.sync()

    def _pick(self, i):
        self.ctx.engine.switch_set(self.sw.id, -1 if (i >= 0 and self.sw.active == i) else i)
        self.sync()

    def sync(self):
        for i, b in enumerate(self.btns):
            on = i == self.sw.active
            if b.isChecked() != on:
                b.setChecked(on)


class QuickMixer(QWidget):
    def __init__(self, ctx, open_main):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.ctx = ctx
        self.open_main = open_main
        self.setObjectName("quick")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._drag = None
        self.strips: list = []
        self.switch_strips: list = []
        self.rec_strips: list = []
        self.player_strip = None
        self.pad_btns: dict[str, QPushButton] = {}
        self._playing: set = set()
        self._settings = None
        self.cfg = dict(DEFAULTS)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(2, 2, 2, 2)
        self.root.setSpacing(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------ сборка
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
        clear(self.root)
        self.strips = []
        self.switch_strips = []
        self.rec_strips = []
        self.player_strip = None
        self.pad_btns = {}
        self._playing = set()

    @staticmethod
    def _pick(items, wanted):
        return list(items) if wanted is None else [x for x in items if x.id in wanted]

    def build(self):
        self._clear()
        pr = self.ctx.project
        cfg = self.cfg = quick_cfg(pr)
        s = cfg["scale"]
        self.setStyleSheet(f"QWidget#quick {{ background: {theme.BG}; border: 2px solid {theme.ACCENT}; }}"
                           f"QWidget#quick QPushButton, QWidget#quick QLabel {{ font-size: {9.5 * s:.1f}pt; }}")
        self.setWindowOpacity(max(0.3, min(1.0, cfg["opacity"] / 100)))
        head = QWidget()
        head.setObjectName("qhead")
        head.setAttribute(Qt.WA_StyledBackground, True)
        head.setStyleSheet(f"QWidget#qhead {{ background: {theme.ACCENT}; }}")
        head.setCursor(Qt.SizeAllCursor)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(8, 0, 2, 0)
        hl.setSpacing(4)
        t = QLabel("БЫСТРЫЙ МИКШЕР")
        t.setStyleSheet(f"color: {theme.INK}; font-weight: 700;")
        hl.addWidget(t)
        k = keys_for(pr, lambda a: a.get("type") == "quick_mixer")
        if k and cfg["show_keys"]:
            kl = QLabel(f"·  {k} — закрыть")
            kl.setStyleSheet(f"color: {theme.INK}; font-size: {8 * s:.1f}pt;")
            hl.addWidget(kl)
        hl.addStretch(1)
        btn_css = (f"QPushButton {{ background: {theme.INK}; color: {theme.ACCENT}; border: none; padding: 0 8px;"
                   f" font-weight: 700; min-height: 0; }} QPushButton:hover {{ color: white; }}")
        gear = QPushButton("НАСТРОИТЬ")
        gear.setFixedHeight(20)
        gear.setStyleSheet(btn_css)
        gear.setToolTip("Размер, что показывать, прозрачность, где появляться")
        gear.clicked.connect(lambda: self.open_settings())
        hl.addWidget(gear)
        x = QPushButton("✕")
        x.setFixedSize(26, 20)
        x.setStyleSheet(btn_css)
        x.clicked.connect(self.hide)
        hl.addWidget(x)
        head.setFixedHeight(int(24 * max(1.0, s)))
        head.mousePressEvent = self._start_drag
        head.mouseMoveEvent = self._do_drag
        self.root.addWidget(head)

        body = QHBoxLayout()
        body.setContentsMargins(6, 6, 6, 6)
        body.setSpacing(6)
        if cfg["show_player"] and pr.player.playlist:
            self.player_strip = PlayerStrip(self.ctx, cfg)
            body.addWidget(self.player_strip)
        for ch in self._pick(pr.channels, cfg["channels"]):
            st = MiniStrip(self.ctx, ch, cfg)
            self.strips.append(st)
            body.addWidget(st)
        outs = [o for o in pr.outputs if not o.switch and not o.recorder and o.id in (cfg["outputs"] or [])]
        for o in outs:
            st = MiniStrip(self.ctx, o, cfg, is_output=True)
            self.strips.append(st)
            body.addWidget(st)
        if cfg["show_switches"]:
            for sw in self._pick([o for o in pr.outputs if o.switch], cfg["switches"]):
                ss = SwitchStrip(self.ctx, sw, cfg)
                self.switch_strips.append(ss)
                body.addWidget(ss)
        if cfg.get("show_rec", True):
            for rec in [o for o in pr.outputs if o.recorder and o.enabled]:
                rs = RecStrip(self.ctx, rec, cfg)
                self.rec_strips.append(rs)
                body.addWidget(rs)

        side = QVBoxLayout()
        side.setSpacing(4)
        pads = self._pick(pr.pads, cfg["pads"]) if cfg["show_pads"] else []
        cols = max(1, int(cfg["pad_cols"]))
        bw, bh = int(cfg["pad_w"]), int(cfg["pad_h"])
        if pads:
            side.addWidget(caption("Звуки — клик, чтобы сыграть"))
            grid_host = QWidget()
            grid_host.setObjectName("page")
            grid = QGridLayout(grid_host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(3)
            grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            for i, p in enumerate(pads):
                keys = keys_for(pr, lambda a, p=p: a.get("type") == "pad" and a.get("pad") == p.id)
                b = QPushButton(f"{p.name.upper()}\n{keys or '—'}" if cfg["show_keys"] else p.name.upper())
                b.setFixedSize(bw, bh)
                b.setToolTip(p.name + (f"\nКлавиши: {keys}" if keys else ""))
                b.pressed.connect(lambda pid=p.id: self.ctx.engine.pad_event(pid, "press"))
                b.released.connect(lambda pid=p.id: self.ctx.engine.pad_event(pid, "release"))
                self._style_pad(b, p, False)
                self.pad_btns[p.id] = b
                grid.addWidget(b, i // cols, i % cols)
            sc = QScrollArea()
            sc.setWidget(grid_host)
            sc.setWidgetResizable(True)
            sc.setStyleSheet(f"QScrollArea {{ background: {theme.BG}; border: 1px solid {theme.BORDER}; }}")
            sc.setFixedWidth(bw * cols + 3 * (cols - 1) + 16)
            rows = (len(pads) + cols - 1) // cols
            sc.setMinimumHeight(min(rows, 4) * (bh + 3) + 6)
            side.addWidget(sc, 1)
        if cfg["show_stop"]:
            stop = QPushButton("■ СТОП ВСЁ")
            stop.setObjectName("danger")
            stop.clicked.connect(lambda: self.ctx.engine.stop_all())
            sk = keys_for(pr, lambda a: a.get("type") == "stop_all")
            if sk and cfg["show_keys"]:
                stop.setText(f"■ СТОП ВСЁ  ·  {sk}")
            side.addWidget(stop)
            open_b = QPushButton("ОТКРЫТЬ МЯУКАЛО")
            open_b.clicked.connect(lambda: (self.hide(), self.open_main()))
            side.addWidget(open_b)
        if side.count():
            body.addLayout(side)
        if not body.count():
            lab = QLabel("Ничего не выбрано для показа — нажмите «НАСТРОИТЬ».")
            lab.setObjectName("muted")
            body.addWidget(lab)
        self.root.addLayout(body)
        self.setMinimumSize(0, 0)
        self.adjustSize()
        self.resize(self.sizeHint())

    def _style_pad(self, b, p, playing):
        fs = 8 * self.cfg.get("scale", 1.0)
        if playing:
            b.setStyleSheet(f"QPushButton {{ background: {theme.ACCENT}; color: {theme.INK}; border: 1px solid {theme.ACCENT};"
                            f" border-left: 6px solid {p.color}; text-align: left; padding: 2px 6px; font-size: {fs:.1f}pt; font-weight: 700; }}")
        else:
            b.setStyleSheet(f"QPushButton {{ text-align: left; padding: 2px 6px; font-size: {fs:.1f}pt; border-left: 6px solid {p.color}; }}")

    # ------------------------------------------------------------ настройки
    def open_settings(self, parent=None):
        if self._settings is not None:
            return
        self.hide()
        self._settings = QuickSettingsDialog(self.ctx, parent)
        try:
            self._settings.exec()
        finally:
            self._settings.deleteLater()
            self._settings = None

    # ------------------------------------------------------------ показ
    def toggle(self):
        if self._settings is not None:
            return
        if self.isVisible():
            self.hide()
            return
        self.build()
        cfg = self.cfg
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        if cfg["position"] == "remember" and cfg.get("pos"):
            x, y = cfg["pos"]
            self.move(int(x), int(y))
        elif cfg["position"] == "cursor":
            c = QCursor.pos() - QPoint(self.width() // 2, 20)
            c.setX(max(g.left(), min(g.right() - self.width(), c.x())))
            c.setY(max(g.top(), min(g.bottom() - self.height(), c.y())))
            self.move(c)
        else:
            self.move(g.center() - QPoint(self.width() // 2, self.height() // 2))
        self.show()
        self.raise_()
        self.activateWindow()
        self.timer.start(40)

    def hideEvent(self, e):
        self.timer.stop()
        if self.cfg.get("position") == "remember":
            q = dict(self.ctx.project.ui.get("quick") or {})
            q["pos"] = [self.pos().x(), self.pos().y()]
            self.ctx.project.ui["quick"] = q
            self.ctx.save_later()
        super().hideEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)

    def _start_drag(self, e):
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _do_drag(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def _tick(self):
        e = self.ctx.engine
        peaks = e.channel_peaks()
        opeaks = e.output_peaks()
        for st in self.strips:
            st.meter.set_level((opeaks if st.is_output else peaks).get(st.ch.id, 0.0))
            st.sync()
        for ss in self.switch_strips:
            ss.sync()
        for rs in self.rec_strips:
            rs.sync()
        if self.player_strip is not None:
            self.player_strip.sync()
        playing = set(e.pad_states())
        if playing != self._playing:
            for pid in playing ^ self._playing:
                b = self.pad_btns.get(pid)
                p = self.ctx.project.pad(pid)
                if b and p:
                    self._style_pad(b, p, pid in playing)
            self._playing = playing


class QuickSettingsDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        pr = ctx.project
        cfg = quick_cfg(pr)
        self.setWindowTitle("Настройки быстрого микшера")
        self.setMinimumWidth(780)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 12)
        bar = QLabel("  БЫСТРЫЙ МИКШЕР · НАСТРОЙКИ")
        bar.setStyleSheet(f"background: {theme.ACCENT}; color: {theme.INK}; font-weight: 700; padding: 4px;")
        lay.addWidget(bar)
        top = QGridLayout()
        top.setContentsMargins(12, 8, 12, 4)
        top.setHorizontalSpacing(10)
        top.setVerticalSpacing(6)
        self.scale = QComboBox()
        for v, t in SCALES:
            self.scale.addItem(t, v)
        self.scale.setCurrentIndex(min(range(len(SCALES)), key=lambda i: abs(SCALES[i][0] - cfg["scale"])))
        self.pos = QComboBox()
        for k, v in POSITIONS.items():
            self.pos.addItem(v, k)
        self.pos.setCurrentIndex(max(0, self.pos.findData(cfg["position"])))
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(40, 100)
        self.opacity.setValue(int(cfg["opacity"]))
        self.op_l = QLabel(f"{self.opacity.value()}%")
        self.opacity.valueChanged.connect(lambda v: self.op_l.setText(f"{v}%"))
        self.cols = QSpinBox()
        self.cols.setRange(1, 6)
        self.cols.setValue(int(cfg["pad_cols"]))
        top.addWidget(caption("Размер"), 0, 0)
        top.addWidget(self.scale, 0, 1)
        top.addWidget(caption("Где появляться"), 0, 2)
        top.addWidget(self.pos, 0, 3)
        top.addWidget(caption("Непрозрачность"), 1, 0)
        orow = QHBoxLayout()
        orow.addWidget(self.opacity, 1)
        orow.addWidget(self.op_l)
        top.addLayout(orow, 1, 1)
        top.addWidget(caption("Столбцов звуков"), 1, 2)
        top.addWidget(self.cols, 1, 3)

        def spin(lo, hi, val, step=4):
            sp = QSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setSuffix(" px")
            sp.setValue(int(val))
            return sp

        self.strip_w = spin(80, 260, cfg["strip_w"])
        self.fader_h = spin(40, 220, cfg["fader_h"])
        self.pad_w = spin(60, 280, cfg["pad_w"])
        self.pad_h = spin(22, 90, cfg["pad_h"], 2)
        top.addWidget(caption("Ширина полосок громкости"), 2, 0)
        top.addWidget(self.strip_w, 2, 1)
        top.addWidget(caption("Высота фейдера"), 2, 2)
        top.addWidget(self.fader_h, 2, 3)
        top.addWidget(caption("Ширина кнопок звуков"), 3, 0)
        top.addWidget(self.pad_w, 3, 1)
        top.addWidget(caption("Высота кнопок звуков"), 3, 2)
        top.addWidget(self.pad_h, 3, 3)
        pre = QHBoxLayout()
        pre.addWidget(caption("Быстро"))
        for name, vals in SIZE_PRESETS.items():
            b = QPushButton(name.upper())
            b.setStyleSheet("padding: 2px 8px;")
            b.clicked.connect(lambda _=False, v=vals: [w.setValue(x) for w, x in
                                                       zip((self.strip_w, self.fader_h, self.pad_w, self.pad_h), v)])
            pre.addWidget(b)
        pre.addStretch(1)
        top.addLayout(pre, 4, 0, 1, 4)
        lay.addLayout(top)

        flags = QGridLayout()
        flags.setContentsMargins(12, 2, 12, 2)
        flags.setHorizontalSpacing(16)
        self.flags = {}
        for i, (key, text) in enumerate((("show_fx", "Кнопки эффектов"), ("show_mute", "Кнопки «Заглушить»"),
                                         ("show_keys", "Подписи клавиш"), ("show_switches", "Переключатели"),
                                         ("show_pads", "Звуки"), ("show_stop", "«Стоп всё» и «Открыть»"),
                                         ("show_player", "Плеер"), ("show_rec", "Блоки записи"))):
            cb = QCheckBox(text)
            cb.setChecked(bool(cfg[key]))
            self.flags[key] = cb
            flags.addWidget(cb, i // 3, i % 3)
        flags.setColumnStretch(3, 1)
        lay.addLayout(flags)

        lists = QHBoxLayout()
        lists.setContentsMargins(12, 4, 12, 4)
        self.l_ch = self._list(lists, "Каналы", pr.channels, cfg["channels"])
        self.l_out = self._list(lists, "Выходы и шины", [o for o in pr.outputs if not o.switch and not o.recorder], cfg["outputs"] or [],
                                all_default=False)
        self.l_sw = self._list(lists, "Переключатели", [o for o in pr.outputs if o.switch], cfg["switches"])
        self.l_pad = self._list(lists, "Звуки", pr.pads, cfg["pads"])
        lay.addLayout(lists, 1)
        btns = QHBoxLayout()
        btns.setContentsMargins(12, 6, 12, 0)
        reset = QPushButton("ПО УМОЛЧАНИЮ")
        reset.clicked.connect(self._reset)
        btns.addWidget(reset)
        btns.addStretch(1)
        b_c = QPushButton("ОТМЕНА")
        b_c.clicked.connect(self.reject)
        b_ok = QPushButton("ПРИМЕНИТЬ")
        b_ok.setObjectName("primary")
        b_ok.clicked.connect(self._save)
        btns.addWidget(b_c)
        btns.addWidget(b_ok)
        lay.addLayout(btns)

    def _list(self, parent_lay, title, items, wanted, all_default=True):
        col = QVBoxLayout()
        col.addWidget(caption(title))
        lw = QListWidget()
        lw.setMinimumHeight(180)
        for x in items:
            it = QListWidgetItem(x.name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            on = (wanted is None and all_default) or (wanted is not None and x.id in wanted)
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
            it.setData(Qt.UserRole, x.id)
            lw.addItem(it)
        col.addWidget(lw, 1)
        row = QHBoxLayout()
        for text, state in (("ВСЕ", Qt.Checked), ("НИЧЕГО", Qt.Unchecked)):
            b = QPushButton(text)
            b.setStyleSheet("padding: 2px 6px;")
            b.clicked.connect(lambda _=False, lw=lw, state=state: [lw.item(i).setCheckState(state) for i in range(lw.count())])
            row.addWidget(b)
        col.addLayout(row)
        parent_lay.addLayout(col, 1)
        return lw

    @staticmethod
    def _checked(lw):
        ids = [lw.item(i).data(Qt.UserRole) for i in range(lw.count()) if lw.item(i).checkState() == Qt.Checked]
        return None if len(ids) == lw.count() else ids     # «все» храним как None — новые звуки тоже появятся

    def _reset(self):
        self.ctx.project.ui["quick"] = dict(DEFAULTS)
        self.ctx.save_later()
        self.accept()

    def _save(self):
        q = dict(self.ctx.project.ui.get("quick") or {})
        q.update({
            "scale": self.scale.currentData(), "position": self.pos.currentData(), "opacity": self.opacity.value(),
            "pad_cols": self.cols.value(),
            "strip_w": self.strip_w.value(), "fader_h": self.fader_h.value(),
            "pad_w": self.pad_w.value(), "pad_h": self.pad_h.value(),
            "channels": self._checked(self.l_ch), "switches": self._checked(self.l_sw), "pads": self._checked(self.l_pad),
            "outputs": [self.l_out.item(i).data(Qt.UserRole) for i in range(self.l_out.count())
                        if self.l_out.item(i).checkState() == Qt.Checked],
        })
        for k, cb in self.flags.items():
            q[k] = cb.isChecked()
        self.ctx.project.ui["quick"] = q
        self.ctx.save_later()
        self.accept()
