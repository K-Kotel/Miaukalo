"""Глобальные горячие клавиши, MIDI-контроллеры и выполнение действий."""
from __future__ import annotations

import sys
import threading
import time

MOD_ORDER = ["ctrl", "shift", "alt", "win"]

# Скан-коды (набор 1) → стабильные имена, не зависящие от раскладки (ru/en)
_SCAN = {
    1: "esc", 2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
    12: "minus", 13: "=", 14: "backspace", 15: "tab", 16: "q", 17: "w", 18: "e", 19: "r", 20: "t",
    21: "y", 22: "u", 23: "i", 24: "o", 25: "p", 26: "[", 27: "]", 28: "enter", 30: "a", 31: "s",
    32: "d", 33: "f", 34: "g", 35: "h", 36: "j", 37: "k", 38: "l", 39: ";", 40: "'", 41: "`",
    43: "\\", 44: "z", 45: "x", 46: "c", 47: "v", 48: "b", 49: "n", 50: "m", 51: ",", 52: ".",
    53: "slash", 57: "space", 58: "caps lock", 59: "f1", 60: "f2", 61: "f3", 62: "f4", 63: "f5",
    64: "f6", 65: "f7", 66: "f8", 67: "f9", 68: "f10", 69: "num lock", 70: "scroll lock",
    71: "home", 72: "up", 73: "page up", 75: "left", 77: "right", 79: "end", 80: "down",
    81: "page down", 82: "insert", 83: "delete", 87: "f11", 88: "f12",
    100: "f13", 101: "f14", 102: "f15", 103: "f16", 104: "f17", 105: "f18", 106: "f19",
    107: "f20", 108: "f21", 109: "f22", 110: "f23", 118: "f24",
}
_KEYPAD = {
    71: "7", 72: "8", 73: "9", 74: "minus", 75: "4", 76: "5", 77: "6", 78: "plus", 79: "1",
    80: "2", 81: "3", 82: "0", 83: "decimal", 28: "enter", 53: "slash", 55: "star",
}


def modifier_of(name: str):
    n = (name or "").lower()
    if "ctrl" in n or "control" in n:
        return "ctrl"
    if "shift" in n:
        return "shift"
    if n in ("alt", "alt gr", "left alt", "right alt", "altgr") or n.endswith(" alt"):
        return "alt"
    if "windows" in n or n in ("win", "cmd", "command", "left win", "right win"):
        return "win"
    return None


def key_id(name: str, scan_code: int | None, is_keypad: bool | None) -> str:
    name = (name or "").lower()
    mod = modifier_of(name)
    if mod:
        return mod
    if is_keypad and scan_code in _KEYPAD:
        return "num " + _KEYPAD[scan_code]
    if sys.platform == "win32" and scan_code in _SCAN:
        return _SCAN[scan_code]
    if name == "+":
        return "plus"
    if name == "-":
        return "minus"
    return name or f"sc{scan_code}"


def make_combo(mods, key: str) -> str:
    parts = [m for m in MOD_ORDER if m in mods and m != key]
    if key not in MOD_ORDER or key not in parts:
        parts.append(key)
    return "+".join(parts)


