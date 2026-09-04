# download-sorter

Lightweight auto-sorter for the **top of your Downloads folder**, plus a sweep of **Windows Documents**.

Downloads files are moved into a premade folder tree *inside Downloads*. Files already in those folders are left alone.

Targeted CAD and 3D files (e.g. `.stl`, `.step`, `.3mf`, etc.) found under Windows Documents are moved into the Downloads tree (for example `Downloads\3D Models`). Application, game directories (like `SOLIDCast`, `My Games`, `Steam`), and OneDrive cloud placeholders are automatically skipped.

## Folder tree

```
Downloads/
  3D Models/STL, 3MF, OBJ, STEP, Print files, Other, CAD
  Documents/School, PDFs, Word, Text
  Spreadsheets
  Images
  Videos
  Audio
  Archives
  Extracted
  Installers
  Code
  Other
```

Keyword rules run first (for example a PDF named `transcript` goes to `Documents\School`, not `Documents\PDFs`). Edit `config.json` to change mappings.

## Usage

### Run once (or simulate)

```powershell
# Preview what would move without changing anything
.\.venv\Scripts\python.exe sort_once.py --dry-run

# Run the sorter now
.\.venv\Scripts\python.exe sort_once.py
```

### Undo recent moves

Restore moved files back to their original locations:

```powershell
# Undo the most recent batch
.\.venv\Scripts\python.exe sort_undo.py

# Preview undo without moving
.\.venv\Scripts\python.exe sort_undo.py --dry-run

# List recent moves
.\.venv\Scripts\python.exe sort_undo.py --list
```

### Pause / Resume sorting

```powershell
# Toggle pause state
.\.venv\Scripts\python.exe toggle_pause.py

# Check status
.\.venv\Scripts\python.exe toggle_pause.py --status
```

### Sort any specific folder

```powershell
.\.venv\Scripts\python.exe sort_once.py --target "C:\path\to\folder"
```

## Power Tools & Companion Utilities

### 1. 3D Print / Slicer File Inspector (`gcode_peek.py`)
Quickly inspect `.3mf`, `.gcode`, and `.bgcode` files from the terminal or right-click:

```powershell
# Inspect latest 3D file in Downloads
.\.venv\Scripts\python.exe gcode_peek.py --latest

# Inspect a specific file
.\.venv\Scripts\python.exe gcode_peek.py "path\to\model.3mf"

# Extract and view embedded thumbnail render
.\.venv\Scripts\python.exe gcode_peek.py "path\to\model.3mf" --thumb
```

### 2. 3D Zip Flattener (`unzip_3d.py`)
Unpack MakerWorld / Printables / Thingiverse archives without nested folder clutter:

```powershell
# Unpack a specific 3D model zip
.\.venv\Scripts\python.exe unzip_3d.py "path\to\model.zip"

# Unpack all 3D model zips sitting in Downloads
.\.venv\Scripts\python.exe unzip_3d.py --all
```

### 3. General Auto-Unzipper (`unzip_archive.py`)
Automatically or manually unpack `.zip` archives into isolated landing folders under `Downloads\Extracted\<archive_name>\` while moving original archives safely to `Downloads\Archives`:

```powershell
# Unpack a specific zip archive
.\.venv\Scripts\python.exe unzip_archive.py "path\to\archive.zip"

# Preview extraction without touching files
.\.venv\Scripts\python.exe unzip_archive.py "path\to\archive.zip" --dry-run

# Unpack all zip archives currently sitting at the top of Downloads
.\.venv\Scripts\python.exe unzip_archive.py --all

# Extract into a custom landing directory
.\.venv\Scripts\python.exe unzip_archive.py "path\to\archive.zip" --dest "C:\path\to\folder"
```

* **Smart Root-Stripping:** Intelligently strips redundant single top-level folders inside the zip to avoid duplicate nested folders like `Extracted\my_archive\my_archive\...`.
* **Zip Slip Defense:** Rejects path traversal (`../`) attacks to safeguard your filesystem.
* **Auto-Sorting Integration:** When the background watcher (`watch.py`) is running and `auto_unzip` is enabled in `config.json`, newly downloaded `.zip` files are automatically extracted and archived.

### 4. Safe Janitor (`clean_old.py`)
Clean out old installers and archives by sending them to your **Windows Recycle Bin** (never permanent deletion):

```powershell
# Preview what would be recycled (older than 30 days)
.\.venv\Scripts\python.exe clean_old.py --dry-run

# Run cleanup for files older than 60 days
.\.venv\Scripts\python.exe clean_old.py --days 60
```

### 4. Windows File Explorer Context Menu Shortcuts
Add right-click options in Windows Explorer (installed in user profile, no admin needed):

```powershell
# Install right-click shortcuts
powershell -ExecutionPolicy Bypass -File .\install_context_menu.ps1

# Remove right-click shortcuts
powershell -ExecutionPolicy Bypass -File .\uninstall_context_menu.ps1
```

* Right-click any folder $\to$ **"Sort with DownloadSorter"**
* Right-click folder background $\to$ **"Undo Last Sort"**
* Right-click `.3mf` / `.gcode` $\to$ **"Inspect 3D Print Info (gcode-peek)"**
* Right-click `.zip` $\to$ **"Extract 3D Models to Downloads"**

### 5. Interactive Top-Right Notification Ribbon (`ribbon_popup.py`)
Whenever an incoming download lands and organizes into its target folder, a sleek notification card pops up at the top-right of your screen:
* **One-Click Reveal:** Click anywhere on the ribbon to open Windows File Explorer with that exact file highlighted.
* **Vertical Stacking:** Simultaneous downloads automatically stack neatly down the right margin without overlapping.
* **Per-Monitor High-DPI:** Crystal-sharp subpixel typography matching Windows scaling.
* **Dynamic Category Accents:** Colors adjust to file types based on the **Devyn Personal Suite** theme tokens (`suite_theme.py`).
* **Test ribbon manually:**
  ```powershell
  .\.venv\Scripts\python.exe ribbon_popup.py --test
  ```

## Background Watch + Auto-start at Login

```powershell
# Install and start background task
powershell -ExecutionPolicy Bypass -File .\install_startup.ps1

# Stop and remove background task
powershell -ExecutionPolicy Bypass -File .\uninstall_startup.ps1
```

* Logs: `%LOCALAPPDATA%\download-sorter\sorter.log` (automatically rotated)
* History: `%LOCALAPPDATA%\download-sorter\history.jsonl`


