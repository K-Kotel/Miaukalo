"""Модель данных проекта: выходы, каналы, звуки (пэды), привязки, настройки.

Всё хранится в одном JSON-файле. Классы терпимы к отсутствующим полям,
чтобы старые конфиги открывались в новых версиях.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "Miaukalo"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    (d / "library").mkdir(exist_ok=True)
    return d


def resource_path(name: str) -> Path:
    """Файл рядом с программой (работает и в собранном .exe)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / name


# ---------------------------------------------------------------- dataclasses

@dataclass
class Output:
    id: str
    name: str
    device: str = ""          # имя устройства как его показывает система
    hostapi: str = ""         # "Windows WASAPI" и т.п.; пусто = любое
    volume: float = 1.0
    enabled: bool = True
    bus: bool = False         # только шина: своего устройства нет, звук лишь пересылается дальше
    sends: list = field(default_factory=list)   # id выходов, куда идёт копия итогового звука
    effects: list = field(default_factory=list)  # list[EffectSlot] — эффекты на итоговый звук выхода/шины
    switch: str = ""          # "" | "split" (1 вход → N выходов) | "merge" (N входов → 1 выход)
    active: int = 0           # активное положение переключателя, -1 = выключен
    sw_inputs: list = field(default_factory=list)  # порядок входов переключателя N→1 (id канала/выхода/звука)
    fade_ms: int = 120        # плавность переключения
    recorder: str = ""        # "" | "file" (в файл) | "clip" (короткие звуки) | "replay" (последние N секунд)
    muted: bool = False       # заглушён (горячей клавишей / кнопкой) — устройство остаётся открытым
    rec: dict = field(default_factory=dict)        # настройки записи (см. audio/recorder.rec_defaults)

    @staticmethod
    def from_dict(d: dict) -> "Output":
        return Output(
            id=d.get("id") or new_id("out"),
            name=d.get("name", "Выход"),
            device=d.get("device", ""),
            hostapi=d.get("hostapi", ""),
            volume=float(d.get("volume", 1.0)),
            enabled=bool(d.get("enabled", True)),
            bus=bool(d.get("bus", False)),
            sends=list(d.get("sends", [])),
            effects=[EffectSlot.from_dict(e) for e in d.get("effects", [])],
            switch=d.get("switch", "") if d.get("switch") in ("split", "merge") else "",
            active=int(d.get("active", 0)),
            sw_inputs=list(d.get("sw_inputs", [])),
            fade_ms=int(d.get("fade_ms", 120)),
            recorder=d.get("recorder", "") if d.get("recorder") in ("file", "clip", "replay") else "",
            muted=bool(d.get("muted", False)),
            rec=dict(d.get("rec") or {}),
        )


@dataclass
class EffectSlot:
    id: str
    type: str
    enabled: bool = False
    params: dict = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict) -> "EffectSlot":
        return EffectSlot(
            id=d.get("id") or new_id("fx"),
            type=d.get("type", "reverb"),
            enabled=bool(d.get("enabled", False)),
            params=dict(d.get("params", {})),
        )


TRANSITIONS = {
    "overlay": "Наложение",
    "cut": "Обрыв",
    "crossfade": "Кроссфейд",
    "fade_out_in": "Затухание → новый",
}
TRANSITION_HINTS = {
    "overlay": "Звуки звучат одновременно, друг поверх друга",
    "cut": "Новый звук сразу обрывает предыдущий",
    "crossfade": "Старый плавно затихает, новый одновременно плавно нарастает",
    "fade_out_in": "Старый затихает, и только потом начинается новый",
}


@dataclass
class Channel:
    id: str
    name: str
    volume: float = 0.8
    muted: bool = False
    outputs: list = field(default_factory=list)   # id выходов
    transition: str = "overlay"
    transition_ms: int = 1000
    effects: list = field(default_factory=list)    # list[EffectSlot]
    color: str = "#ff9a1f"

    @staticmethod
    def from_dict(d: dict) -> "Channel":
        return Channel(
            id=d.get("id") or new_id("ch"),
            name=d.get("name", "Канал"),
            volume=float(d.get("volume", 0.8)),
            muted=bool(d.get("muted", False)),
            outputs=list(d.get("outputs", [])),
            transition=d.get("transition", "overlay") if d.get("transition") in TRANSITIONS else "overlay",
            transition_ms=int(d.get("transition_ms", 1000)),
            effects=[EffectSlot.from_dict(e) for e in d.get("effects", [])],
            color=d.get("color", "#ff9a1f"),
        )


