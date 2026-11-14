# Download Sorter v3.0 — Claude Code Final Release Handoff

**Repository Root**: `c:\Users\devyn\Documents\antigravity\beautiful-bell\Download-Sorter-v3`  
**Legacy v2 Reference**: `C:\Users\devyn\Download-Sorter`  
**Python Environment**: `C:\Users\devyn\Download-Sorter\.venv\Scripts\python.exe`  
**Audit Companion Report**: [`AI_STUDIO_AUDIT_REPORT.md`](./AI_STUDIO_AUDIT_REPORT.md)  
**Current Test Suite Status**: **131 passed, 0 failed** (`pytest Download-Sorter-v3\tests_v3 -v`, `12.70s`)

---

## 1. Executive Summary & Handoff Scope

Download Sorter v3.0 consolidates the 58-script legacy v2 toolkit into a unified, single-process PySide6 desktop application (`DownloadSorter.exe`) and companion CLI (`DownloadSorter-cli.exe`).

All feature development and audit milestones leading up to the final release cut are complete:
1. **Top-to-Bottom Layered Folder Architecture Canvas (`architecture.py`, `ui/architecture_canvas.py`)**: Interactive 2-to-5 tier visual hierarchy with `Auto-Create Basic` (`basic_starter`), 40+ premade folders, custom rule-backed folders, automatic file shifting, 1-click Undo, Return to Defaults, and v2.8 setup carry-over (`carry_over_setup`, `export_setup_file`).
2. **Top-Right Side Ribbon Notifications (`ui/toasts.py`)**: Category-aware animated ribbons with configurable countdown timer (`ribbon_timeout_seconds`, 1–30s spinbox in Settings), hover-to-pause, instant `✕` close button (`WA_ShowWithoutActivating` without `WindowDoesNotAcceptFocus`), and `Close Ribbons` control.
3. **Interactive Sorty Mascot & Easter Egg (`ui/motion.py`, `ui/main_window.py`)**: Clickable Sorty mascot for tips and hop animations, plus the secret **Sorty Party Mode** Easter Egg (triggered by 5 rapid clicks on Sorty within 4s or typing `sorty`, `party`, `disco`, `easteregg`, or `benchy.party` into the Custom Rules Live Rule Tester) with `ConfettiOverlayWidget`.
4. **Recency-Gated Recent File Insert / Quick Switcher (`quick_switcher.py`, `ui/quick_switcher_pill.py`)**: Full v3 port and enhancement of the v2 `#32770` File Open/Upload dialog helper, supporting single-file and multi-file batch injection, 3-tier fallback (direct multi-select edit injection -> `.sorter_stage` hardlink/copy staging -> `CF_HDROP` clipboard + OLE drag-and-drop), 64-bit Win32 pointer safety, multilingual dialog button detection, and `--dialog <hwnd>` CLI support.
5. **Google AI Studio (`Gemini 4 Argon`) Full-Codebase Audit ([`AI_STUDIO_AUDIT_REPORT.md`](./AI_STUDIO_AUDIT_REPORT.md))**: Comprehensive architectural scorecard (**B+ / 8.2 out of 10**), 33-feature v2-vs-v3 parity matrix, and prioritized P0/P1/P2 engineering directives for final GA hardening.

---

## 2. Codebase Architecture & Module Map