class InputRouter:
    def __init__(self, engine):
        self.engine = engine
        self.project = None
        self.index: dict[str, list] = {}
        self.enabled = True
        self.held_mods: set[str] = set()
        self.down: set[str] = set()
        self.active: dict[str, list] = {}      # клавиша → привязки, сработавшие на её нажатие
        self.cc_state: dict[str, bool] = {}
        self.capture_cb = None
        self._cap_mods: set[str] = set()
        self._cap_key = False
        self.keyboard_error = ""
        self.midi_error = ""
        self.midi_ports: dict[str, object] = {}
        self.midi_enabled = True
        self.on_midi_ports = None
        self.on_activity = None                 # колбэк(текст) — для индикатора «что нажато»
        self._lock = threading.RLock()
        self._running = False
        self._drum_down: dict = {}              # клавиша → пэд дрампада (чтобы отпускание ушло туда же)

    # ------------------------------------------------------------ config
    def set_project(self, project):
        idx: dict[str, list] = {}
        for b in project.bindings:
            t = b.trigger
            if t.type == "key" and not t.combo:
                continue
            if t.type != "key" and t.number < 0:
                continue
            idx.setdefault(t.key(), []).append(b)
        with self._lock:
            self.project = project
            self.index = idx
            self.enabled = project.settings.hotkeys_enabled
            self.midi_enabled = getattr(project.settings, "midi_enabled", True)

    # ------------------------------------------------------------ capture
    def start_capture(self, cb):
        """cb(Trigger) — будет вызван с первой нажатой комбинацией или MIDI-сообщением."""
        with self._lock:
            self.capture_cb = cb
            self._cap_mods = set()
            self._cap_key = False

    def cancel_capture(self):
        with self._lock:
            self.capture_cb = None

    def _deliver_capture(self, trigger):
        cb = self.capture_cb
        self.capture_cb = None
        if cb:
            try:
                cb(trigger)
            except Exception:
                pass

    # ------------------------------------------------------------ keyboard
    def handle_key(self, kid: str, is_down: bool):
        from app.model import Trigger
        mod = kid if kid in MOD_ORDER else None
        with self._lock:
            if is_down:
                repeat = kid in self.down
                self.down.add(kid)
                if mod:
                    self.held_mods.add(mod)
                if self.capture_cb:
                    if mod:
                        self._cap_mods.add(mod)
                    elif not repeat:
                        combo = make_combo(self.held_mods, kid)
                        self._cap_key = True
                        self._deliver_capture(Trigger(type="key", combo=combo))
                    return
                if repeat:
                    if kid in self._drum_down:
                        return
                    for b in self.active.get(kid, []):
                        self._run(b, "repeat")
                    return
                if self._drum_key(kid):
                    return
                combo = make_combo(self.held_mods, kid)
                binds = self.index.get("key:" + combo, []) if self.enabled else []
                self.active[kid] = list(binds)
                if self.on_activity:
                    self.on_activity(combo)
                for b in binds:
                    self._run(b, "press")
            else:
                self.down.discard(kid)
                if mod:
                    self.held_mods.discard(mod)
                pad = self._drum_down.pop(kid, None)
                if pad is not None:
                    self.engine.drums.release(pad)
                    return
                if self.capture_cb:
                    if mod and self._cap_mods and not self._cap_key and not self.held_mods:
                        combo = "+".join(m for m in MOD_ORDER if m in self._cap_mods)
                        self._deliver_capture(Trigger(type="key", combo=combo))
                    return
                for b in self.active.pop(kid, []):
                    self._run(b, "release")

    def _drum_key(self, kid) -> bool:
        """Клавиши дрампада: 16 пэдов (+ Shift — акцент) и транспорт, когда открыта вкладка."""
        dr = getattr(self.engine, "drums", None)
        if dr is None or not self.enabled or not dr.keys_active():
            return False
        mods = self.held_mods - {"shift"}
        if mods:
            return False                        # Ctrl/Alt/Win + … — это обычные горячие клавиши
        keys = dr.cfg.get("keys") or []
        if kid in keys:
            pad = keys.index(kid)
            vel = 127 if "shift" in self.held_mods else int(dr.cfg.get("kb_vel", 100))
            dr.hit(pad, vel)
            self._drum_down[kid] = pad
            if self.on_activity:
                self.on_activity(f"ПЭД {pad + 1}")
            return True
        if dr.ui_focus and not self.held_mods:
            if kid == "space":
                dr.play_stop()
                return True
            if kid == "`":
                dr.toggle_rec()
                return True
            if kid == "backspace":
                dr.undo()
                return True
        return False

    def start_keyboard(self):
        try:
            import keyboard
        except Exception as e:  # noqa: BLE001
            self.keyboard_error = f"модуль keyboard не загрузился: {e}"
            return False

        def on_event(ev):
            try:
                kid = key_id(ev.name, ev.scan_code, getattr(ev, "is_keypad", False))
                self.handle_key(kid, ev.event_type == "down")
            except Exception:
                pass

        try:
            keyboard.hook(on_event)
            self.keyboard_error = ""
            return True
        except Exception as e:  # noqa: BLE001
            self.keyboard_error = str(e) or "нет доступа к клавиатуре"
            return False

    def release_all(self):
        """Сбросить зажатые клавиши (например, после потери фокуса Windows-блокировкой)."""
        with self._lock:
            for kid in list(self.down):
                self.handle_key(kid, False)

    # ------------------------------------------------------------ MIDI
    def handle_midi(self, port: str, msg):
        from app.model import Trigger
        t = msg.type
        if t == "note_on" and msg.velocity > 0:
            key, kind, value = f"midi_note:{msg.note}", "press", msg.velocity / 127
            trig = Trigger(type="midi_note", number=msg.note)
        elif t == "note_off" or (t == "note_on" and msg.velocity == 0):
            key, kind, value = f"midi_note:{msg.note}", "release", 0.0
            trig = None
        elif t == "control_change":
            key, kind, value = f"midi_cc:{msg.control}", "cc", msg.value / 127
            trig = Trigger(type="midi_cc", number=msg.control)
        elif t == "program_change":
            key, kind, value = f"midi_note:{1000 + msg.program}", "tap", 1.0
            trig = Trigger(type="midi_note", number=1000 + msg.program)
        else:
            return
        ch = getattr(msg, "channel", 0)
        with self._lock:
            if self.capture_cb:
                if trig is not None:
                    self._deliver_capture(trig)
                return
            if not self.enabled:
                return
            dr = getattr(self.engine, "drums", None)
            if dr is not None and dr.cfg.get("midi_on", True) and dr.keys_active() and t in ("note_on", "note_off"):
                pad = msg.note - int(dr.cfg.get("midi_base", 36))
                if 0 <= pad < 16:                # MIDI-пэды → пэды дрампада, с силой удара
                    if kind == "press":
                        dr.hit(pad, msg.velocity)
                    else:
                        dr.release(pad)
                    return
            if self.on_activity:
                self.on_activity(key.replace("midi_note:", "MIDI нота ").replace("midi_cc:", "MIDI CC "))
            for b in self.index.get(key, []):
                bt = b.trigger
                if bt.channel >= 0 and bt.channel != ch:
                    continue
                if bt.port and bt.port != port:
                    continue
                if kind == "cc":
                    if b.action.get("type") == "volume_set":
                        self._run(b, "value", value)
                        continue
                    state = value >= 0.5
                    prev = self.cc_state.get(b.id, False)
                    if state != prev:
                        self.cc_state[b.id] = state
                        self._run(b, "press" if state else "release")
                elif kind == "tap":
                    self._run(b, "press")
                    self._run(b, "release")
                else:
                    self._run(b, kind, value)

    def start_midi(self):
        try:
            import mido
            mido.get_input_names()
        except Exception as e:  # noqa: BLE001
            self.midi_error = f"MIDI недоступен: {e}"
            return False
        self._running = True
        threading.Thread(target=self._midi_watch, daemon=True, name="midi-watch").start()
        return True

    def _midi_watch(self):
        import mido
        last = None
        while self._running:
            try:
                names = mido.get_input_names() if self.midi_enabled else []
            except Exception:
                names = []
            for n in list(self.midi_ports):
                if n not in names:
                    try:
                        self.midi_ports.pop(n).close()
                    except Exception:
                        pass
            for n in names:
                if n not in self.midi_ports:
                    try:
                        self.midi_ports[n] = mido.open_input(n, callback=lambda m, n=n: self.handle_midi(n, m))
                    except Exception as e:  # noqa: BLE001
                        self.midi_error = f"{n}: {e}"
            cur = sorted(self.midi_ports)
            if cur != last:
                last = cur
                if self.on_midi_ports:
                    try:
                        self.on_midi_ports(cur)
                    except Exception:
                        pass
            time.sleep(2)

    def stop(self):
        self._running = False
        for p in list(self.midi_ports.values()):
            try:
                p.close()
            except Exception:
                pass
        self.midi_ports.clear()
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass

    # ------------------------------------------------------------ actions
    def _run(self, binding, kind, value=None):
        try:
            execute(self.engine, binding.action, kind, value)
        except Exception:
            pass


