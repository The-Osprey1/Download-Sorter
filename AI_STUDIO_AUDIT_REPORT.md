# Download Sorter v3.0 — Google AI Studio Codebase Audit Report

**Auditor Session**: Google AI Studio (Model: Gemini 4 Argon)  
**Lead Systems Architects**: Principal Systems Architect & Senior Windows Systems Engineer (Consultant) & Teamwork Audit Working Group  
**Target Repository**: `Download-Sorter-v3` (`c:\Users\devyn\Documents\antigravity\beautiful-bell\Download-Sorter-v3`)  
**Audit Date**: November 14, 2026  
**Integrity Mode**: Development / Pre-Release Verification  
**Test Suite Verification Baseline**: `tests_v3` (42 passed, 0 failures, 2 warnings in 6.56s)  

---

## Executive Summary

Download Sorter v3.0 represents a massive, professional structural leap forward from the sprawling 58-script legacy v2 codebase. The consolidation into a clean, domain-driven Python package (`src/download_sorter/`) featuring a deterministic 4-stage pure evaluation router (`router.py`), SQLite WAL state tracking (`db.py`), atomic filesystem primitives (`fs_ops.py`), single-instance named pipe IPC (`ipc.py`), and a dedicated PySide6 presentation layer (`ui/`) reflects mature systems engineering. Furthermore, the inclusion of Win32 native locking checks (`CreateFileW`), NTFS Alternate Data Stream (`Zone.Identifier`) source tracking, and single-collection dual-binary PyInstaller packaging (`DownloadSorter.spec`) demonstrates deep Windows platform affinity.

However, the codebase currently suffers from several classic desktop concurrency and durability pitfalls:
1. **Lock-order inversion deadlock**: Holding a reentrant lock across a blocking thread-join in `service.py:update_config()` produces a deterministic 3.0-second GUI freeze under load.
2. **Qt main thread starvation**: Executing synchronous NTFS ADS file reads, SQLite queries, and full SHA-256 duplicate hashing on the main PySide6 thread causes dropped frames and ghost windows (`Not Responding`).
3. **Durability commit inversion**: Moving files on disk before logging write-ahead intent in `state.db` leaves unrecoverable orphan files if interrupted mid-batch.
4. **Missing slicer process awareness**: Relying solely on elapsed file age without verifying running slicer processes (`CreateToolhelp32Snapshot`) risks moving active 3MF models while users are still working on them.
5. **Release preflight blockers**: Packaging version drift (2.8.0 vs 3.0.0) and missing `--dialog` CLI parity cause `verify_release.py` failures.

Resolving these targeted issues elevates Download Sorter v3.0 from a **B+ (8.2 / 10)** to an **A+ production-grade release**.

---

## Section 1: Executive Assessment & Architectural Grade

### 1.1 Architectural Grade: B+ (8.2 / 10)

Download Sorter v3.0 is awarded an overall composite grade of **B+ (8.2 / 10)**. The foundational architecture is exceptionally well-conceived and cleanly separated. The core routing and persistence models are solid, but concurrency edge cases, main-thread blocking, and release verification gaps must be addressed prior to public release.

### 1.2 Evaluation Scorecard across 5 Architectural Dimensions

| Dimension | Score (1–10) | Rating | Key Finding |
|---|---|---|---|
| **1. Modular Separation** | **9.5 / 10** | *Exemplary* | Impeccable decoupling between pure routing (`router.py`), stateful orchestration (`service.py`), OS primitives (`fs_ops.py`), state persistence (`db.py`), and PySide6 presentation (`ui/`). Completely headless CLI operation is preserved. |
| **2. Concurrency & Responsiveness** | **6.5 / 10** | *Needs Polish* | Vulnerable to a 3.0-second lock-order deadlock in `service.py:update_config()`, synchronous NTFS ADS reads during `MainWindow.refresh_all()`, and un-offloaded directory hashing during `apply_architecture_and_shift()`. |
| **3. Data Durability & State Safety** | **7.5 / 10** | *Very Good* | Solid SQLite WAL configuration (`PRAGMA journal_mode=WAL`) and byte-for-byte duplicate isolation (`_Duplicates/`). Lacks pre-move write-ahead intent logging (`PENDING` state) and destination collision guards in `undo_batch`. |
| **4. Feature Parity & User Experience** | **8.5 / 10** | *Good* | Apple-minimalist dark mode UI, interactive Folder Architecture node canvas, and side-ribbon toasts. Recent File Insert (Quick Switcher) is actively being ported; slicer process detection (`CreateToolhelp32Snapshot`) is currently missing. |
| **5. Release Packaging & Deployment** | **8.8 / 10** | *Very Good* | Impressive zero-dependency standalone C# WinForms installer compiled via native Windows `csc.exe` and dual-binary PyInstaller spec. Currently blocked by version drift (2.8.0 vs 3.0.0) and missing `--dialog` CLI flag. |
| **Composite Overall Score** | **8.2 / 10** | **B+** | **Production-ready core requiring concurrency, durability, and preflight hardening.** |

### 1.3 Key Architectural Strengths

