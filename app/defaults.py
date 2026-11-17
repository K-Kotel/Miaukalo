"""Проект по умолчанию при первом запуске."""
from __future__ import annotations

import shutil

from app.audio import devices
from app.audio.effects import default_params
from app.audio.sources import BROWSERS, running_apps
from app.model import (Binding, Channel, EffectSlot, LiveSource, Output, Pad, Project, Trigger, data_dir, new_id,
                       resource_path)

STARTER = [("Та-дам!", "#ffd36b"), ("Ба-дум-тсс", "#ff6a2a"), ("Барабанная дробь", "#d0843a"), ("Горн", "#ff3b30")]


def default_project() -> Project:
    p = Project()
    spk = devices.default_device("output")
    out_me = Output(new_id("out"), "Мои наушники", spk["name"] if spk else "", spk["hostapi"] if spk else "")
    cables = devices.list_virtual_cables()
    cable = cables[0] if cables else None
    out_discord = Output(new_id("out"), f"Дискорд ({cable['label']})" if cable else "Дискорд (VB-Cable)",
                         cable["name"] if cable else "CABLE Input (VB-Audio Virtual Cable)",
                         cable["hostapi"] if cable else devices.PREFERRED_HOSTAPI,
                         enabled=cable is not None)
    p.outputs = [out_me, out_discord]
    both = [out_me.id, out_discord.id]

    music = Channel(new_id("ch"), "Музыка", volume=0.7, outputs=both, transition="crossfade",
                    transition_ms=2000, color="#ffd36b")
    sfx = Channel(new_id("ch"), "Эффекты", volume=0.85, outputs=both, transition="overlay", color="#ff6a2a")
    mic_fx = [EffectSlot(new_id("fx"), t, False, default_params(t)) for t in ("delay", "pitch", "radio", "reverb")]
    mic_fx.insert(0, EffectSlot(new_id("fx"), "noisegate", False, default_params("noisegate")))
    mic = Channel(new_id("ch"), "Микрофон", volume=0.9, outputs=[out_discord.id], transition="overlay",
                  effects=mic_fx, color="#b9e35a")
    p.channels = [music, sfx, mic]

    # стартовые звуки
    lib = data_dir() / "library"
    for i, (name, color) in enumerate(STARTER):
        src = resource_path(f"assets/sounds/{name}.wav")
        if not src.exists():
            continue
        dst = lib / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
        pad = Pad(new_id("pad"), name, file=str(dst), channel=sfx.id, color=color, retrigger="restart")
        p.pads.append(pad)
        p.bindings.append(Binding(new_id("bind"), Trigger("key", f"num {i + 1}"), {"type": "pad", "pad": pad.id}))

    apps = running_apps()
    browser = next((b for b in BROWSERS[:7] if b in apps), "chrome.exe")
    mic_pad = Pad(new_id("pad"), "Микрофон", kind="live", channel=mic.id, mode="toggle", color="#b9e35a",
                  source=LiveSource(type="input"), autostart=cable is not None, fade_out_ms=150)
    yt = Pad(new_id("pad"), "Браузер (YouTube)", kind="live", channel=music.id, mode="toggle", color="#ff9a1f",
             source=LiveSource(type="process", process=browser), exclude_outputs=[out_me.id], fade_out_ms=500)
    p.pads += [mic_pad, yt]

    echo = mic_fx[1]
    pitch = mic_fx[2]
    p.bindings += [
        Binding(new_id("bind"), Trigger("key", "ctrl+num 0"), {"type": "stop_all"}),
        Binding(new_id("bind"), Trigger("key", "ctrl+num plus"), {"type": "volume_step", "channel": music.id, "step": 0.05}),
        Binding(new_id("bind"), Trigger("key", "ctrl+num minus"), {"type": "volume_step", "channel": music.id, "step": -0.05}),
        Binding(new_id("bind"), Trigger("key", "ctrl+num 7"), {"type": "effect", "channel": mic.id, "effect": echo.id, "mode": "toggle"}),
        Binding(new_id("bind"), Trigger("key", "ctrl+num 8"), {"type": "effect", "channel": mic.id, "effect": pitch.id, "mode": "hold"}),
        Binding(new_id("bind"), Trigger("key", "ctrl+num 9"), {"type": "mute", "channel": mic.id, "mode": "toggle"}),
        Binding(new_id("bind"), Trigger("key", "ctrl+num 5"), {"type": "pad", "pad": yt.id}),
    ]
    # остальные кабели VB-Audio (платные A+B, C+D, Voicemeeter) — заводим как выходы, пока без подключений.
    # Кабели других производителей добавляются по кнопке «НАЙТИ КАБЕЛИ» — их бывает много (Wave Link, Sonar…)
    add_missing_cables(p, vendors=("VB-Audio",))
    p.bindings.append(Binding(new_id("bind"), Trigger("key", "ctrl+num star"), {"type": "quick_mixer"}))
    p.bindings.append(Binding(new_id("bind"), Trigger("key", "ctrl+num slash"), {"type": "player", "op": "play_pause"}))
    p.bindings.append(Binding(new_id("bind"), Trigger("key", "ctrl+num decimal"), {"type": "player", "op": "next"}))
    p.player.channel = music.id
    p.settings.preview_output = out_me.id
    p.repair()
    return p


def missing_cables(project: Project, vendors=None) -> list:
    used = {o.device for o in project.outputs if not o.bus}
    return [c for c in devices.list_virtual_cables(vendors) if c["name"] not in used]


def add_missing_cables(project: Project, vendors=None, names=None) -> list:
    """Создаёт выходы для найденных виртуальных кабелей, которых ещё нет в проекте.
    names — добавить только эти устройства (выбор в окне «Найти кабели»)."""
    added = []
    for c in missing_cables(project, vendors):
        if names is not None and c["name"] not in names:
            continue
        o = Output(new_id("out"), c["label"], c["name"], c["hostapi"])
        project.outputs.append(o)
        added.append(o)
    return added
