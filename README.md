# Download Sorter

> **Made by Devyn Fidel (2026)** · v2.0.0 · [MIT License](LICENSE)

[![Release](https://img.shields.io/github/v/release/The-Osprey1/Download-Sorter?color=69E6AD&logo=github)](https://github.com/The-Osprey1/Download-Sorter/releases)
[![CI](https://github.com/The-Osprey1/Download-Sorter/actions/workflows/ci.yml/badge.svg)](https://github.com/The-Osprey1/Download-Sorter/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB.svg?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-0078D6.svg?logo=windows&logoColor=white)](https://microsoft.com/windows)
[![Author: Devyn Fidel](https://img.shields.io/badge/author-Devyn%20Fidel-69E6AD.svg)](https://github.com/The-Osprey1/Download-Sorter)
[![Tests Passing](https://img.shields.io/badge/tests-1064%20passed-69E6AD.svg?style=flat)](tests/)

A lightweight, local-first automated file organizer for Windows that keeps your **Downloads** and **Documents** folders organized without sending your data to any cloud service.

Includes a **PySide6 Visual Rules Manager & Machine Remodel Wizard**, **Curated Rule Presets Library**, **3-Path Batch File Handoff Quick Switcher**, **3MF Grace-Period Hold for Bambu Studio**, **Staged Machine Remodel Engine**, **System Tray monitor**, **interactive High-DPI Desktop Ribbon toasts**, **reversible Undo**, and **3D Printing / CNC intelligence**. Available both as a **standalone Windows executable (no Python required)** and as an extensible Python package.

---

## 1-Click Download & Setup (Recommended)

For everyday users on Windows 10 & 11 (zero Python installation or terminal commands required):

1. **Download the Setup Executable**:
   Get the latest single-file installer: [`DownloadSorter-Setup-v2.0.2-windows-x64.exe`](https://github.com/The-Osprey1/Download-Sorter/releases/latest).
2. **Run the Installer**:
   Double-click the setup `.exe`.
   - Installs cleanly into `%LOCALAPPDATA%\Programs\DownloadSorter` (no Administrator privileges required).
   - Automatically creates Desktop and Start Menu shortcuts.
   - Registers in Windows Installed Apps for seamless 1-click uninstallation.
3. **Enjoy Automated Organization**:
   The interactive 3-step Welcome Wizard launches automatically on completion so you can choose your persona profile and set your filing preferences.

*Unattended / Silent deployment:* Run `DownloadSorter-Setup-v2.0.2-windows-x64.exe /S` (supports optional `/D=C:\Custom\Path`).

---

## Folder Architecture

Newly downloaded files are routed into an organized tree directly inside your Downloads folder:

```text
Downloads/
  ├── 3D Models/
  │     ├── MakerWorld/ (<Author>/<Model>/<Plate>.3mf)
  │     ├── STL/
  │     ├── 3MF/
  │     ├── OBJ/
  │     ├── STEP/
  │     ├── CAD/ (F3D, SLDPRT, IPT, DWG, DXF)
  │     └── Other/
  ├── Documents/
  │     ├── School/ (Course-specific keywords, syllabi, homework)
  │     ├── PDFs/
  │     ├── Word/
  │     └── Text/
  ├── Spreadsheets/ (XLSX, CSV, ODS)
  ├── Images/ (JPG, PNG, WebP, SVG, HEIC)
  ├── Videos/ (MP4, MKV, MOV, WebM)
  ├── Audio/ (MP3, WAV, FLAC, AAC)
  ├── Archives/ (Original ZIP, 7Z, RAR)
  ├── Extracted/ (Unpacked landing folders)
  ├── Installers/ (EXE, MSI, MSIX)
  ├── Code/ (PY, JS, TS, CPP, JSON, HTML, CSS, Haas/GCode)
  └── Other/ (Uncategorized fallback)
```

Targeted CAD and 3D files (e.g. `.stl`, `.step`, `.3mf`) found in Windows Documents are safely organized into the Downloads 3D tree, while application folders (like `OneDrive`, `Steam`, `My Games`, `SOLIDCast`, `Adobe`) and OneDrive cloud placeholders are automatically protected and skipped.

---

## Key Features in Version 2.0

### 1. Visual Rules Manager & Remodel Wizard (`rules_manager.py`)
A sleek dark-mode desktop GUI built with **PySide6** and styled to the **Devyn Personal Suite** (`suite_theme.py`) design tokens:
* **3 Dedicated Tabs**:
  1. **Keyword Rules**: Reorder, add, edit, or disable keyword-based routing with drag-and-drop precedence.
  2. **Extension Rules**: Organize file extensions into category folders.
  3. **Machine Remodel**: Interactive folder cards to scan and remodel messy machine architectures with **Organize**, **Keep intact**, and **Skip** actions.
* **Live Sandbox & Real-time Simulator**: Type any hypothetical filename (e.g. `ME3210_Exam_Review.pdf`, `bracket_op1.nc`, `vase_multicolor.3mf`) to watch the rule engine evaluate the match chain in real time, showing the winning rule, matched trigger, destination path, and classification badge.
* **Curated Rule Presets Library**: Browse and 1-click import preset packs for School, Office, CAD/CAM, and Dev setups.
* **Temporary "Active Project" Catch-All**: Route all incoming CAD, 3D models, and CNC programs directly to your active project directory with optional auto-expiry.
* **Safe Atomic Persistence**: Changes are verified with schema validation, written with `fsync`, backed up to `config.json.bak`, and replaced atomically.

```powershell
# Launch the Visual Rules Manager GUI
download-sorter-gui
# Or open directly to the Remodel tab
download-sorter-gui --tab remodel
```

### 2. 3-Path Batch File Handoff Quick Switcher (`quick_switcher.py`)
Downloading multi-part models, document batches, or asset sets? The Quick Switcher coalesces downloads arriving within a 25-second window:
* **Floating Switcher Pill**: Docks an unobtrusive floating pill directly beside any active Windows File Open / Upload dialog.
* **3 Handoff Paths**:
  * **`↵ Latest`** (`Ctrl+G`): Instantly injects the most recent file into the dialog.
  * **`⇉ All N`** (`Ctrl+Shift+G`): Injects the entire batch simultaneously using hidden `.batch_stage` NTFS hardlinks (no disk duplication).
  * **`⧉ Copy`** (`Ctrl+Shift+C`): Copies all batch files to the Windows clipboard as a native `CF_HDROP` file list for immediate pasting with `Ctrl+V`.
* **Native OLE Drag & Drop**: Drag the pill or individual files directly into CAD, slicers, or Explorer folders.
* **Interactive Hover Checklist**: Hover over the pill to untick unwanted files before committing the handoff.

### 3. 3MF Grace-Period Hold & MakerWorld Intelligence (`hold_manager.py`)
Moving `.3mf` files immediately often breaks Bambu Studio's in-app shortcuts, recent projects, and `Ctrl+S` incremental saves. Version 2 solves this:
* **7-Day Idle Hold**: Incoming `.3mf` files remain in `Downloads\` for up to 7 days without relocation.
* **Slicer Process Awareness**: Checks if Bambu Studio, OrcaSlicer, or PrusaSlicer are running or holding file locks; holds are extended up to 14 days while actively in use.
* **Desktop Ribbon Thumbnail Preview**: Parses and displays the embedded plate preview thumbnail inside the ribbon banner, with **`File now`** and **`Keep here`** buttons.
* **Empirical MakerWorld Detection**: Secure `defusedxml` parser distinguishes MakerWorld downloads from local saves, routing models to `3D Models\MakerWorld\<Author>\<Model>\<Plate>.3mf`.
* **System Tray Indicator**: Real-time tray badge showing currently held 3MF counts and remaining hold duration.

### 4. Staged Machine Remodel Engine (`remodel.py`)
Safely remodel old or messy PCs with chaotic file structures:
* **Deep Engineering & Simulation Protections**: Automatically identifies and leaves intact:
  * **SOLIDCast**: `.fsimodel9`, `.fsimodel*`, `.afs`, `.gdproj`, `.fsimaterials`, `FLOWCast.set`, `$mesh*.out`, `.abin`, `inuse.lok`.
  * **MAGMASOFT**: `.magma`, `.magmainteract`, simulation output directories.
  * **CAD Assemblies**: SolidWorks, Inventor, Fusion 360, STEP, IGES files.
  * **Electronics**: KiCad (`.kicad_*`), Arduino (`.ino`, `.pde`).
  * **Dev Repositories**: `.git`, `.venv`, `node_modules`, `build`, `dist`.
  * **Active Folders**: Folders modified within 14 days.
  * **Explicit Keep Marker**: Any folder containing `.download-sorter-keep`.
* **3-Stage Deduplication**: File size grouping -> 64KB fast hash -> full SHA-256 + NTFS inode verification.
* **30-Day Quarantine**: Moves duplicates to `<root>\_Remodel Quarantine\remodel_<id>\Duplicates\`.
* **HTML Receipt & 1-Click Undo**: Generates a self-contained, searchable `Where did my files go.html` in the remodeled root, and supports instant reversal via `download-sorter-remodel --undo`.

```powershell
# Scan a folder and preview candidate cards
download-sorter-remodel --scan "C:\Users\<user>\Downloads"

# Dry run simulation
download-sorter-remodel --dry-run "C:\Users\<user>\Downloads"

# Apply remodel safely
download-sorter-remodel --apply "C:\Users\<user>\Downloads"

# Revert a remodel completely
download-sorter-remodel --undo
```

### 5. Low-Resource Background Daemon & System Tray (`tray.py`)
* **Background Watchdog**: Watches Downloads and Documents using low-CPU native event hooks.
* **Single-Instance Mutex**: Prevents multiple daemon instances from colliding.
* **Instant Tray Control**: Right-click the tray icon to pause/resume sorting, trigger manual sorts, undo recent moves, remodel messy folders, or open rules.
* **Live Daily Counter**: Displays files sorted today and active held 3MF models.

```powershell
# Run the tray monitor (background watcher lives inside)
download-sorter-tray
```

### 6. Desktop Ribbon Toast Notifications (`ribbon_popup.py`)
* **One-Click Reveal**: Click the notification pill to highlight sorted files in Windows File Explorer.
* **3MF Plate Previews**: Embedded thumbnails with quick **File now** and **Keep here** actions.
* **Vertical Dynamic Stacking**: Stacks simultaneous downloads neatly down the right margin.
* **Per-Monitor High-DPI Awareness**: Crystal-clear typography on all display scaling levels.

### 7. 3D Print Inspector & CNC Machine Sniffer (`gcode_peek.py`)
* **Haas vs 3D Detection**: Inspects initial lines of ambiguous toolpaths (`.nc`, `.tap`, `.gcode`, `.bgcode`) to differentiate Haas CNC machine programs from 3D slicer outputs.
* **Embedded Thumbnail Extraction**: Pulls embedded preview thumbnails from `.3mf` and `.gcode` slices.

```powershell
# Inspect latest 3D file in Downloads
gcode-peek --latest

# View embedded thumbnail render
gcode-peek "path\to\model.3mf" --thumb
```

### 8. Smart Archive Unpackers (`unzip_3d.py` & `unzip_archive.py`)
* **3D Zip Flattener**: Unpack MakerWorld, Printables, and Thingiverse archives without nested wrapper folders.
* **Zip Slip Defense**: Strictly validates extraction paths to prevent directory traversal (`../`) attacks.

```powershell
# Unpack all 3D model zips sitting in Downloads
unzip-3d --all

# Extract general archive to Extracted\
unzip-archive "path\to\archive.zip"
```

### 9. Write-Ahead Journal & Crash Recovery (`safe_ops.py`)
* **Atomic 2-Phase Moves**: All file moves log write-ahead `planned` and `done` journal records with disk `fsync`.
* **Automatic Recovery**: On startup, orphaned `planned` records reconcile against real disk state.
* **Non-Overwriting Moves**: Existing targets automatically resolve to `name (2).ext` collisions.

---

## Installation & Setup

### Option 1: Standalone Windows App (No Python Required)
1. Download the latest **`DownloadSorter-v2.0.0-windows-x64.zip`** from [GitHub Releases](https://github.com/The-Osprey1/Download-Sorter/releases).
2. Extract the ZIP anywhere on your system (e.g. `C:\Users\<user>\Download-Sorter` or Program Files).
3. Double-click **`setup.bat`** (or run `.\setup.bat`). It will:
   - Configure optional automatic startup at login.
   - Configure right-click Explorer context menus.
   - Offer an optional scan of your Downloads folder to preview remodeling.
   - Launch the background system tray monitor immediately.

---

### Option 2: Python Source Installation (For Developers)

#### Quick Start (One-Click)
Double-click **`setup.bat`** (or run `.\setup.bat` in PowerShell/CMD). It will automatically:
1. Detect Python 3.10+ (supporting `python`, official `py -3` launcher, `python3`, or offer `winget` installation)
2. Create your `.venv` virtual environment
3. Install dependencies from `requirements.txt`
4. Configure optional auto-start at login and Explorer context menus
5. Launch the system tray icon

---

### Updating to the Latest Version (One-Click)
Double-click **`update.bat`** (or run `.\update.bat` in PowerShell/CMD). It will:
1. Safely stop running background daemon and tray instances
2. Pull the latest commits from GitHub (`git pull`)
3. Synchronize any updated dependencies inside `.venv`
4. Restart the system tray application automatically

*(Because custom rules are stored safely in `%LOCALAPPDATA%\download-sorter\config.json`, updating will never conflict with or erase your personalized sorting rules).*

---

### Manual Setup (CLI / Pip)
```powershell
git clone https://github.com/The-Osprey1/Download-Sorter.git
cd Download-Sorter

# Create virtual environment
python -m venv .venv

# Install editable package with dependencies
.\.venv\Scripts\pip.exe install -e .
```

### Windows Explorer Right-Click Shortcuts (Optional)
Add convenient right-click options in File Explorer (installed for the current user, no Administrator privileges required):

```powershell
powershell -ExecutionPolicy Bypass -File .\install_context_menu.ps1
```

* **Folder right-click**: `"Sort with DownloadSorter"`
* **Folder background right-click**: `"Undo Last DownloadSorter Batch"`
* **`.3mf` / `.gcode` right-click**: `"Inspect 3D Print Info (gcode-peek)"`
* **`.zip` right-click**: `"Extract 3D Models to Downloads"`

To remove the context menu entries:
```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_context_menu.ps1
```

### Auto-Start at Login (Optional)
To register Download Sorter as a persistent Windows Scheduled Task that starts at user logon:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_startup.ps1
```

To remove the scheduled task:
```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_startup.ps1
```

---

## Command Line Usage

Download Sorter installs dedicated CLI console scripts on PATH:

| Command | Action |
|:---|:---|
| `download-sorter` | Run one-pass sorting batch on Downloads |
| `download-sorter --dry-run` | Simulate sorting without moving files |
| `download-sorter --target <dir>` | Sort a specific directory once |
| `download-sorter-watch` | Run foreground directory watcher daemon |
| `download-sorter-tray` | Launch system tray monitor with background daemon |
| `download-sorter-gui` | Launch visual Rules Manager & Live Simulator GUI |
| `download-sorter-gui --tab remodel` | Open Rules Manager directly to Machine Remodel tab |
| `download-sorter-remodel --scan` | Scan folder and preview candidate remodel cards |
| `download-sorter-remodel --dry-run` | Simulate machine remodel actions and output summary |
| `download-sorter-remodel --apply` | Execute safe machine remodel with HTML receipt |
| `download-sorter-remodel --undo` | Reversibly undo previous machine remodel |
| `download-sorter-undo` | Reversibly undo previous download sorting batch |
| `gcode-peek <file>` | Inspect 3D print file / CNC toolpath |
| `gcode-peek --latest` | Inspect most recent 3D file in Downloads |
| `unzip-3d <zip>` | Flatten and unpack 3D models from archive |
| `unzip-archive <zip>` | Safely unpack archive into `Extracted/` |
| `python status.py` | Print daemon health, memory, held files, and recent moves |
| `python clean_old.py` | Recycle old installers/archives (>30 days) to Recycle Bin |
| `<any-command> --version` | Print Download Sorter version and attribution credit |
| `<any-command> --help` | Display command usage and option details |

---

## Testing & Quality Assurance

This repository includes a comprehensive 4-Tier test suite covering feature tests, boundary cases, cross-feature interactions, visual design consistency, and real-world stress scenarios:

```powershell
# Run the complete test suite (1,064 tests)
.\.venv\Scripts\pytest.exe

# Run visual design consistency tests only
.\.venv\Scripts\pytest.exe tests/test_visual_consistency.py -v

# Run v2 E2E acceptance tests only
.\.venv\Scripts\pytest.exe tests/test_e2e_v2_features.py -v
```

---

## Safety & Privacy

* **100% Offline**: Zero external network requests, analytics, or telemetry. All sorting occurs entirely on your local machine.
* **Non-Destructive Cleanup**: Clean scripts send items strictly to the Windows Recycle Bin using native Windows Shell API (`SHFileOperationW` with `FOF_ALLOWUNDO`).
* **Path Traversal Protection**: Archive extractors and 3MF parsers reject any archive member with directory traversal markers (`../` or leading root slashes).
* **Write-Ahead Journaling**: Safe 2-phase move operations with crash recovery prevent data loss during power outages or unexpected interruptions.
* **Atomic Configuration**: Config persistence creates `.bak` backups before swapping files to guarantee zero configuration loss if interrupted.

---

## Frequently Asked Questions (FAQ)

### Windows SmartScreen displays a warning when running `DownloadSorter.exe`. Is it safe?
Yes. SmartScreen flags newly released binaries from GitHub that have not yet built up download reputation or are not signed with a paid Extended Validation (EV) code signing certificate. To proceed:
1. Click **More info**.
2. Click **Run anyway**.
The application is 100% open-source, offline, and makes zero network requests. You can inspect the source code and build it locally from scratch at any time.

### Where are my custom rules saved?
Your personal rule configurations, custom keywords, and category folders are stored in:
`%LOCALAPPDATA%\download-sorter\config.json`
This keeps your settings strictly isolated from the application directory. Updating the application or pulling new commits will never overwrite your personal rules.

### How do I undo a sort batch?
If a batch was sorted into the wrong place, you can immediately revert it:
* **Via System Tray**: Right-click the Download Sorter icon and click **"Undo last batch"**.
* **Via File Explorer**: Right-click the background of any folder and click **"Undo Last DownloadSorter Batch"**.
* **Via Command Line**: Run `download-sorter-undo` (or `python sort_undo.py`).

### Does Download Sorter support OneDrive or moved Downloads folders?
Yes! Download Sorter automatically checks Windows Shell Known Folders (`FOLDERID_Downloads` registry key) to detect your active Downloads folder even if you redirected it to another drive (e.g. `D:\Downloads`). For OneDrive, cloud-only placeholder files (smart sync) are automatically detected via Windows file attributes and skipped until fully downloaded locally.

---

## Contributing & Security

* **Contributing**: Check out [CONTRIBUTING.md](CONTRIBUTING.md) for local development instructions, test guidelines, and how to submit new rule presets.
* **Security & Privacy**: Read our [SECURITY.md](SECURITY.md) policy detailing our zero-telemetry and offline security model.
* **Changelog**: See [CHANGELOG.md](CHANGELOG.md) for version release history.

---

## Credits

**Download Sorter** was created by **Devyn Fidel** (2026).  
Repository: https://github.com/The-Osprey1/Download-Sorter

Every surface that shows credit (tray tooltip and menu, `status`, the Rules Manager footer and *About* dialog, every CLI's `--version`, `setup.bat`, `update.bat`, `install_context_menu.ps1`, `install_startup.ps1`) reads it from the single `about.py` module, so the credit line is always identical.

---

## License

This project is licensed under the [MIT License](LICENSE) - Copyright (c) 2026 Devyn Fidel.