- **Pure, Deterministic Routing Engine (`router.py`)**: Rule evaluation is strictly decoupled from filesystem side effects. The 4-stage waterfall (Ignore Guards → Source Domain ADS → User Rules → Fallback Groups) generates a human-readable execution trace (`RouteStep`), enabling instant simulation (`--simulate`) and 100% deterministic test coverage.
- **Non-Recursive Top-Level Watchdog Observer (`_TopLevelEventHandler`)**: Restricting filesystem directory observation strictly to top-level `Downloads` eliminates the classic infinite re-indexing loops that plague naive file organizers when sorting into subdirectories.
- **Byte-for-Byte Collision Safety (`fs_ops.py`)**: Safely distinguishes true content duplicates (via SHA-256 routing to `_Duplicates/YYYY-MM/`) from distinct files with identical filenames (via collision numbering `name (2).ext`), guaranteeing zero accidental data loss.
- **Zero-Dependency Native Windows Installer (`installer_builder.py`)**: Automatically locates the built-in Microsoft .NET Framework compiler (`csc.exe`) to compile a custom dark-themed WinForms setup executable embedding `payload.zip`. Eliminates third-party installer runtimes while providing silent install (`/S`), update (`/UPDATE`), and directory selection (`/D=dir`).
- **Single-Collection Dual Binary Architecture (`DownloadSorter.spec`)**: Compiles both a windowless GUI daemon (`DownloadSorter.exe`) and a native console executable (`DownloadSorter-cli.exe`) into a shared `dist/DownloadSorter/` directory, sharing PySide6 DLLs and Python runtimes without disk bloat.

### 1.4 Primary System Vulnerabilities & Risks

1. **Lock-Hold Across Thread Join Deadlock (`P0-1`)**: `service.py:update_config()` holds `self._lock` (`threading.RLock`) while invoking `self._observer.join(timeout=3.0)`. In-flight watchdog callbacks requiring `self._lock` deadlock the observer, freezing the Qt main thread for 3 full seconds.
2. **Qt Main Thread I/O Starvation (`P0-2`)**: Synchronous disk walks, NTFS Alternate Data Stream reads (`read_zone_url`), and full SHA-256 hashing executed directly on the Qt GUI thread drop frame rates during animations and trigger Windows `(Not Responding)` ghost windows on large directories.
3. **Post-Move Commit Inversion (`P0-3`)**: Executing `_move_with_retries(src, final_dest)` before recording the move record in `state.db` means that any mid-batch crash, SIGKILL, or power loss creates orphan files on disk with zero database history, permanently breaking undo.
4. **Slicer File In-Memory Hijacking (`P1-1`)**: Relying purely on file age for `.3mf` files without checking whether Bambu Studio or OrcaSlicer is actively running risks sorting files while users are actively working in slicers.
5. **Release Preflight Drift (`P0-5`)**: Discrepancies between `installer.iss` (2.8.0), `CHANGELOG.md` (2.8.0), and `about.py` (3.0.0), alongside a missing `--dialog` argument in `cli.py`, cause `verify_release.py` preflight stages to fail.

---

## Section 2: Missing Features Analysis (v2 vs v3 Delta)

### 2.1 Complete Feature Inventory & Porting Status (33 Features)