```text
Download-Sorter-v3/
├── main.py                              # Root entry point delegating to download_sorter.cli.main()
├── pyproject.toml                       # Package metadata, dependencies, pytest config
├── DownloadSorter.spec                  # Single-collection dual-binary PyInstaller specification
├── build_release.py                     # Standalone release compiler (PyInstaller + ZIP + C# Setup.exe)
├── installer_builder.py                 # Zero-dependency C# WinForms Setup compiler via native csc.exe
├── verify_release.py                    # Release preflight gatekeeper script
├── AI_STUDIO_AUDIT_REPORT.md            # Full Gemini 4 Argon architectural & parity audit report
├── CLAUDE_RELEASE_HANDOFF.md            # This handoff briefing document
├── src/download_sorter/
│   ├── __init__.py                      # Package version export
│   ├── about.py                         # Canonical version & metadata constants (__version__ = "3.0.0")
│   ├── paths.py                         # AppPaths resolution (data_dir, config_file, db_file, recent_state_file)
│   ├── config.py                        # Schema v3 Config dataclass, Rule model, templates, v2 carry-over
│   ├── router.py                        # Pure 4-stage deterministic routing engine & trace generator
│   ├── architecture.py                  # Multi-tier FolderArchitecture graph, presets & rule compiler
│   ├── fs_ops.py                        # Win32 lock checks, ADS Zone.Identifier reader, SHA-256 safe_move & undo
│   ├── db.py                            # SQLite WAL persistence (moves, held_files, stats)
│   ├── service.py                       # SorterService orchestrator, watchdog observer & dialog monitor thread
│   ├── quick_switcher.py                # Win32 #32770 dialog detector, recency state, 3-path file injector & OLE drag
│   ├── ipc.py                           # Single-instance QLocalServer / QLocalSocket JSON IPC
│   ├── cli.py                           # CLI parser & command dispatcher (--status, --explain, --dialog, etc.)
│   ├── windows_integration.py           # HKCU Run startup & Explorer directory context-menu registration
│   └── ui/
│       ├── theme.py                     # Apple-HIG dark mode stylesheet & color tokens
│       ├── motion.py                    # SortyMascotWidget, ConfettiOverlayWidget, AnimatedBanner, vector icons
│       ├── toasts.py                    # Top-right CategoryArtWidget, ToastNotification & ToastManager
│       ├── architecture_canvas.py       # Top-to-bottom FolderArchitectureCanvas & ArchitectureTab
│       ├── quick_switcher_pill.py       # Floating DWM-rounded QuickSwitcherPill & batch checklist popup
│       ├── main_window.py               # 4-tab MainWindow (Home | Folder Architecture | Custom Rules | Settings)
│       └── tray_app.py                  # Single-process QSystemTrayIcon host, IPC handler & QuickSwitcherBridge
└── tests_v3/
    ├── conftest.py                      # Isolated AppPaths & offscreen QApplication fixtures with GC cleanup
    ├── mock_win32.py                    # Deterministic Win32 HWND / dialog / clipboard simulation harness
    ├── test_router_and_config.py        # Core 4-stage router & Config/v2 import unit tests
    ├── test_service_and_undo.py         # SorterService, .3MF hold, safe_move & undo tests
    ├── test_ui_and_ipc.py               # MainWindow, Folder Architecture, Side Ribbons, Sorty Easter Egg & IPC tests
    ├── test_quick_switcher.py           # 22 unit/integration tests for Recent File Insert & QuickSwitcherPill
    ├── test_e2e_tier1_features.py       # E2E Tier 1 feature verification suite
    ├── test_e2e_tier2_boundaries.py     # E2E Tier 2 boundary & edge-case verification suite
    ├── test_e2e_tier3_interactions.py   # E2E Tier 3 cross-subsystem interaction suite
    └── test_e2e_tier4_scenarios.py      # E2E Tier 4 full lifecycle user workflows
```

---

## 3. Recent File Insert / Quick Switcher Architecture (Milestone 1)

### How It Works End-to-End
1. **Recency Recording (`fs_ops.py` -> `quick_switcher.record_recent_file`)**:
   - Whenever `execute_route()` moves a file (or coalesces a batch within 10s), it records the moved path, display folder, category badge, color, and timestamp in `recent_files.json` under `AppPaths.data_dir` protected by cross-process `msvcrt.locking` (`state_lock`) and atomic temp-file replacement (`os.replace`).
   - Standard files remain eligible for `quick_switcher_timeout_seconds` (default `300s` / 5 minutes); 3D/CAD files (`.3mf`, `.stl`, `.step`, `.stp`, `.f3d`, `.f3z`) use `quick_switcher_3d_timeout_hours` (default `12h`).
