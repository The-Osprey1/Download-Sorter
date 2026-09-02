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

## Background Watch + Auto-start at Login

```powershell
# Install and start background task
powershell -ExecutionPolicy Bypass -File .\install_startup.ps1

# Stop and remove background task
powershell -ExecutionPolicy Bypass -File .\uninstall_startup.ps1
```

* Logs: `%LOCALAPPDATA%\download-sorter\sorter.log` (automatically rotated)
* History: `%LOCALAPPDATA%\download-sorter\history.jsonl`