The following comprehensive inventory catalogs every feature across the legacy v2 codebase (`C:\Users\devyn\Download-Sorter\`, 58 scripts) and the modern v3 package (`src/download_sorter/`):

| # | Feature / Utility | Legacy v2 Location | Modern v3 Location | Porting Status | Audit Classification | Architectural Target |
|---|---|---|---|---|---|---|
| 1 | **Quick Switcher / Dialog Insert (R1)** | `quick_switcher.py` | `ui/quick_switcher.py` | **Ported / In Verification** | **Essential** | v3.0 GA Core |
| 2 | **3-Path Batch File Handoff (R1)** | `batch_handoff.py` | `batch_handoff.py` | **Ported / In Verification** | **Essential** | v3.0 GA Core |
| 3 | **3MF Slicer Process Awareness** | `hold_manager.py:121` | *None* | **Completely Missing** | **Essential** | v3.0 GA Core |
| 4 | **3MF Dynamic Recency Extension** | `hold_manager.py:230` | *None* | **Completely Missing** | **Essential** | v3.0 GA Core |
| 5 | **3MF Basic Grace Hold & Flush** | `hold_manager.py` | `service.py:117, 195` | **Fully Ported** | Core Complete | v3.0 Core |
| 6 | **3MF Embedded Plate Previews** | `gcode_peek.py`, `ribbon_popup.py` | *None* | **Completely Missing** | Optional / Power-User | v3.1 Ribbon Toast Polish |
| 7 | **MakerWorld 3MF Metadata Sniffer** | `sorter.py:1918`, `kits.py` | *None* | **Completely Missing** | Optional / Power-User | v3.1 Rule Preset |
| 8 | **G-code Machine Sniffer (Haas vs 3D)** | `gcode_peek.py:114` | *None* | **Completely Missing** | Deprecated / Incompatible | Omit (Standalone CLI) |
| 9 | **G-code Peek CLI (`gcode-peek`)** | `gcode_peek.py` | *None* | **Completely Missing** | Deprecated / Incompatible | Omit (Separate Repo) |
| 10 | **Staged Machine Remodel Engine** | `remodel.py` (1,307 lines) | *None* | **Completely Missing** | Deprecated / Incompatible | Omit (Replaced by Canvas) |
| 11 | **Folder Architecture Canvas & Shift** | *None (v3 original)* | `architecture_canvas.py`, `service.py` | **Fully Ported (v3 New)** | Core Complete | v3.0 Core |
| 12 | **Smart Archive Unpackers (`unzip-3d`)** | `unzip_3d.py`, `unzip_archive.py` | *None* | **Completely Missing** | Optional / Power-User | v3.1 Post-Route Hook |
| 13 | **Shelf Quick Search (`Ctrl+Shift+Space`)** | `shelf.py`, `file_index.py` | *None* | **Completely Missing** | Deprecated / Incompatible | Omit (Superseded by Quick Switcher) |
| 14 | **Desktop Sweep** | `desktop_sweep.py` | *None* | **Completely Missing** | Optional / Power-User | v3.1 Secondary Root Watch |
| 15 | **Clean Old Installers / Recycle Bin** | `clean_old.py` | *None* | **Completely Missing** | **Essential** | v3.0 GA Maintenance |
| 16 | **Projects Hub & Project Mode** | `projects_hub.py`, `projects_page.py` | *None* | **Completely Missing** | Optional / Power-User | v3.1 Target Override Mode |
| 17 | **Self-Learning / Adaptive Routing** | `learning.py` (1,123 lines) | *None* | **Completely Missing** | Optional / Power-User | v3.2 UI Suggestion Banner |
| 18 | **PDF Content Text Inspection** | `pdf_content.py` | *None* | **Completely Missing** | Optional / Power-User | v3.2 Advanced Plugin |
| 19 | **Photo EXIF Camera vs Screenshot** | `photo_kind.py` | *None* | **Completely Missing** | Optional / Power-User | v3.2 Advanced Plugin |
| 20 | **Fuzzy User Folder Matching** | `sorter.py:1816` | *None* | **Completely Missing** | Optional / Power-User | v3.2 Heuristic Polish |
| 21 | **Core 4-Step Routing Engine** | `sorter.py` | `router.py` | **Fully Ported** | Core Complete | v3.0 Core |
| 22 | **File Lock & Settle Checks** | `sorter.py` | `fs_ops.py:20-58` | **Fully Ported** | Core Complete | v3.0 Core |
| 23 | **Duplicate Quarantine (`_Duplicates/`)** | `sorter.py` | `fs_ops.py:68-106` | **Fully Ported** | Core Complete | v3.0 Core |
| 24 | **Collision Auto-Renaming `(2)`** | `sorter.py` | `fs_ops.py:78-90` | **Fully Ported** | Core Complete | v3.0 Core |
| 25 | **Write-Ahead Journal** | `safe_ops.py` (JSON) | `db.py` (SQLite WAL) | **Superseded / Modernized** | Deprecated Pattern | v3.0 Core |
| 26 | **Undo (Single & Batch)** | `sort_undo.py` | `fs_ops.py:180-218` | **Fully Ported** | Core Complete | v3.0 Core |
| 27 | **Toast Notifications** | `ribbon_popup.py` | `toasts.py` | **Fully Ported & Enhanced** | Core Complete | v3.0 Core |
| 28 | **Single-Instance IPC** | `quit_signal.py` (file lock) | `ipc.py` (QLocalSocket) | **Superseded / Modernized** | Deprecated Pattern | v3.0 Core |
| 29 | **System Tray Menu & App** | `tray.py`, `tray_qt.py` | `tray_app.py` | **Partially Ported** | **Essential** | v3.0 GA Tray Counters |
| 30 | **Explorer Context Menus** | `install_context_menu.ps1` | `windows_integration.py` | **Fully Ported** | Core Complete | v3.0 Core |
| 31 | **Startup Launch Integration** | `install_startup.ps1` | `windows_integration.py` | **Fully Ported** | Core Complete | v3.0 Core |
| 32 | **Config Persistence & v2 Import** | `config_manager.py` | `config.py` | **Fully Ported** | Core Complete | v3.0 Core |
| 33 | **Unified CLI Interface** | Multiple `.bat`/entry scripts | `cli.py` | **Fully Ported** | Core Complete | v3.0 Core |

---

### 2.2 Analysis of Essential Features (Required for v3.0 GA)

#### 1. Bambu Studio / OrcaSlicer `.3mf` Grace Hold & Live Process Detection
- **The Vulnerability in Current v3**: Current v3 implements a static time-based hold (`service.py:117-129`) and checks `is_file_locked()`. However, Bambu Studio and OrcaSlicer load the entire `.3mf` ZIP container into memory upon opening and immediately release the Win32 file handle. While a user spends 30 minutes orienting parts, setting infill, and generating supports, the file is completely unlocked on disk. If v3's static timer expires and sorts the file into `3D Printing/`, when the user hits `Ctrl+S` in Bambu Studio, the slicer either throws a path error or saves a duplicate `.3mf` back into `Downloads/`, causing state bifurcation.
- **Architectural Solution**: Restore live process detection via Win32 `CreateToolhelp32Snapshot`.
  - **Do NOT shell out to `tasklist.exe`** (which spawns console subprocesses costing 80–150ms of CPU overhead). Use `ctypes.windll.kernel32` with `CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)`, `Process32FirstW`, and `Process32NextW` to scan process names (`bambu-studio.exe`, `orca-slicer.exe`, `prusaslicer.exe`, `cura.exe`). In pure ctypes, this execution takes `< 0.4 ms`.
  - **Dynamic Hold Lease**: When a `.3mf`, `.stl`, or `.step` file enters the hold queue, query `is_slicer_running()`. If `True`, dynamically extend the hold lease and keep the file in `Downloads/` until the slicer process exits, capped at a 14-day safety ceiling.

#### 2. Common Item Dialog Quick Switcher (`quick_switcher.py` & `--dialog`)
- Once Download Sorter sorts an incoming download into deep nested category structures (e.g., `Downloads/Documents/Engineering/Spec_2026.pdf`), the user faces intense friction when attempting to upload the file in Chrome, Slack, Discord, or an ERP portal, expecting it in root `Downloads/`.
- Completing and verifying the port of `quick_switcher.py` (which detects Windows Common Item File Open/Upload Dialogs `#32770` and injects recent paths into Edit controls `1148`/`1152`) resolves the single biggest user pain point of desktop sorting.