LIVE_TYPES = {
    "input": "Устройство ввода (микрофон, кабель)",
    "process": "Звук программы (браузер, плеер…)",
    "loopback": "Всё, что играет на устройстве вывода",
    "tab": "Вкладка браузера (через расширение)",
}


@dataclass
class LiveSource:
    type: str = "input"
    device: str = ""
    hostapi: str = ""
    process: str = ""

    @staticmethod
    def from_dict(d: dict | None) -> "LiveSource":
        d = d or {}
        return LiveSource(
            type=d.get("type", "input") if d.get("type") in LIVE_TYPES else "input",
            device=d.get("device", ""),
            hostapi=d.get("hostapi", ""),
            process=d.get("process", ""),
        )


PAD_MODES = {
    "oneshot": "Нажал — проигралось",
    "toggle": "Нажал — играет, нажал — стоп",
    "hold": "Звучит, пока держишь",
}

RETRIGGER = {
    "restart": "Начать заново",
    "overlap": "Ещё один поверх",
    "ignore": "Игнорировать",
    "stop": "Остановить",
}


@dataclass
class Pad:
    id: str
    name: str
    kind: str = "file"               # file | live
    file: str = ""
    trim_start: float = 0.0          # секунды
    trim_end: float = 0.0            # 0 = до конца
    gain_db: float = 0.0
    channel: str = ""
    mode: str = "oneshot"
    loop: bool = False
    retrigger: str = "restart"
    fade_in_ms: int = 0
    fade_out_ms: int = 60
    color: str = "#ff9a1f"
    source: LiveSource = field(default_factory=LiveSource)
    autostart: bool = False
    exclude_outputs: list = field(default_factory=list)  # id выходов, куда этот звук НЕ идёт
    route_own: bool = False       # своя разводка: провода канала игнорируются
    outputs: list = field(default_factory=list)          # выходы при своей разводке

    @staticmethod
    def from_dict(d: dict) -> "Pad":
        return Pad(
            id=d.get("id") or new_id("pad"),
            name=d.get("name", "Звук"),
            kind=d.get("kind", "file") if d.get("kind") in ("file", "live") else "file",
            file=d.get("file", ""),
            trim_start=float(d.get("trim_start", 0.0)),
            trim_end=float(d.get("trim_end", 0.0)),
            gain_db=float(d.get("gain_db", 0.0)),
            channel=d.get("channel", ""),
            mode=d.get("mode", "oneshot") if d.get("mode") in PAD_MODES else "oneshot",
            loop=bool(d.get("loop", False)),
            retrigger=d.get("retrigger", "restart") if d.get("retrigger") in RETRIGGER else "restart",
            fade_in_ms=int(d.get("fade_in_ms", 0)),
            fade_out_ms=int(d.get("fade_out_ms", 60)),
            color=d.get("color", "#ff9a1f"),
            source=LiveSource.from_dict(d.get("source")),
            autostart=bool(d.get("autostart", False)),
            exclude_outputs=list(d.get("exclude_outputs", [])),
            route_own=bool(d.get("route_own", False)),
            outputs=list(d.get("outputs", [])),
        )


