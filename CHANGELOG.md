# Changelog

All notable changes to **Download Sorter** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.0.0] - 2026-11-03

### Added
* **Full Single-Source Attribution**:
  * Unified author credit (`Made by Devyn Fidel (2026)`) defined exclusively in `about.py`.
  * Wired into all 18 CLI entry points (`--version`), setup and install scripts (`setup.bat`, `update.bat`, `install_context_menu.ps1`, `install_startup.ps1`), GUI footer, system tray menu/tooltips, generated receipts, and packaging metadata.
* **3-Path Batch File Handoff Quick Switcher (`batch_handoff.py`, `quick_switcher.py`)**:
  * Coalesces incoming download bursts arriving within 25 seconds into an atomic batch.
  * Floating multi-button switcher pill offering:
    * `? Latest` (`Ctrl+G`): Direct injection of the most recent file into Windows File Open/Upload dialogs.
    * `? All N` (`Ctrl+Shift+G`): Simultaneous multi-file dialog injection via hidden `.batch_stage` NTFS hardlinks without copying disk blocks.
    * `? Copy` (`Ctrl+Shift+C`): Native `CF_HDROP` clipboard file-list copying for instant `Ctrl+V` pasting.
  * Native Windows OLE Drag-and-Drop from the pill into CAD, slicers, or file managers.
  * Interactive hover checklist allowing users to untick unwanted files before committing handoff.
* **3MF Grace-Period Hold & MakerWorld Overhaul (`hold_manager.py`, `ribbon_popup.py`, `gcode_peek.py`)**:
  * 7-day idle hold preserving incoming `.3mf` files in `Downloads\` without premature sorting, keeping Bambu Studio shortcuts, recent projects, and `Ctrl+S` incremental saves intact.
  * Active slicer lock detection extending holds up to 14 days while Bambu Studio, OrcaSlicer, or PrusaSlicer are open.
  * Desktop Ribbon Toast preview displaying extracted plate thumbnails up to 1024x1024 with interactive **File now** and **Keep here** actions.
  * Hardened `defusedxml` parser guarding against entity expansion and directory traversal attacks.
  * Empirical MakerWorld project routing to `3D Models\MakerWorld\<Author>\<Model>\<Plate>.3mf`.
  * Dynamic System Tray held items badge displaying count and time remaining.
* **Staged Machine Remodel Engine (`remodel.py`, `rules_manager.py`)**:
  * Safe file system remodeling for chaotic PCs and legacy folders via CLI (`download-sorter-remodel`) and Rules Manager 3rd tab.
  * Deep protections for engineering and simulation data: SOLIDCast (`.fsimodel9`, `.afs`, `.gdproj`, `FLOWCast.set`), MAGMASOFT (`.magma`, simulation trees), KiCad, Arduino, CAD assemblies, developer environments, Git repos, active folders (<14d), and `.download-sorter-keep`.
  * 3-stage deduplication (size -> 64KB fast hash -> full SHA-256 + NTFS inode verification).
  * 30-day quarantine (`_Remodel Quarantine\remodel_<id>\`) and searchable `Where did my files go.html` receipt.
  * 1-Click Undo reversing all remodel operations via write-ahead journal with quarantine file preservation.
* **Write-Ahead Journaling & Automatic Crash Recovery (`safe_ops.py`)**:
  * 2-phase move operations logging `planned` and `done` states with disk `fsync`.
  * Startup crash recovery reconciling orphaned operations across all daemon and CLI entry points.
* **Visual Design Token Compliance (`suite_theme.py`)**:
  * Strict alignment across all UI surfaces, tables, pills, dialogs, and HTML receipts with Devyn Personal Suite design tokens.
  * Zero forbidden off-palette hex codes.

---

## [1.0.0] - 2026-10-30

### Added
* **PySide6 Visual Rules Manager & Live Simulator GUI**:
  * Real-time drag/reorder precedence hierarchy.
  * Instant Live Sandbox evaluating arbitrary test filenames with winning rule, match trigger, and target destination preview.
  * Temporary Active Project Catch-All mode for CAD/3D/CNC files with auto-expiration.
  * Curated **Rule Presets Library** covering Office/Finance, Student Coursework, 3D Printing/Maker, Software Dev, and Creative Media.
  * Standalone JSON Rule **Export** and **Import** (Merge or Replace).
  * Factory defaults restore with confirmation safeguards.
* **Low-Resource Background Daemon & System Tray Companion (`tray.py`)**:
  * Windows single-instance mutex preventing accidental multi-process collisions.
  * Real-time daily file move counter.
  * Instant context menu: Pause/Resume, Sort Downloads Now, Undo Recent Batch, Configure Rules, and Open Downloads/Logs.
* **Per-Monitor High-DPI Desktop Ribbon Notifications (`ribbon_popup.py`)**:
  * Anti-aliased 60fps toast notifications with category-specific color accents (Mint for 3D/CAD, Blue for Documents, Red for CNC/G-Code).
  * 1-Click File Explorer reveal.
  * Dynamic vertical stacking for simultaneous batch downloads.
  * Multi-monitor edge clamping and auto-dismiss on file interaction.
* **Recency-Gated Quick Switcher (`quick_switcher.py`)**:
  * Automatic Common Item Dialog sniffer detecting active File Open/Upload dialogs in browsers, Slack, Discord, slicers, and CAD within 5 minutes of downloading.
  * Floating interactive pill with 1-click "Select" or `Ctrl+G` file path injection.
* **Reversible Undo Engine (`sort_undo.py`)**:
  * Full rollback of sorting batches using append-only `%LOCALAPPDATA%\download-sorter\history.jsonl` audit log.
* **Safe Janitor Cleanup (`clean_old.py`)**:
  * Non-destructive recycling of aging installers and archives directly to the Windows Recycle Bin.
* **3D Printing & CNC Intelligence (`gcode_peek.py`, `unzip_3d.py`)**:
  * Smart content sniffer distinguishing Haas CNC machine programs (`G90 G54`) from slicer layers (`.3mf`, `.stl`, `.gcode`, `.bgcode`).
  * Embedded thumbnail extraction for `.3mf` and slicer files.
  * Flat unpacking of nested 3D model archives from Thingiverse, MakerWorld, and Printables with Zip Slip path traversal defense.
* **Zero-Python Standalone Windows Executable Distribution**:
  * PyInstaller packaging script (`build_release.py`) and spec file (`DownloadSorter.spec`) compiling the entire application into a self-contained Windows x64 distribution (`DownloadSorter.exe`).
  * Automated GitHub Actions release pipeline (`.github/workflows/release.yml`) generating checksum-verified release ZIP archives on version tags.
  * Smart `setup.bat` with automatic Python launcher detection (`py -3`, `python`, `python3`) and automated `winget` installation fallback.
  * Complete clean uninstallation suite (`uninstall.bat` and `uninstall.ps1`).
