# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path, PurePosixPath

block_cipher = None
root = Path('.').resolve()

added_files = [
    ('README.md', '.'),
    ('LICENSE', '.'),
    ('FLOWCHART.md', '.'),
    ('DownloadSorter.ico', '.'),
    ('src/download_sorter/ui/assets/chrome_guide', 'download_sorter/ui/assets/chrome_guide'),
]

hidden_imports = [
    'download_sorter',
    'download_sorter.about',
    'download_sorter.paths',
    'download_sorter.ui.workers',
    'download_sorter.update_check',
    'download_sorter.updater',
    'download_sorter.config',
    'download_sorter.architecture',
    'download_sorter.learning',
    'download_sorter.project_detect',
    'download_sorter.router',
    'download_sorter.dest',
    'download_sorter.db',
    'download_sorter.fs_ops',
    'download_sorter.service',
    'download_sorter.ipc',
    'download_sorter.cli',
    'download_sorter.chrome_bridge',
    'download_sorter.windows_integration',
    'download_sorter.ui',
    'download_sorter.ui.theme',
    'download_sorter.ui.motion',
    'download_sorter.ui.toasts',
    'download_sorter.ui.chrome_walkthrough',
    'download_sorter.ui.main_window',
    'download_sorter.ui.tray_app',
    'download_sorter.pipeline',
    'download_sorter.folder_designer',
    'download_sorter.rule_text',
    'download_sorter.ui.folders',
    'download_sorter.ui.folders.folders_tab',
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

# QtNetwork only handles local IPC; HTTPS updates use Python's urllib/OpenSSL (libcrypto-3.dll, libssl-3.dll),
# so drop Qt's OpenSSL TLS backend and runner-PATH OpenSSL DLLs while keeping qschannelbackend.dll.
_EXCLUDED_BINARIES = frozenset(
    {
        "libcrypto-3-x64.dll",
        "libssl-3-x64.dll",
        "pyside6/plugins/tls/qopensslbackend.dll",
    }
)
a.binaries = [
    entry
    for entry in a.binaries
    if PurePosixPath(entry[0].replace("\\", "/")).as_posix().lower().removeprefix("_internal/")
    not in _EXCLUDED_BINARIES
]

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
