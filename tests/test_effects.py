"""Все эффекты дают звук, не ломаются на блоках по 10 мс, а голосовые — с малой задержкой."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.audio.effects import CATALOG, VOICE, EffectChain, default_params  # noqa: E402
from app.model import EffectSlot  # noqa: E402

SR = 48000
noise = np.random.default_rng(1).normal(0, 0.15, (SR, 2)).astype(np.float32)
click = np.zeros((SR // 2, 2), np.float32)
click[0:240] = 0.5
for t, (title, cat, _p) in CATALOG.items():
    ch = EffectChain()
    ch.sync([EffectSlot("x", t, True, default_params(t))])
    out = np.concatenate([ch.process(noise[i:i + 480].copy()) for i in range(0, SR, 480)])
    assert out.shape == noise.shape and np.isfinite(out).all(), t
    rms = float(np.sqrt((out[9600:] ** 2).mean()))
    assert rms > 0.02, f"{t} ({title}) почти не даёт звука: {rms}"
    if cat == VOICE:
        ch = EffectChain()
        ch.sync([EffectSlot("x", t, True, default_params(t))])
        o = np.concatenate([ch.process(click[i:i + 480].copy()) for i in range(0, len(click), 480)])
        first = int(np.argmax(np.abs(o).max(axis=1) > 0.01))
        assert first / SR < 0.05, f"{t} ({title}): задержка {first / SR * 1000:.0f} мс — слишком много для живого голоса"
print(f"effects ok: {len(CATALOG)}")
