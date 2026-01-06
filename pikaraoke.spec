# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for PiKaraoke Windows installer
This file defines how to bundle PiKaraoke into a standalone Windows executable
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Get the project root directory
project_root = Path(SPECPATH)
pikaraoke_dir = project_root / 'pikaraoke'

# Collect all Flask-Babel translation files
babel_datas = collect_data_files('flask_babel')

# Collect yt-dlp data files (extractors, etc.)
ytdlp_datas = collect_data_files('yt_dlp')

# Define data files to include
datas = [
    # 1. Flask templates -> Destination: 'pikaraoke/templates'
    (str(pikaraoke_dir / 'templates'), 'pikaraoke/templates'),

    # 2. Static assets -> Destination: 'pikaraoke/static'
    (str(pikaraoke_dir / 'static'), 'pikaraoke/static'),

    # 3. Translation files -> Destination: 'pikaraoke/translations'
    (str(pikaraoke_dir / 'translations'), 'pikaraoke/translations'),

    # 4. Other root-level files (These stay in 'pikaraoke' root)
    (str(pikaraoke_dir / 'logo.png'), 'pikaraoke'),
    (str(pikaraoke_dir / 'babel.cfg'), 'pikaraoke'),
    (str(pikaraoke_dir / 'messages.pot'), 'pikaraoke'),
]

# Add Flask-Babel data files
datas.extend(babel_datas)

# Add yt-dlp data files
datas.extend(ytdlp_datas)

# Collect all yt-dlp extractor modules (dynamically loaded)
ytdlp_hidden_imports = collect_submodules('yt_dlp.extractor')

# Define hidden imports (modules imported dynamically that PyInstaller can't detect)
hiddenimports = [
    # Flask and extensions
    'flask',
    'flask_babel',
    'flask_socketio',
    'flask_paginate',
    'flasgger',
    'jinja2.ext',

    # Gevent and async support
    'gevent',
    'gevent.pywsgi',
    'gevent.monkey',
    'gevent._socket3',
    'gevent._socketcommon',
    'dns.resolver',
    'dns.reversename',

    # yt-dlp and extractors
    'yt_dlp',
    'yt_dlp.extractor',
    'yt_dlp.postprocessor',
    'yt_dlp.downloader',

    # Selenium
    'selenium',
    'selenium.webdriver',
    'selenium.webdriver.chrome',
    'selenium.webdriver.chrome.service',
    'selenium.webdriver.chrome.options',
    'selenium.webdriver.common',
    'selenium.webdriver.support',

    # CherryPy
    'cherrypy',
    'cherrypy.wsgiserver',

    # Other dependencies
    'psutil',
    'requests',
    'qrcode',
    'ffmpeg',
    'babel',
    'babel.messages',
    'babel.messages.mofile',

    # Socket.IO support
    'socketio',
    'engineio',
    'engineio.async_drivers.gevent',

    # Standard library modules that might be needed
    'configparser',
    'pkg_resources',
    'pkg_resources.py2_warn',
]

# Add all yt-dlp extractors
hiddenimports.extend(ytdlp_hidden_imports)

# Define binaries (external executables)
binaries = []

# Modules to exclude (reduce bundle size)
excludes = [
    'tkinter',
    'matplotlib',
    'numpy',
    'pandas',
    'PIL',
    'PyQt5',
    'PyQt6',
    'PySide2',
    'PySide6',
    'scipy',
    'test',
    'unittest',
    # Note: distutils and setuptools removed from excludes for Python 3.13 compatibility
]

# Analysis: Scan the application
a = Analysis(
    ['pikaraoke/app.py'],  # Entry point
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,  # Python optimization level (0=none, 1=basic, 2=aggressive)
)

# Remove duplicate files
pyz = PYZ(a.pure)

# Create the executable
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='pikaraoke',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,  # Compress with UPX (reduces size by ~30-40%)
    console=True,  # Show console window (needed for logging and output)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(pikaraoke_dir / 'logo.ico'),  # Application icon
)

# Collect all files into a directory
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='pikaraoke',
)

# Note: Using COLLECT (one-folder mode) instead of one-file mode because:
# 1. Faster startup time (no extraction needed)
# 2. Easier to debug
# 3. Can include external files like ffmpeg.exe separately
# 4. More reliable with complex apps like Flask