def execute(engine, action: dict, kind: str, value=None):
    """kind: press | release | repeat | value."""
    t = action.get("type")
    if t == "pad":
        if kind in ("press", "release", "repeat"):
            engine.pad_event(action.get("pad", ""), kind)
    elif t == "stop_pad":
        if kind == "press":
            engine.stop_pad(action.get("pad", ""))
    elif t == "stop_channel":
        if kind == "press":
            engine.stop_channel(action.get("channel", ""))
    elif t == "stop_all":
        if kind == "press":
            engine.stop_all()
    elif t == "volume_step":
        if kind in ("press", "repeat"):
            engine.step_volume(action.get("channel", ""), float(action.get("step", 0.05)))
    elif t == "volume_set":
        if kind == "value" and value is not None:
            engine.set_volume(action.get("channel", ""), value)
        elif kind == "press":
            engine.set_volume(action.get("channel", ""), float(action.get("value", 0.8)))
    elif t == "mute":
        ch = engine.project.channel(action.get("channel", "")) if engine.project else None
        if ch is None:
            return
        mode = action.get("mode", "toggle")
        if mode == "toggle":
            if kind == "press":
                engine.set_muted(ch.id, not ch.muted)
        elif mode == "hold":       # тишина, пока держишь
            if kind == "press":
                engine.set_muted(ch.id, True)
            elif kind == "release":
                engine.set_muted(ch.id, False)
        elif mode == "ptt":        # звук, только пока держишь (push-to-talk)
            if kind == "press":
                engine.set_muted(ch.id, False)
            elif kind == "release":
                engine.set_muted(ch.id, True)
    elif t == "mute_out":
        o = engine.project.output(action.get("output", "")) if engine.project else None
        if o is None:
            return
        mode = action.get("mode", "toggle")
        if mode == "toggle":
            if kind == "press":
                engine.set_out_muted(o.id, not o.muted)
        elif mode == "hold":
            if kind in ("press", "release"):
                engine.set_out_muted(o.id, kind == "press")
        elif mode == "ptt":
            if kind in ("press", "release"):
                engine.set_out_muted(o.id, kind == "release")
    elif t == "reverse":
        scope = action.get("scope", "all")
        target = action.get("channel" if scope == "channel" else "pad", "") if scope != "all" else ""
        if action.get("mode", "hold") == "toggle":
            if kind == "press":
                engine.reverse(scope, target, not engine.reverse_active(scope, target))
        elif kind in ("press", "release"):
            engine.reverse(scope, target, kind == "press")
    elif t == "pad_random":
        if kind == "press":
            engine.play_random_fx(action.get("pad", ""), int(action.get("count", 3)),
                                  float(action.get("strength", 0.7)), action.get("pool", "all"))
    elif t == "drum":
        if kind != "press":
            return
        dr = engine.drums
        op = action.get("op", "play_stop")
        if op == "play_stop":
            dr.play_stop()
        elif op == "rec":
            dr.toggle_rec()
        elif op == "undo":
            dr.undo()
        elif op == "clear":
            dr.clear()
        elif op == "metro":
            dr.cfg["metro"] = "off" if dr.cfg.get("metro") != "off" else "rec"
            dr._notify()
        elif op == "keys":
            dr.cfg["keys_everywhere"] = not dr.cfg.get("keys_everywhere")
            dr._notify()
    elif t == "quick_mixer":
        if kind == "press" and getattr(engine, "on_ui_action", None):
            engine.on_ui_action("quick_mixer")
    elif t == "switch":
        sid = action.get("switch", "")
        op = action.get("op", "next")
        if op == "hold":                          # пока держишь — положение N, отпустил — обратно
            if kind == "press":
                sw = engine.project.output(sid) if engine.project else None
                if sw:
                    engine._switch_back = getattr(engine, "_switch_back", {})
                    engine._switch_back[sid] = sw.active
                    engine.switch_set(sid, int(action.get("index", 0)))
            elif kind == "release":
                back = getattr(engine, "_switch_back", {}).pop(sid, None)
                if back is not None:
                    engine.switch_set(sid, back)
            return
        if kind != "press":
            return
        if op == "next":
            engine.switch_step(sid, 1)
        elif op == "prev":
            engine.switch_step(sid, -1)
        elif op == "set":
            engine.switch_set(sid, int(action.get("index", 0)))
        elif op == "off":
            engine.switch_set(sid, -1)
        elif op == "toggle":                      # положение N ↔ выкл
            sw = engine.project.output(sid) if engine.project else None
            idx = int(action.get("index", 0))
            if sw:
                engine.switch_set(sid, -1 if sw.active == idx else idx)
    elif t == "record":
        rid = action.get("rec", "")
        op = action.get("op", "toggle")
        if op == "hold":                          # пишет, пока держишь
            if kind == "press":
                engine.rec_command(rid, "start")
            elif kind == "release":
                engine.rec_command(rid, "stop")
        elif kind == "press":
            engine.rec_command(rid, op)
    elif t == "player":
        op = action.get("op", "play_pause")
        if kind == "press" or (kind == "repeat" and op in ("vol_up", "vol_down", "seek_fwd", "seek_back")):
            engine.player.command(op)
    elif t == "effect":
        ch = engine.fx_owner(action.get("channel", "")) if engine.project else None
        if ch is None:
            return
        slot = next((s for s in ch.effects if s.id == action.get("effect")), None)
        if slot is None:
            return
        if action.get("mode", "toggle") == "hold":
            if kind == "press":
                engine.set_effect(ch.id, slot.id, True)
            elif kind == "release":
                engine.set_effect(ch.id, slot.id, False)
        elif kind == "press":
            engine.set_effect(ch.id, slot.id, not slot.enabled)


