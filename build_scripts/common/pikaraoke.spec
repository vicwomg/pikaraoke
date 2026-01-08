# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for PiKaraoke (OneDir Build).

Location: /build_scripts/common/pikaraoke.spec

This spec file is platform-agnostic and used for building PiKaraoke on
Windows, macOS, and Linux.
"""

import platform
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# --- 1. PROJECT ROOT DETECTION ---
# The spec file is in build_scripts/common/, so project root is 2 levels up
spec_dir = Path(SPECPATH).resolve()
project_root = spec_dir.parent.parent
pikaraoke_dir = project_root / "pikaraoke"

print(f"Project Root: {project_root}")
print(f"PiKaraoke Dir: {pikaraoke_dir}")

# --- 2. DATA COLLECTION ---
babel_datas = collect_data_files("flask_babel")
ytdlp_datas = collect_data_files("yt_dlp")
flasgger_datas = collect_data_files("flasgger")

datas = [
    (str(pikaraoke_dir / "templates"), "pikaraoke/templates"),
    (str(pikaraoke_dir / "static"), "pikaraoke/static"),
    (str(pikaraoke_dir / "translations"), "pikaraoke/translations"),
    (str(pikaraoke_dir / "logo.png"), "pikaraoke"),
    (str(pikaraoke_dir / "babel.cfg"), "pikaraoke"),
    (str(pikaraoke_dir / "messages.pot"), "pikaraoke"),
]

datas.extend(babel_datas)
datas.extend(ytdlp_datas)
datas.extend(flasgger_datas)

# --- 3. HIDDEN IMPORTS ---
ytdlp_hidden_imports = collect_submodules("yt_dlp.extractor")

hiddenimports = [
    # Core Flask & Dependencies
    "flask",
    "flask_babel",
    "flask_socketio",
    "flask_paginate",
    "flasgger",
    "jinja2.ext",
    "gevent",
    "gevent.pywsgi",
    "gevent.monkey",
    "gevent._socket3",
    "gevent._socketcommon",
    "engineio.async_drivers.gevent",
    "dns.resolver",
    "dns.reversename",
    "yt_dlp",
    "selenium",
    "cherrypy",
    "psutil",
    "requests",
    "qrcode",
    "ffmpeg",
    "babel",
    "socketio",
    "engineio",
    "configparser",
    "pkg_resources",
]
hiddenimports.extend(ytdlp_hidden_imports)

# --- 4. BUILD CONFIGURATION ---
binaries = []
excludes = ["tkinter", "matplotlib", "numpy", "pandas", "PyQt5", "scipy", "test", "unittest"]

# Determine Icon based on OS
icon_path = None
if platform.system() == "Windows":
    icon_path = str(pikaraoke_dir / "static/icons/logo.ico")
elif platform.system() == "Darwin":
    icon_path = str(pikaraoke_dir / "static/icons/logo.icns")

a = Analysis(
    [str(pikaraoke_dir / "app.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# --- 5. DIRECTORY BUILD (OneDir) ---
# Create the executable launcher
exe = EXE(
    pyz,
    a.scripts,
    [],
    [],
    [],
    exclude_binaries=True,
    name="pikaraoke",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)

# Collect everything into a directory
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="pikaraoke",
)