#### 3. Stale File Auto-Cleanup (`clean_old.py`)
- Category folders for Installers (`.exe`, `.msi`) and Archives (`.zip`, `.tar.gz`) accumulate gigabytes of obsolete clutter over months.
- Porting `clean_old.py` as an opt-in background maintenance task using `shell32.SHFileOperationW` with `FOF_ALLOWUNDO | FOF_NOCONFIRMATION` moves aged files safely to the Windows Recycle Bin, completing the full lifecycle management story for v3.0.

#### 4. System Tray Daily Activity & Held Counters (`tray_app.py`)
- In v2, the tray icon menu displayed instant at-a-glance awareness: `"14 files sorted today · 2 held"`. In v3, the tray menu displays static text labels. Restoring dynamic counters via a periodic SQLite query restores parity with minimal complexity.

---

### 2.3 Analysis of Optional / Power-User Features (v3.1 / v3.2 Roadmap)

- **Desktop Sweep (`desktop_sweep.py`) [v3.1]**: Implement as an optional "Secondary Ingest Root" in `service.py` that periodically sweeps loose non-shortcut (`!.lnk`, `!.url`) files older than $N$ days from `%USERPROFILE%\Desktop` into the router pipeline.
- **Smart Archive Unpackers (`unzip_3d.py`, `unzip_archive.py`) [v3.1]**: MakerWorld and Printables bundle multi-part models into `.zip` archives. Implement as a "Post-Route Action Hook" in `router.py` so matching archives automatically unpack into a named subfolder and quarantine the raw `.zip`.
- **Projects Hub & Project Mode (`projects_hub.py`) [v3.1]**: Re-architect as a temporary "Target Override Mode" accessible from the tray icon ("Route all CAD/3D files to Project X for the next 2 hours").
- **Embedded Plate Previews (`gcode_peek.py`) [v3.1]**: Parse embedded PNG thumbnails (`Metadata/plate_1.png`) from `.3mf` ZIP headers in-memory to display 3D model previews inside side-ribbon toasts.
- **Self-Learning Adaptive Routing (`learning.py`) [v3.2]**: Keep statistical learning strictly out of the deterministic execution path! Instead, surface user move observations as non-intrusive Suggestion Banners in the UI ("You moved 3 files containing 'invoice' to Accounting/. Create a rule?").

---

### 2.4 Analysis of Deprecated / Incompatible Features (Omit from v3)

- **Machine Remodel Engine (`remodel.py`) — DEPRECATED**: Whole-disk PC remodeling is inherently high-risk, out of scope for a Downloads organizer, and fully superseded within Downloads by v3's visual `architecture_canvas.py` and `apply_architecture_and_shift()`.
- **G-code Machine Sniffer (`gcode_peek.py`) — INCOMPATIBLE**: Inspecting CNC toolpaths and G-code speeds/feeds is a specialized maker domain utility, not a general filesystem sorting concern. It should remain a standalone repository or external CLI tool.
- **Shelf Search HUD (`shelf.py`) — DEPRECATED**: A custom global `Ctrl+Shift+Space` overlay with Win32 OLE Drag & Drop duplicates modern Windows utilities (PowerToys Run, Everything, Raycast) while overlapping with the `#32770` Quick Switcher. Omit to prevent code bloat.
- **Flat JSON Write-Ahead Journal (`safe_ops.py`) — SUPERSEDED**: Replaced by ACID-compliant SQLite WAL database (`db.py`).
- **File-Lock Mutex / Quit Signals (`quit_signal.py`) — SUPERSEDED**: Replaced by desktop-scoped `QLocalServer`/`QLocalSocket` IPC (`ipc.py`).

---

## Section 3: Code Quality, Safety & Concurrency Findings

### 3.1 Concurrency, Deadlocks & Qt Event Loop Starvation