2. **Win32 Common Item Dialog Polling (`service.py` -> `quick_switcher.find_active_file_dialog`)**:
   - Background worker checks `GetForegroundWindow()` for class `#32770` windows that are visible, belong to a foreign PID, do not already carry the `DownloadSorterQuickSwitcher` window property (`GetPropW`), and represent an Open/Upload dialog (having Edit control `1148`/`1152` or `DUIViewWndClassName`/`SysListView32` and a non-Save affirmative button across English and localized labels).
3. **Floating Docked Pill UI (`ui/quick_switcher_pill.py` -> `QuickSwitcherPill`)**:
   - Marshaled onto the Qt GUI thread via `_QuickSwitcherBridge.dialog_detected` in `ui/tray_app.py` (or launched standalone via `DownloadSorter-cli.exe --dialog <hwnd>`).
   - Docks smoothly above or below the active file dialog (`_track_dialog` at 100ms), applies Windows 11 `DwmSetWindowAttribute` rounded corners, and supports:
     - **Single-File Mode**: 1-click `Insert` (`Ctrl+G`), `Copy` (`Ctrl+Shift+C`), or native Win32 OLE `DoDragDrop` drag out of the pill.
     - **Batch Mode**: Expandable checklist (`▾`), `Insert (N)` (`Ctrl+G`), `All` (`Ctrl+Shift+G`), `Copy`, and multi-file drag.
4. **3-Path Batch File Injection (`quick_switcher.inject_files_into_dialog`)**:
   - **Path A**: Direct multi-select `"file1" "file2"` string into Edit control (`1148`/`1152`) when all files share a parent directory and fit within the control's buffer limit.
   - **Path B**: Hardlink/copy staging into `<Downloads>/.sorter_stage` when files span multiple category subfolders, followed by multi-select injection.
   - **Path C**: Automatic fallback to `CF_HDROP` clipboard copy (`copy_files_to_clipboard`) if the target dialog rejects multi-file text injection or runs elevated (`UIPI`).

---

## 4. Prioritized Audit Action Items for Claude Code (from `AI_STUDIO_AUDIT_REPORT.md`)

Full technical analysis and code snippets for every item below are documented in [`AI_STUDIO_AUDIT_REPORT.md`](./AI_STUDIO_AUDIT_REPORT.md).

### P0 Blockers (Execute Before Tagging Final Public GA)
- [ ] **P0-1 (`src/download_sorter/service.py`) — Fix Observer Join Lock Inversion**:
  - In `update_config()` and `_restart_observer()`, extract `self._observer` inside `with self._lock:`, set `self._observer = None`, release `self._lock`, and invoke `old_observer.stop()` + `old_observer.join(timeout=3.0)` **outside** `with self._lock:` so in-flight watchdog events calling `_enqueue()` cannot deadlock for 3.0 seconds.
- [ ] **P0-2 (`src/download_sorter/ui/main_window.py` & `service.py`) — Offload Main-Thread Disk/ADS/SHA-256 I/O**:
  - Move `refresh_all()` disk/ADS/SQLite queries and `apply_architecture_and_shift()` into background `QRunnable` workers dispatched via `QThreadPool.globalInstance()`, updating Qt widgets strictly inside main-thread signal slots.
- [ ] **P0-3 (`src/download_sorter/db.py` & `fs_ops.py`) — Two-Phase Write-Ahead Move Journaling**:
  - Record move intent (`status='PENDING'`) in `state.db` *before* calling `_move_with_retries()`, then update to `status='COMPLETED'` after the filesystem move succeeds. Add `reconcile_incomplete_moves()` on service startup.
- [ ] **P0-4 (`src/download_sorter/fs_ops.py`) — Safe `undo_batch` Collision & Lock Guards**:
  - Check `is_file_locked(dest)` before attempting `undo_move()`. If `src_path` is now occupied by a newer file, restore using a non-destructive `(Restored)` suffix rather than overwriting with `os.replace()`.
- [ ] **P0-5 (`installer.iss`, `CHANGELOG.md`, `verify_release.py`) — Version & Preflight Alignment**:
  - Ensure `installer.iss` and `CHANGELOG.md` headers are synced to `3.0.0` (matching `about.__version__` and `pyproject.toml`), and update `verify_release.py` to run `pytest tests_v3` and recognize `--dialog`.

