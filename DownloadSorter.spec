# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

block_cipher = None
root = Path('.').resolve()

added_files = [
    ('config.json', '.'),
    ('README.md', '.'),
    ('LICENSE', '.'),
    ('FLOWCHART.md', '.'),
]

hidden_imports = [
    'pystray',
    'pystray._win32',
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    'watchdog',
    'watchdog.observers',
    'watchdog.observers.winapi',
    'watchdog.events',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'sorter',
    'config_manager',
    'rules_manager',
    'tray',
    'watch',
    'sort_once',
    'sort_undo',
    'gcode_peek',
    'unzip_3d',
    'unzip_archive',
    'status',
    'quick_switcher',
    'ribbon_popup',
    'suite_theme',
    'clean_old',
]

a = Analysis(
    ['main.py'],
    pathex=[str(root)],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'numpy', 'pandas', 'IPython', 'notebook', 'pytest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 1. Windowed Executable (No console flash, ideal for tray, GUI, scheduled startup)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DownloadSorter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# 2. Console Executable (Native console streams, ideal for CLI, context menus, CI/CD, piping)
exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DownloadSorter-cli',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    exe_cli,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='DownloadSorter',
)
