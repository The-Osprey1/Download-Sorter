# CLAUDE.md — Download Sorter v3.0 Final Release Guide

Welcome! This repository contains **Download Sorter v3.0** (`Download-Sorter-v3`), a Windows 11 single-process PySide6 desktop application (`DownloadSorter.exe`) and companion CLI (`DownloadSorter-cli.exe`).

## Crucial Handoff Documents (Read First)
1. **[`CLAUDE_RELEASE_HANDOFF.md`](./CLAUDE_RELEASE_HANDOFF.md)** — Complete architecture map, feature inventory, test suite breakdown, and step-by-step release checklist.
2. **[`AI_STUDIO_AUDIT_REPORT.md`](./AI_STUDIO_AUDIT_REPORT.md)** — Full architectural & concurrency audit from Google AI Studio (`Gemini 4 Argon`), including exact code patterns for the P0/P1 pre-release hardening items.

## Environment & Key Commands
Always use the existing Python 3.12 virtual environment at `C:\Users\devyn\Download-Sorter\.venv`:

- **Run full v3 test suite (131 tests)**:
  ```powershell
  C:\Users\devyn\Download-Sorter\.venv\Scripts\python.exe -X faulthandler -m pytest tests_v3 -v
  ```
- **Run CLI / Simulation**:
  ```powershell
  C:\Users\devyn\Download-Sorter\.venv\Scripts\python.exe main.py --version
  C:\Users\devyn\Download-Sorter\.venv\Scripts\python.exe main.py --simulate
  ```
- **Run release preflight verification**:
  ```powershell
  C:\Users\devyn\Download-Sorter\.venv\Scripts\python.exe verify_release.py
  ```
- **Build standalone EXE + Windows Setup installer (`dist/DownloadSorter-Setup-v3.0.0-windows-x64.exe`)**:
  ```powershell
  Get-Process -Name "*DownloadSorter*" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  C:\Users\devyn\Download-Sorter\.venv\Scripts\python.exe build_release.py
  ```

## Your Mission for the Final v3.0.0 Release Cut
Execute the P0 and P1 items detailed in `CLAUDE_RELEASE_HANDOFF.md` and `AI_STUDIO_AUDIT_REPORT.md`:
1. **P0-1 (`src/download_sorter/service.py`)**: Move `old_observer.stop()` and `old_observer.join(timeout=3.0)` outside `with self._lock:` in `update_config()` and `_restart_observer()`.
2. **P0-2 (`src/download_sorter/ui/main_window.py` & `service.py`)**: Offload synchronous disk/ADS/SQLite work in `refresh_all()` and `apply_architecture_and_shift()` to `QThreadPool` (`QRunnable` + Qt signals).
3. **P0-3 (`src/download_sorter/db.py` & `fs_ops.py`)**: Implement 2-phase write-ahead move intent logging (`PENDING` -> `COMPLETED`) and startup crash reconciliation (`reconcile_incomplete_moves()`).
4. **P0-4 (`src/download_sorter/fs_ops.py`)**: Add `is_file_locked(dest)` check and non-destructive `(Restored)` suffix collision protection during `undo_move()` / `undo_batch()`.
5. **P0-5 (`installer.iss`, `CHANGELOG.md`, `verify_release.py`)**: Sync version headers to `3.0.0`, point `verify_release.py` to `tests_v3`, and verify `--dialog` CLI parity.
6. **P1 Polish**: Live 3D slicer process detection via `CreateToolhelp32Snapshot` (`bambu-studio.exe`, `orca-slicer.exe`, `prusaslicer.exe`), C# installer retry + `.old_<guid>` rename fallback in `installer_builder.py`, and `PRAGMA wal_checkpoint(TRUNCATE)` on DB close.
7. Run the full `tests_v3` suite, run `verify_release.py`, and run `build_release.py` to produce the final signed/checksummed v3.0.0 installer.