#### 1. The `update_config()` / `self._observer.join()` Lock-Order Inversion Deadlock
- **Location**: `src/download_sorter/service.py:96-105` and `470-476`.
- **Mechanism of Failure**:
  1. The Qt main thread invokes `service.update_config()`, which acquires `self._lock` (`threading.RLock`).
  2. While holding `self._lock`, `update_config()` calls `self._restart_observer()`, which calls `self._observer.stop()` followed by `self._observer.join(timeout=3.0)`.
  3. At that exact moment, the watchdog directory observer thread (`_TopLevelEventHandler`) receives an OS filesystem event (`on_created` or `on_moved`) and calls `_enqueue()`.
  4. In `service.py:455`, `_enqueue()` attempts to acquire `self._lock`.
  5. Because `self._lock` is held by the Qt GUI thread, the watchdog worker thread blocks waiting for `self._lock` to be released.
  6. The Qt GUI thread is blocked in `join(timeout=3.0)` waiting for the watchdog thread to exit.
  7. **Result**: Both threads freeze for the full 3.0-second timeout, locking up the PySide6 UI and dropping all OS messages.
- **Architectural Solution**: Never call `Thread.join()` while holding a shared lock that the target thread requires. Extract the observer reference inside the lock, release the lock, stop and join the observer outside the lock, and re-acquire the lock to swap in the new observer:
  ```python
  def update_config(self, new_config: Config) -> None:
      with self._lock:
          old_root = self.config.resolved_watch_folder(self.paths)
          save_config(new_config, self.paths)
          self.config = new_config
          new_root = self.config.resolved_watch_folder(self.paths)
          observer_to_restart = self._observer if (self._observer is not None and new_root != old_root) else None
          if observer_to_restart is not None:
              self._observer = None

      # Stop and join OUTSIDE self._lock so in-flight watchdog callbacks can complete cleanly
      if observer_to_restart is not None:
          observer_to_restart.stop()
          observer_to_restart.join(timeout=3.0)
          self._start_observer()

      self._wake_event.set()
  ```

#### 2. Offloading `refresh_all()` and `apply_architecture_and_shift()` from the Qt Main Thread
- **Location**: `src/download_sorter/ui/main_window.py:1194-1224` and `src/download_sorter/service.py:306-382`.
- **Mechanism of Failure**:
  - `MainWindow.refresh_all()` runs synchronously on the PySide6 main event loop. For every file in Downloads, it calls `read_zone_url(path)`, opening and reading the NTFS Alternate Data Stream `Zone.Identifier`. It also synchronously runs SQLite queries for stats and recent moves.
  - `apply_architecture_and_shift()` executes full directory tree traversals, SHA-256 duplicate hashing on collisions, and file moves directly within the main GUI thread slot handler.
  - In directories with >300 files or multi-gigabyte ISOs/videos, frame rates plunge from 60 FPS to 0 FPS, triggering the Windows Desktop Window Manager `(Not Responding)` ghost window.
- **Architectural Solution**: Offload all filesystem and SQLite operations to `QThreadPool` using a generic `AsyncTask(QRunnable)` with worker signals, emitting immutable dataclass DTOs back to the GUI:
  ```python
  from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

  class WorkerSignals(QObject):
      finished = Signal(object)       # Emits immutable result DTO
      progress = Signal(int, str)     # Emits (percent, status_text)
      error = Signal(str)

  class AsyncTask(QRunnable):
      def __init__(self, fn, *args, **kwargs):
          super().__init__()
          self.fn = fn
          self.args = args
          self.kwargs = kwargs
          self.signals = WorkerSignals()

      @Slot()
      def run(self):
          try:
              result = self.fn(*self.args, **self.kwargs)
              self.signals.finished.emit(result)
          except Exception as exc:
              self.signals.error.emit(str(exc))
  ```
  - `refresh_all()` becomes non-blocking: The background task builds a `DashboardSnapshot` DTO and emits it; the main thread slot receives the DTO and updates Qt widgets in `< 2 ms`.
  - `apply_architecture_and_shift()` runs on the worker thread, reporting progress via `self.signals.progress.emit(pct, msg)` to drive a smooth progress dialog.

#### 3. Cross-Thread Communication: Signals vs. Thread-Safe Callbacks
- Keep `SorterService` 100% decoupled from PySide6 so that headless CLI runs (`DownloadSorter-cli.exe`) and test suites can run without initializing a `QApplication`.
- `SorterService` exposes a standard callback mechanism: `service.on_moves_completed = callback`.
- In `ui/main_window.py` / `ui/tray_app.py`, assign `window.moves_received.emit` to the callback. In PySide6, calling `.emit()` on a `Signal` from an arbitrary Python background thread (`sorter-v3-worker`) is natively thread-safe. When connected to a GUI widget slot, Qt's `Qt.AutoConnection` automatically marshals the event across the thread boundary into a `Qt.QueuedConnection` on the main GUI event loop.

---

### 3.2 SQLite WAL Durability & Crash Recovery Audit

#### 1. Inverted Move Sequencing & Write-Ahead Intent Pattern
- **Location**: `src/download_sorter/fs_ops.py:154-167`.
- **Mechanism of Failure**: `_move_with_retries(src, final_dest)` physically relocates the file on disk *before* `db.record_move()` is called. If the process is killed (power failure, user task kill, OS reboot, installer upgrade termination) after the file is moved but before the database transaction commits:
  - The file exists at `final_dest`.
  - SQLite `state.db` has zero record of the move.
  - The move can **never** be undone via `undo_move` or `undo_batch`.
