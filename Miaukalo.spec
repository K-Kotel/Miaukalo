# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller --noconfirm Miaukalo.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [("assets", "assets"), ("browser_extension", "browser_extension")]
datas += collect_data_files("soundcard")
datas += collect_data_files("_sounddevice_data")
datas += collect_data_files("_soundfile_data")

hiddenimports = ["mido.backends.rtmidi", "rtmidi", "psutil", "soundcard", "winreg"]
hiddenimports += collect_submodules("pedalboard")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "PIL", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Miaukalo",
    icon="assets/icon.ico",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Miaukalo")
