"""Расширение браузера «Мяукало — звук вкладки»: постоянная копия в %APPDATA%, чтобы браузер не терял его,
если папку с программой переместят."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.model import data_dir, resource_path


def bundled_dir() -> Path:
    return resource_path("browser_extension")


def installed_dir() -> Path:
    return data_dir() / "browser_extension"


def _version(d: Path) -> str:
    try:
        return json.loads((d / "manifest.json").read_text("utf-8")).get("version", "")
    except Exception:
        return ""


def install_extension_copy() -> Path:
    src, dst = bundled_dir(), installed_dir()
    if src.exists() and (_version(src) != _version(dst) or not (dst / "manifest.json").exists()):
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
    return dst