### P1 High-Polish Enhancements (Recommended for v3.0 GA)
- [ ] **P1-1 (`src/download_sorter/fs_ops.py` & `service.py`)**: Add pure-ctypes `CreateToolhelp32Snapshot` process check (`is_slicer_running()` for `bambu-studio.exe`, `orca-slicer.exe`, `prusaslicer.exe`, `cura.exe`) so `.3mf` grace holds dynamically extend while a slicer is open.
- [ ] **P1-2 (`installer_builder.py`)**: Add `proc.WaitForExit(3000)` and a 10-attempt exponential backoff extraction retry with `.old_<guid>` rename fallback to prevent `ERROR_SHARING_VIOLATION` when upgrading over a running instance.
- [ ] **P1-3 (`src/download_sorter/fs_ops.py`)**: Guard against OneDrive cloud placeholder hydration (`FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000`) and use head/tail 64KB sample hashing before full SHA-256 on multi-gigabyte collisions.
- [ ] **P1-4 (`src/download_sorter/db.py`)**: Set `PRAGMA synchronous = NORMAL` on every connection in `_connect()`, run `PRAGMA wal_checkpoint(PASSIVE)` after batches, and execute `PRAGMA wal_checkpoint(TRUNCATE); PRAGMA optimize;` in `close()`.
- [ ] **P1-5 (`src/download_sorter/service.py`)**: Optional background Recycle Bin sweep (`SHFileOperationW` with `FOF_ALLOWUNDO`) for aged installers (`clean_old.py` parity).

---

## 5. Verification & Final Release Build Checklist

Run all commands from the workspace root (`c:\Users\devyn\Documents\antigravity\beautiful-bell`) using the project virtual environment:

### Step 1: Run the Full v3 Automated Test Suite
```powershell
C:\Users\devyn\Download-Sorter\.venv\Scripts\python.exe -X faulthandler -m pytest Download-Sorter-v3\tests_v3 -v
```
- **Expected**: `131 passed` with exit code `0`.

### Step 2: Verify CLI Surface & Quick Switcher Entry Point
```powershell
C:\Users\devyn\Download-Sorter\.venv\Scripts\python.exe Download-Sorter-v3\main.py --version
C:\Users\devyn\Download-Sorter\.venv\Scripts\python.exe Download-Sorter-v3\main.py --help
C:\Users\devyn\Download-Sorter\.venv\Scripts\python.exe Download-Sorter-v3\main.py --explain "benchy_plate1.3mf" --url "https://makerworld.com/en/models/123"
```
- **Expected**: Version prints `Download Sorter 3.0.0`, `--help` includes `--dialog DIALOG`, and `--explain` outputs deterministic 4-stage trace.

### Step 3: Build Standalone Executables & Setup Installer
```powershell
Get-Process -Name "*DownloadSorter*" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
C:\Users\devyn\Download-Sorter\.venv\Scripts\python.exe Download-Sorter-v3\build_release.py
```
- **Outputs**:
  - `Download-Sorter-v3\dist\DownloadSorter\DownloadSorter.exe` (windowed tray/UI binary)
  - `Download-Sorter-v3\dist\DownloadSorter\DownloadSorter-cli.exe` (console CLI binary)
  - `Download-Sorter-v3\dist\DownloadSorter-v3.0.0-windows-x64.zip`
  - `Download-Sorter-v3\dist\DownloadSorter-Setup-v3.0.0-windows-x64.exe`
  - `Download-Sorter-v3\dist\checksums.txt`

### Step 4: Verify Silent Installation & Installed Binary Version
```powershell
$p = Start-Process -FilePath "Download-Sorter-v3\dist\DownloadSorter-Setup-v3.0.0-windows-x64.exe" -ArgumentList "/S" -Wait -PassThru
Write-Host "Installer ExitCode: $($p.ExitCode)"
& "$env:LOCALAPPDATA\Programs\DownloadSorter\DownloadSorter-cli.exe" --version
```
- **Expected**: `Installer ExitCode: 0` and `Download Sorter 3.0.0`.
