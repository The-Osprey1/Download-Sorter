# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

block_cipher = None
root = Path('.').resolve()

added_files = [
    ('config.json', '.'),
    ('README.md', '.'),
    ('LICENSE', '.'),
    ('FLOWCHART.md', '.'),
    ('DownloadSorter.ico', '.'),
]

hidden_imports = [
    'download_sorter',
    'download_sorter.about',
    'download_sorter.paths',
    'download_sorter.ui.workers',
    'download_sorter.update_check',
    'download_sorter.updater',
    'download_sorter.config',
    'download_sorter.router',
    'download_sorter.db',
    'download_sorter.fs_ops',
    'download_sorter.service',
    'download_sorter.ipc',
    'download_sorter.cli',
    'download_sorter.windows_integration',
    'download_sorter.ui',
    'download_sorter.ui.theme',
    'download_sorter.ui.motion',
    'download_sorter.ui.toasts',
    'download_sorter.ui.main_window',
    'download_sorter.ui.tray_app',
    'about',
    'sqlite3',
    'watchdog',
    'watchdog.observers',
    'watchdog.observers.winapi',
    'watchdog.events',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtNetwork',
    'PySide6.QtWidgets',
]

a = Analysis(
    ['main.py'],
    pathex=[str(root), str(root / 'src')],
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
    icon='DownloadSorter.ico',
    version='build/file_version_info.txt',
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
    icon='DownloadSorter.ico',
    version='build/file_version_info.txt',
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