@dataclass
class Trigger:
    type: str = "key"        # key | midi_note | midi_cc
    combo: str = ""          # для клавиатуры: "ctrl+num 1"
    channel: int = -1        # MIDI-канал, -1 = любой
    number: int = -1         # нота / номер CC
    port: str = ""           # пусто = любое MIDI-устройство

    @staticmethod
    def from_dict(d: dict | None) -> "Trigger":
        d = d or {}
        return Trigger(
            type=d.get("type", "key"),
            combo=d.get("combo", ""),
            channel=int(d.get("channel", -1)),
            number=int(d.get("number", -1)),
            port=d.get("port", ""),
        )

    def label(self) -> str:
        if self.type == "key":
            return pretty_combo(self.combo) if self.combo else "—"
        ch = f" к{self.channel + 1}" if self.channel >= 0 else ""
        if self.type == "midi_note":
            return f"MIDI нота {note_name(self.number)}{ch}"
        if self.type == "midi_cc":
            return f"MIDI CC {self.number}{ch}"
        return "?"

    def key(self) -> str:
        if self.type == "key":
            return "key:" + self.combo
        return f"{self.type}:{self.number}"


ACTIONS = {
    "pad": "Звук (по режиму звука)",
    "stop_pad": "Остановить звук",
    "stop_channel": "Остановить канал",
    "stop_all": "Остановить всё",
    "volume_step": "Громкость канала +/−",
    "volume_set": "Громкость канала (крутилка MIDI)",
    "mute": "Выключить звук канала",
    "effect": "Включить/выключить эффект",
    "quick_mixer": "Открыть / закрыть быстрый микшер",
    "switch": "Переключатель на схеме",
    "player": "Плеер",
    "record": "Запись (блок записи на схеме)",
    "mute_out": "Заглушить выход / шину",
    "reverse": "Задом наперёд (играющие звуки)",
    "pad_random": "Звук со случайными эффектами",
    "drum": "Дрампад",
}


@dataclass
class Binding:
    id: str
    trigger: Trigger
    action: dict

    @staticmethod
    def from_dict(d: dict) -> "Binding":
        return Binding(
            id=d.get("id") or new_id("bind"),
            trigger=Trigger.from_dict(d.get("trigger")),
            action=dict(d.get("action", {"type": "stop_all"})),
        )


@dataclass
class PlayerCfg:
    channel: str = ""
    playlist: list = field(default_factory=list)   # [{"path", "title", "dur"}]
    index: int = 0
    shuffle: bool = False
    repeat: str = "all"        # all | one | off
    volume: float = 0.85
    crossfade: float = 2.0     # секунды плавного перехода между треками, 0 — без перехода

    @staticmethod
    def from_dict(d: dict | None) -> "PlayerCfg":
        d = d or {}
        pl = []
        for it in d.get("playlist", []):
            if isinstance(it, str):
                it = {"path": it}
            if isinstance(it, dict) and it.get("path"):
                from pathlib import Path
                pl.append({"path": it["path"], "title": it.get("title") or Path(it["path"]).stem,
                           "dur": float(it.get("dur") or 0.0)})
        return PlayerCfg(channel=d.get("channel", ""), playlist=pl, index=int(d.get("index", 0)),
                         shuffle=bool(d.get("shuffle", False)),
                         repeat=d.get("repeat", "all") if d.get("repeat") in ("all", "one", "off") else "all",
                         volume=float(d.get("volume", 0.85)), crossfade=float(d.get("crossfade", 2.0)))


@dataclass
class Settings:
    latency_ms: int = 60
    preview_output: str = ""
    show_all_hostapis: bool = False
    hotkeys_enabled: bool = True
    midi_enabled: bool = True
    minimize_to_tray: bool = True

    @staticmethod
    def from_dict(d: dict | None) -> "Settings":
        d = d or {}
        s = Settings()
        for k in asdict(s):
            if k in d:
                setattr(s, k, type(getattr(s, k))(d[k]))
        return s


