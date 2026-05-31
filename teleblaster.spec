# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec untuk membundel Telegram Blaster By VibeTool.Club menjadi
distribusi portable (one-folder). Hasilnya akan dipindah ke folder Distribusi/
oleh build_distribusi.bat / build_distribusi.sh.

Build:
    pyinstaller teleblaster.spec --noconfirm
"""

import os
import sys
from pathlib import Path

# SPECPATH = direktori tempat .spec berada (PyInstaller akan inject otomatis).
ROOT = Path(SPECPATH).resolve()  # noqa: F821

block_cipher = None

# ---------------------------------------------------------------------------
# Data files yang harus ikut di-bundle agar app bisa jalan dari folder dist.
# ---------------------------------------------------------------------------
datas = [
    (str(ROOT / "assets" / "vibetool_logo.png"), "assets"),
    (str(ROOT / "assets" / "vibetool_logo.ico"), "assets"),
]

# Embed .env (berisi API_ID & API_HASH) bila ada — supaya client tidak perlu
# konfigurasi apa pun. .env akan di-extract di samping .exe saat run.
env_file = ROOT / ".env"
if env_file.exists():
    datas.append((str(env_file), "."))

# ---------------------------------------------------------------------------
# Hidden imports yang sering tidak ke-detect oleh PyInstaller analyzer.
# ---------------------------------------------------------------------------
hiddenimports = [
    "pyrogram_compat",
    "tgcrypto",
    "PIL._tkinter_finder",
    "pyrogram.crypto.aes",
    "pyrogram.crypto.rsa",
    "pyrogram.crypto.prime",
    "pyrogram.raw.all",
    "pyrogram.raw.types",
    "pyrogram.raw.functions",
    "cryptography.hazmat.backends.openssl",
]

a = Analysis(
    ["gui_app.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim bundle size — modules besar yang tidak dipakai.
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "IPython",
        "notebook",
        "jupyter",
        "test",
        "tests",
        "unittest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

icon_path = ROOT / "assets" / "vibetool_logo.ico"
exe_kwargs = dict(
    name="TelegramBlaster",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # disable UPX, often flagged by AV
    console=False,  # no terminal popup; pure GUI
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
if icon_path.exists():
    exe_kwargs["icon"] = str(icon_path)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    **exe_kwargs,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TelegramBlaster",
)