- **Architectural Solution (Two-Phase Intent Table)**:
  - Add `status TEXT DEFAULT 'COMPLETED'` (`PENDING` | `COMPLETED` | `ROLLED_BACK`) to the `moves` table.
  - **Phase 1 (Pre-Move Intent)**: Before touching the filesystem, insert intent row:
    ```sql
    INSERT INTO moves (batch_id, filename, src_path, dest_path, status, moved_at)
    VALUES (?, ?, ?, ?, 'PENDING', ?);
    ```
  - **Phase 2 (Filesystem Move)**: Execute `safe_move(src, target)`.
  - **Phase 3 (Commit Completion)**:
    ```sql
    UPDATE moves SET dest_path = ?, status = 'COMPLETED' WHERE id = ?;
    ```
  - **Startup Crash Reconciliation (`reconcile_incomplete_moves()`)**: On `SorterService` startup, query `SELECT * FROM moves WHERE status = 'PENDING'`:
    - If `src_path` does not exist and `dest_path` exists: The move succeeded right before the crash; update status to `'COMPLETED'`.
    - If `src_path` exists and `dest_path` does not: Crash occurred before move; update status to `'ROLLED_BACK'`.
    - If both exist: Incomplete cross-volume copy; remove partial `dest_path` and update status to `'ROLLED_BACK'`.

#### 2. Robust Batch Undo Collision Guards
- During `undo_batch` (`fs_ops.py:201-218`), the destination file might have mutated or the original source location might be reoccupied:
  1. **Source Collision**: A new file with the same name was downloaded into `Downloads/file.pdf`. Blindly calling `os.replace` will destroy the new file. Check `src.exists()`; if occupied, restore as `file (Restored).pdf` or abort with `UndoConflictResult`.
  2. **Destination Locked**: Destination file is currently open in Word/Excel/Acrobat. Call `is_file_locked(dest)`. If locked, skip and return a structured summary ("Restored 4 of 5 files. 'budget.xlsx' is locked in another app").
  3. **Destination Deleted**: If user deleted the sorted file, gracefully catch `FileNotFoundError` and mark the database record as `ORPHANED` rather than failing the batch loop.

#### 3. PRAGMA synchronous = NORMAL and WAL Checkpointing
- In `src/download_sorter/db.py`, `PRAGMA synchronous = NORMAL` is currently only executed during `_init_schema()`. Subsequent connections created via `_connect()` revert to SQLite's default `FULL`, incurring unnecessary sync overhead on every commit. `PRAGMA synchronous = NORMAL` must be executed in `_connect()` on every new connection.
- **WAL Hygiene**: Long-running background tray daemons can cause `state.db-wal` to grow indefinitely.
  - Execute `PRAGMA wal_checkpoint(PASSIVE);` after every batch completion.
  - In `StateDB.close()` / application shutdown, execute:
    ```sql
    PRAGMA wal_checkpoint(TRUNCATE);
    PRAGMA optimize;
    ```
    This flushes all WAL pages into `state.db` and truncates the WAL file to 0 bytes, ensuring clean backups and preventing sharing violations during updates.

---

### 3.3 Filesystem Safety & Edge Case Audit

#### 1. File Settling Heuristic Flaw (HTTP Last-Modified Headers)
- **Location**: `src/download_sorter/fs_ops.py:47-58`:
  ```python
  newest_ts = max(st.st_mtime, st.st_ctime)
  return (cur - newest_ts) >= float(settle_seconds)
  ```
- **Vulnerability**: When Chrome, Edge, or Firefox downloads a file from an HTTP server that returns a `Last-Modified` header, the browser sets `st_mtime` to the remote server's date (often months or years in the past). When the download finishes and renames from `.crdownload`, `(cur - newest_ts)` evaluates to hundreds of days, immediately returning `True`! If antivirus or secondary background processes are still inspecting the file, `is_file_settled()` falsely declares it ready.
- **Architectural Solution**: Multi-sample size stability check. A file is settled only when:
  1. `is_file_locked(path)` returns `False` (no write handles open).
  2. The file size (`st_size`) has remained completely identical over two consecutive polls separated by at least `settle_seconds`.

#### 2. Cross-Volume Move Atomicity
- In `fs_ops.py:114-121`, cross-volume moves fall back to `shutil.move()`, which performs a byte copy followed by an unlink. If interrupted midway during a 5 GB ISO move to an external drive, a corrupt partial file remains at the destination.
- **Solution**: For cross-volume moves, copy to a hidden staging filename on the destination drive (`dest_dir/.filename.ds_part`), `fsync` the file handle, perform an atomic `os.replace` on the same volume, and finally unlink the source file.

#### 3. Cloud Storage & OneDrive Placeholder Protection
- If a user's Downloads folder is managed by OneDrive (Known Folder Move) or iCloud, reading the entire file to compute a SHA-256 hash forces Windows (`cldflt.sys`) to hydrate (download) gigabytes of cloud-only files over the network.
- **Solution**:
  - Inspect `st_file_attributes` for `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` (0x00400000) or `FILE_ATTRIBUTE_OFFLINE` (0x00001000). If set, skip content hashing and treat as cloud-managed.
  - Only compute SHA-256 when a destination filename collision actually occurs and both files have the exact same byte size (`st_size`).

