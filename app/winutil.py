"""Автозапуск вместе с Windows (ключ реестра HKCU\\...\\Run)."""
from __future__ import annotations

import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Miaukalo"


def autostart_supported() -> bool:
    return sys.platform == "win32"


def _command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --hidden'
    import os
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    main = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    return f'"{pyw}" "{main}" --hidden'


def get_autostart() -> bool:
    if not autostart_supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool):
    if not autostart_supported():
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except OSError:
                pass
