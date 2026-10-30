# Changelog

All notable changes to **Download Sorter** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
