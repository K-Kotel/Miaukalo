"""Человекочитаемые описания действий."""
from app.audio.effects import effect_title


def describe_action(project, a: dict) -> str:
    t = a.get("type")
    ch = project.channel(a.get("channel", "")) or project.output(a.get("channel", ""))
    chn = f"«{ch.name}»" if ch else "(канал удалён)"
    pad = project.pad(a.get("pad", ""))
    padn = f"«{pad.name}»" if pad else "(звук удалён)"
    if t == "pad":
        return f"Звук {padn}"
    if t == "stop_pad":
        return f"Остановить звук {padn}"
    if t == "stop_channel":
        return f"Остановить канал {chn}"
    if t == "stop_all":
        return "Остановить всё"
    if t == "volume_step":
        step = float(a.get("step", 0.05))
        return f"Канал {chn}: громкость {'+' if step > 0 else '−'}{abs(step) * 100:.0f}%"
    if t == "volume_set":
        return f"Канал {chn}: громкость крутилкой / на {float(a.get('value', 0.8)) * 100:.0f}%"
    if t == "mute":
        m = {"toggle": "вкл/выкл звук", "hold": "тишина, пока держишь", "ptt": "звук, пока держишь"}
        return f"Канал {chn}: {m.get(a.get('mode', 'toggle'), '')}"
    if t == "switch":
        from app.input.router import SWITCH_OPS
        sw = project.output(a.get("switch", ""))
        swn = f"«{sw.name}»" if sw else "(удалён)"
        op = a.get("op", "next")
        s = SWITCH_OPS.get(op, op)
        if op in ("set", "toggle", "hold") and sw:
            ports = project.switch_ports(sw)
            i = int(a.get("index", 0))
            pn = project.node_name(ports[i]) if 0 <= i < len(ports) else "?"
            s = {"set": f"положение {i + 1} ({pn})", "toggle": f"положение {i + 1} ({pn}) ↔ выкл",
                 "hold": f"положение {i + 1} ({pn}), пока держишь"}[op]
        return f"Переключатель {swn}: {s}"
    if t == "mute_out":
        o = project.output(a.get("output", ""))
        on = f"«{o.name}»" if o else "(выход удалён)"
        m = {"toggle": "вкл/выкл звук", "hold": "тишина, пока держишь", "ptt": "звук, пока держишь"}
        return f"Выход {on}: {m.get(a.get('mode', 'toggle'), '')}"
    if t == "reverse":
        sc = a.get("scope", "all")
        what = "все играющие звуки" if sc == "all" else (f"канал {chn}" if sc == "channel" else f"звук {padn}")
        return f"Задом наперёд: {what}" + (" (вкл/выкл)" if a.get("mode") == "toggle" else ", пока держишь")
    if t == "drum":
        from app.input.router import DRUM_OPS
        return "Дрампад: " + DRUM_OPS.get(a.get("op", "play_stop"), "?").lower()
    if t == "pad_random":
        return f"Звук {padn} + {int(a.get('count', 3))} случ. эффекта"
    if t == "record":
        from app.input.router import REC_OPS
        r = project.output(a.get("rec", ""))
        rn = f"«{r.name}»" if r else "(блок удалён)"
        return f"Запись {rn}: " + REC_OPS.get(a.get("op", "toggle"), "?").lower()
    if t == "player":
        from app.audio.player import OPS
        return "Плеер: " + OPS.get(a.get("op", "play_pause"), "?").lower()
    if t == "quick_mixer":
        return "Быстрый микшер (открыть / закрыть)"
    if t == "effect":
        slot = next((s for s in ch.effects if s.id == a.get("effect")), None) if ch else None
        name = effect_title(slot.type) if slot else "(эффект удалён)"
        m = "пока держишь" if a.get("mode") == "hold" else "вкл/выкл"
        return f"Канал {chn}: {name} — {m}"
    return t or "?"