SWITCH_OPS = {"next": "Следующее положение", "prev": "Предыдущее положение", "set": "Выбрать положение",
              "toggle": "Положение ↔ выключено", "hold": "Положение, пока держишь", "off": "Выключить (никуда)"}
MUTE_MODES = {"toggle": "Вкл/выкл по нажатию", "hold": "Тишина, пока держишь", "ptt": "Звук, только пока держишь (рация)"}
REC_OPS = {"hold": "Записывать, пока держишь", "toggle": "Начать / закончить по нажатию",
           "start": "Только начать", "stop": "Только закончить", "save": "Сохранить последние секунды (повтор)"}
DRUM_OPS = {"play_stop": "Пуск / стоп петли", "rec": "Запись вкл/выкл", "undo": "Отменить последний дубль",
            "clear": "Очистить петлю", "metro": "Метроном вкл/выкл", "keys": "Клавиши дрампада везде вкл/выкл"}
REVERSE_SCOPES = {"all": "Все играющие звуки", "channel": "Звуки канала", "pad": "Один звук"}
REVERSE_MODES = {"hold": "Задом наперёд, пока держишь", "toggle": "Вкл/выкл по нажатию"}
EFFECT_MODES = {"toggle": "Вкл/выкл по нажатию", "hold": "Работает, пока держишь"}