@dataclass
class Project:
    outputs: list = field(default_factory=list)
    channels: list = field(default_factory=list)
    pads: list = field(default_factory=list)
    bindings: list = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)
    layout: dict = field(default_factory=dict)   # позиции блоков в «Коммутации»: id -> [x, y]
    ui: dict = field(default_factory=dict)       # состояние схемы: раскрытые каналы, компактные блоки, рамки
    player: PlayerCfg = field(default_factory=PlayerCfg)
    drum: dict = field(default_factory=dict)     # дрампад: пэды, петля, темп (см. audio/drums.py)
    editor: dict = field(default_factory=dict)

    # ------------------------------------------------------------ lookup
    def output(self, oid):
        return next((o for o in self.outputs if o.id == oid), None)

    def channel(self, cid):
        return next((c for c in self.channels if c.id == cid), None)

    def pad(self, pid):
        return next((p for p in self.pads if p.id == pid), None)

    def send_makes_loop(self, src_out: str, dst_out: str) -> bool:
        """Создаст ли пересылка src → dst петлю (dst уже ведёт обратно в src)."""
        if src_out == dst_out:
            return True
        seen, stack = set(), [dst_out]
        while stack:
            cur = stack.pop()
            if cur == src_out:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            o = self.output(cur)
            if o:
                stack.extend(o.sends)
        return False

    def switch_ports(self, sw) -> list:
        """Положения переключателя: id входов (N→1) или выходов (1→N)."""
        return list(sw.sw_inputs) if sw.switch == "merge" else list(sw.sends)

    def node_name(self, nid) -> str:
        x = self.channel(nid) or self.output(nid) or self.pad(nid)
        return x.name if x else "?"

    def output_order(self) -> list:
        """Выходы в порядке, где источник пересылки всегда раньше получателя."""
        outs = {o.id: o for o in self.outputs}
        indeg = {oid: 0 for oid in outs}
        for o in self.outputs:
            for t in o.sends:
                if t in indeg:
                    indeg[t] += 1
        ready = [oid for oid in outs if indeg[oid] == 0]
        res = []
        while ready:
            oid = ready.pop(0)
            res.append(outs[oid])
            for t in outs[oid].sends:
                if t in indeg:
                    indeg[t] -= 1
                    if indeg[t] == 0:
                        ready.append(t)
        res += [o for o in self.outputs if o not in res]   # на случай петли — не теряем выходы
        return res

    def bindings_for_pad(self, pid):
        return [b for b in self.bindings if b.action.get("type") == "pad" and b.action.get("pad") == pid]

    # ------------------------------------------------------------ io
    @staticmethod
    def from_dict(d: dict) -> "Project":
        p = Project(
            outputs=[Output.from_dict(x) for x in d.get("outputs", [])],
            channels=[Channel.from_dict(x) for x in d.get("channels", [])],
            pads=[Pad.from_dict(x) for x in d.get("pads", [])],
            bindings=[Binding.from_dict(x) for x in d.get("bindings", [])],
            settings=Settings.from_dict(d.get("settings")),
            layout={k: list(v) for k, v in (d.get("layout") or {}).items()
                    if isinstance(v, (list, tuple)) and len(v) == 2},
            ui=dict(d.get("ui") or {}),
            player=PlayerCfg.from_dict(d.get("player")),
            drum=dict(d.get("drum") or {}),
            editor=dict(d.get("editor") or {}),
        )
        p.repair()
        return p

    def to_dict(self) -> dict:
        return {"version": 1, **asdict(self)}

    def repair(self):
        """Чинит ссылки на удалённые каналы/выходы."""
        if not self.channels:
            self.channels.append(Channel(id=new_id("ch"), name="Звуки", outputs=[o.id for o in self.outputs]))
        ch_ids = {c.id for c in self.channels}
        if self.player.channel not in ch_ids:
            music = next((c for c in self.channels if "муз" in c.name.lower()), self.channels[0])
            self.player.channel = music.id
        out_ids = {o.id for o in self.outputs}
        for c in self.channels:
            c.outputs = list(dict.fromkeys(o for o in c.outputs if o in out_ids))
        # пересылки между выходами: без ссылок в никуда, без петель
        # граф строим отдельно и присваиваем готовые списки целиком — микшер в другом потоке
        # никогда не увидит «полупустой» список
        graph: dict[str, list] = {o.id: [] for o in self.outputs}

        def reaches(a, b):
            seen, stack = set(), [a]
            while stack:
                cur = stack.pop()
                if cur == b:
                    return True
                if cur not in seen:
                    seen.add(cur)
                    stack.extend(graph.get(cur, []))
            return False

        for o in self.outputs:
            if o.recorder:              # блок записи — только приёмник: без устройства и без пересылок дальше
                o.bus = True
                o.switch = ""
        for o in self.outputs:          # первая связь выигрывает, замыкающая петлю отбрасывается
            if o.recorder:
                continue
            for t in dict.fromkeys(o.sends):
                if t in out_ids and t != o.id and not reaches(t, o.id):
                    graph[o.id].append(t)
        for o in self.outputs:
            if o.sends != graph[o.id]:
                o.sends = graph[o.id]
        # переключатели: без своего устройства, входы в стабильном порядке, положение в пределах
        for o in self.outputs:
            if not o.switch:
                continue
            o.bus = True
            if o.switch == "merge":
                ups = [c.id for c in self.channels if o.id in c.outputs]
                ups += [x.id for x in self.outputs if o.id in x.sends]
                ups += [p.id for p in self.pads if p.route_own and o.id in p.outputs]
                order = [u for u in o.sw_inputs if u in ups] + [u for u in ups if u not in o.sw_inputs]
                if order != o.sw_inputs:
                    o.sw_inputs = order
                n = len(o.sw_inputs)
            else:
                n = len(o.sends)
            if o.active >= n:
                o.active = n - 1 if n else 0
            if o.active < -1:
                o.active = -1
        for p in self.pads:
            if p.channel not in ch_ids:
                p.channel = self.channels[0].id
            p.exclude_outputs = [o for o in p.exclude_outputs if o in out_ids]
            p.outputs = list(dict.fromkeys(o for o in p.outputs if o in out_ids))
        if isinstance(self.ui.get("rec_pads"), dict):       # какие звуки записаны какими блоками записи
            pad_ids = {p.id for p in self.pads}
            self.ui["rec_pads"] = {k: v for k, v in self.ui["rec_pads"].items() if k in pad_ids}
        if self.settings.preview_output not in out_ids:
            self.settings.preview_output = self.outputs[0].id if self.outputs else ""

    def copy(self) -> "Project":
        return Project.from_dict(json.loads(json.dumps(self.to_dict())))