#### 4. Collision Hashing Performance for Large Files
- In `fs_ops.py:92-106`, when two files have the same size, full SHA-256 hashes are computed in 1 MB chunks. For 10 GB disk images or video files, this blocks processing for 30+ seconds.
- **Solution**: Implement hierarchical tiered hashing:
  1. Quick size check (`st_size`). If different -> Not identical.
  2. Sample hash: Compute SHA-256 of the first 64 KB and last 64 KB. If different -> Not identical.
  3. Full SHA-256 hash fallback only if head and tail match.

---

### 3.4 Windows Shell & Packaging Integration Audit

#### 1. Version Drift Resolution Across Artifacts
- **Current State**: `about.py` (3.0.0), `pyproject.toml` (3.0.0), `installer.iss` (2.8.0), `CHANGELOG.md` (2.8.0).
- **Solution**: Designate `src/download_sorter/about.py` (`__version__ = "3.0.0"`) as the Canonical Single Source of Truth (SSOT).
  - Update `installer.iss` to define `#define MyAppVersion "3.0.0"`.
  - Update `CHANGELOG.md` to top release header `## [3.0.0] - 2026-11-14`.
  - Pass `/DMyAppVersion=3.0.0` from `build_release.py`.

#### 2. Preventing `ERROR_SHARING_VIOLATION` in Standalone C# Installer
- In `installer_builder.py`, the standalone installer sends `--quit` via IPC and terminates active processes. However, Windows Defender (`MsMpEng.exe`) or Windows Explorer frequently hold short-lived `FILE_SHARE_READ` handles on `.exe` and `.dll` binaries for 200–800ms after process exit.
- **Solution**: Enhance C# file extraction with:
  1. Process exit wait (`WaitForExit(3000)`).
  2. 10-attempt exponential backoff retry loop.
  3. **Windows In-Use Rename Trick**: On attempt 5, rename the locked file to `.old_<guid>` (Windows allows renaming locked binaries even when they cannot be deleted or overwritten).

#### 3. Modernizing `verify_release.py`
- Update Stage 2 to run `pytest tests_v3` by default.
- Update Stage 4 CLI dispatcher check to recognize the newly ported `--dialog` parameter.

---

## Section 4: Prioritized Action Matrix

### 4.1 P0 Blockers (Must Fix Before Public v3.0.0 Release)

| ID | Component | Vulnerability / Issue | Concrete Architectural Fix | Effort |
|---|---|---|---|---|
| **P0-1** | `service.py` | **3.0s UI Deadlock in `update_config()`**: Holds `self._lock` while calling `self._observer.join(3.0)`, deadlocking with in-flight watchdog event handlers waiting on `self._lock`. | Extract `self._observer` inside lock, set reference to `None`, release `self._lock`, call `stop()` and `join(3.0)` outside the lock, then re-acquire lock to swap in new observer. | 1 hr |
| **P0-2** | `ui/main_window.py`<br>`service.py` | **Qt Main Thread Starvation**: `refresh_all()` reads NTFS ADS streams and SQLite on GUI thread; `apply_architecture_and_shift()` hashes and moves files on GUI thread. | Wrap file simulation and folder shifting inside `AsyncTask(QRunnable)` dispatched via `QThreadPool.globalInstance()`. Emit results back via Qt signals. | 3 hrs |
| **P0-3** | `fs_ops.py`<br>`db.py` | **Post-Move Commit Inversion**: Files are moved on disk before SQLite records intent, leaving unrecoverable orphan files if process crashes mid-batch. | Implement 2-phase write-ahead WAL journaling (`PENDING` before move → `COMPLETED` after move) with startup reconciliation (`reconcile_incomplete_moves()`). | 2 hrs |
| **P0-4** | `fs_ops.py` | **Unsafe `undo_batch` Collisions**: Undoing a move when `src_path` is reoccupied by a newer file or `dest_path` is locked destroys user data or crashes. | Verify `not is_file_locked(dest)`; if `src_path` exists, restore with non-destructive `(Restored)` suffix or return `UndoConflictResult`. | 1.5 hrs |
| **P0-5** | `installer.iss`<br>`CHANGELOG.md`<br>`cli.py` | **Preflight Verification Failures**: Version drift (2.8.0 vs 3.0.0) and missing `--dialog` CLI flag cause `verify_release.py` stages 2 and 4 to fail. | Bump `installer.iss` and `CHANGELOG.md` to `3.0.0`; ensure `--dialog` is registered in `cli.py` and handled in `ipc.py`; default `verify_release.py` to `tests_v3`. | 1 hr |

---

### 4.2 P1 High Polish Items (Recommended for v3.0 GA Release)