def config_path() -> Path:
    return data_dir() / "config.json"


def load_project() -> Project | None:
    path = config_path()
    if not path.exists():
        return None
    try:
        return Project.from_dict(json.loads(path.read_text("utf-8")))
    except Exception:
        # битый конфиг не должен убивать программу — сохраним копию и начнём заново
        shutil.copy(path, path.with_suffix(".broken.json"))
        return None


def save_project(project: Project):
    path = config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)


def import_audio_file(src: str) -> str:
    """Копирует файл в библиотеку программы, чтобы звук не потерялся при переносе исходника."""
    src_p = Path(src)
    lib = data_dir() / "library"
    if lib in src_p.resolve().parents:
        return str(src_p)
    dst = lib / src_p.name
    i = 1
    while dst.exists():
        dst = lib / f"{src_p.stem}_{i}{src_p.suffix}"
        i += 1
    shutil.copy2(src_p, dst)
    return str(dst)


# ---------------------------------------------------------------- helpers

_NOTE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(n: int) -> str:
    if n < 0:
        return "?"
    return f"{_NOTE[n % 12]}{n // 12 - 1} ({n})"


_PRETTY = {
    "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "win": "Win",
    "space": "Пробел", "enter": "Enter", "esc": "Esc", "tab": "Tab",
    "backspace": "Backspace", "delete": "Del", "insert": "Ins",
    "page up": "PgUp", "page down": "PgDn", "home": "Home", "end": "End",
    "up": "↑", "down": "↓", "left": "←", "right": "→",
    "plus": "+", "minus": "−", "star": "*", "slash": "/", "decimal": ".",
}


def pretty_combo(combo: str) -> str:
    parts = []
    for p in combo.split("+") if combo else []:
        if p.startswith("num "):
            rest = p[4:]
            parts.append("Num " + _PRETTY.get(rest, rest.upper() if len(rest) == 1 else rest.capitalize()))
        else:
            parts.append(_PRETTY.get(p, p.upper() if len(p) <= 3 else p.capitalize()))
    return " + ".join(parts)