| ID | Component | Enhancement | Concrete Implementation | Effort |
|---|---|---|---|---|
| **P1-1** | `service.py`<br>`fs_ops.py` | **Slicer Process Awareness for 3MF Hold**: Prevents moving 3MF files while Bambu Studio or OrcaSlicer has them open in memory. | Add `is_slicer_running()` via `ctypes.windll.kernel32.CreateToolhelp32Snapshot` checking for slicer EXEs; extend hold lease dynamically while active. | 2 hrs |
| **P1-2** | `installer_builder.py` | **Installer `ERROR_SHARING_VIOLATION` Resilience**: Prevents failed upgrades when Defender or Explorer briefly locks DLLs/EXEs after `--quit`. | Add 10-attempt exponential backoff retry loop + `.old_<guid>` in-use file rename fallback to C# extraction code in `installer_builder.py`. | 2 hrs |
| **P1-3** | `fs_ops.py` | **Lazy SHA-256 & Cloud File Guard**: Avoids hashing non-colliding files or hydrating OneDrive cloud-only files. | Only compute SHA-256 when destination already exists and `src.st_size == dest.st_size`. Skip files with `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS`. | 1.5 hrs |
| **P1-4** | `db.py` | **Explicit WAL Truncate on Shutdown**: Prevents unbounded `.db-wal` growth across weeks of tray daemon uptime. | Execute `PRAGMA wal_checkpoint(PASSIVE);` after batches; execute `PRAGMA wal_checkpoint(TRUNCATE); PRAGMA optimize;` in `StateDB.close()`. | 1 hr |
| **P1-5** | `clean_old.py`<br>`service.py` | **Stale Installer Auto-Cleanup**: Restores complete lifecycle parity with v2 by cleaning aged installers and zips. | Port opt-in Recycle Bin sweep (`SHFileOperationW` with `FOF_ALLOWUNDO`) for aged installers (>30 days). | 2.5 hrs |

---

### 4.3 P2 Future Roadmap (v3.1 / v3.2 Release)

| ID | Target | Feature / Subsystem | Architectural Approach |
|---|---|---|---|
| **P2-1** | v3.1 | **Desktop Sweep (`desktop_sweep.py`)** | Add opt-in secondary scheduled sweep of loose non-shortcut files from `%USERPROFILE%\Desktop` into Downloads routing pipeline. |
| **P2-2** | v3.1 | **Smart 3D Archive Unpacker (`unzip_3d.py`)** | Add post-route action hooks to automatically extract MakerWorld/Printables `.zip` bundles into model subfolders. |
| **P2-3** | v3.1 | **3MF Plate Thumbnail Extraction** | Parse `Metadata/plate_1.png` from `.3mf` ZIP headers in-memory to display 3D model previews inside side-ribbon toasts. |
| **P2-4** | v3.1 | **Projects Hub Mode (`projects_hub.py`)** | Add temporary time-boxed target override in system tray ("Route all CAD/Docs to Project Folder X for 2 hours"). |
| **P2-5** | v3.2 | **Adaptive Rule Suggestions (`learning.py`)** | Observe manual user file moves out of Downloads and surface non-intrusive "Create Rule?" suggestions in the dashboard. |

---

### 4.4 Actionable Directives for Claude Code Release Handoff

When Claude Code takes ownership to prepare the final release package in Milestone 4, execute the following implementation sequence:

1. **Fix Observer Deadlock (`src/download_sorter/service.py`)**:
   - In `update_config()` and `_restart_observer()`, move `self._observer.stop()` and `self._observer.join(timeout=3.0)` outside of `with self._lock:`.
2. **Add Win32 Slicer Process Detection (`src/download_sorter/fs_ops.py` & `service.py`)**:
   - Implement `is_slicer_running() -> bool` using `ctypes.windll.kernel32.CreateToolhelp32Snapshot(0x00000002, 0)` checking for `{"bambu-studio.exe", "orca-slicer.exe", "prusaslicer.exe", "cura.exe"}`.
   - In `service.py`, do not expire `.3mf` grace holds while `is_slicer_running()` returns `True` (up to a 14-day safety cap).
3. **Implement Two-Phase Move Journaling & Safe Undo (`src/download_sorter/db.py` & `fs_ops.py`)**:
   - Log move intent (`status='PENDING'`) before `safe_move()`, and update to `status='COMPLETED'` after. Add startup reconciliation `reconcile_incomplete_moves()` for interrupted `PENDING` records.
   - In `undo_move()` / `undo_batch()`, verify `not is_file_locked(dest)` and ensure existing files at `src_path` are never overwritten (restore as `name (Restored).ext`).
   - Add `PRAGMA wal_checkpoint(TRUNCATE);` to `StateDB.close()`.
4. **Decouple Main Thread I/O (`src/download_sorter/ui/main_window.py`)**:
   - Move `refresh_all()` disk/ADS/DB reads and `apply_architecture_and_shift()` into `QRunnable` workers dispatched via `QThreadPool.globalInstance()`, updating UI widgets strictly inside main-thread slots connected to worker signals.
5. **Complete `--dialog` CLI & IPC Wiring (`src/download_sorter/cli.py` & `ipc.py`)**:
   - Ensure `--dialog` argument is registered in `cli.py`, routed over `ipc.py`, and bound to the `quick_switcher` module.
6. **Harden C# Installer & Unify Versions (`installer_builder.py`, `installer.iss`, `CHANGELOG.md`, `verify_release.py`)**:
   - Add the 10-attempt retry loop + `.old_<guid>` rename fallback to `installer_builder.py`.
   - Bump `installer.iss` and `CHANGELOG.md` to `3.0.0`.
   - Update `verify_release.py` to default to `tests_v3`. Run `python verify_release.py` and confirm all 5 preflight stages pass cleanly (`[PASS]`).
7. **Produce Signed Binary Artifacts (`build_release.py`)**:
   - Execute `python build_release.py` to produce `dist/DownloadSorter-v3.0.0-windows-x64.zip`, `dist/DownloadSorter-Setup-v3.0.0-windows-x64.exe`, and `dist/checksums.txt`.
