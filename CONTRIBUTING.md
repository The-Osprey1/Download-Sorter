# Contributing to Download Sorter

Thank you for your interest in contributing to **Download Sorter**! Whether you are fixing a bug, adding a new rule preset, optimizing classification performance, or improving documentation, we welcome your help.

---

## 🛠️ Development Setup

### Prerequisites
* Windows 10 or 11
* Python 3.10+
* Git

### Local Environment Setup
```powershell
# Clone the repository
git clone https://github.com/The-Osprey1/Download-Sorter.git
cd Download-Sorter

# Create virtual environment
python -m venv .venv

# Activate and install dependencies
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install pyinstaller pytest pytest-timeout
```

---

## 🧪 Running Tests

We maintain high test coverage across core sorting invariants, GUI persistence, adversarial edge cases, and simulation parity. Always run the full test suite before opening a Pull Request:

```powershell
# Run the complete test suite
pytest tests/ -v
```

All 750+ tests should pass with 0 errors.

The tests leave a Download Sorter that is installed or running on your PC alone: every test gets its own quit signal, and the registry entries the app writes (start with Windows, right-click menu, Installed apps) go to a scratch area that is deleted afterwards (`tests/conftest.py`, checked by `tests/test_machine_isolation.py`). A test that starts the setup program runs it in another process, which cannot be redirected, so it must pass `/NO-REGISTRY` (and `/NO-SHORTCUTS`).

Tests marked `desktop` open real Windows dialogs and need an unlocked desktop session; skip them with `pytest -m "not desktop"`. A test that runs for more than 5 minutes is stopped and the run shows where it was stuck (this needs `pytest-timeout`, installed above).

---

## 🖼️ Seeing the Windows

`tools/render_screens.py` draws every page offscreen with made-up data and saves PNG files. It never reads your own settings, history or folders.

```powershell
.\.venv\Scripts\python.exe tools\render_screens.py --out C:\temp\shots
# Only some pages, at 150% display scale and the smallest window size:
.\.venv\Scripts\python.exe tools\render_screens.py --out C:\temp\shots --only home,rules --scale 1.5 --min
```

`--min` is the smallest size the main window allows (900x560); `--compact` is 900x640, as narrow but a little taller.

Run it before and after a change to the windows, and attach the images to the pull request.

---

## 📚 Contributing New Rule Presets

Have an awesome rule category that would help others (e.g. specialized engineering software, audio production, photography, video editing)?

1. Open `config_manager.py`.
2. Locate `BUILTIN_PRESETS`.
3. Add your proposed preset following the standard schema:
   ```python
   {
       "id": "my_workflow",
       "name": "My Workflow Name",
       "category": "Domain Category",
       "description": "Short description of what files this organizes.",
       "rules": [
           {
               "name": "Descriptive Name",
               "folder": "Destination\\Subfolder",
               "match": ["keyword1", "keyword2"],
               "extensions": [".ext1", ".ext2"],
               "enabled": True,
           }
       ],
   }
   ```
4. Run `pytest tests/` to verify schema validation.
5. Submit a PR describing the target workflow!

---

## 📦 Building Standalone Releases Locally

To test the PyInstaller standalone build pipeline:

```powershell
# Build standalone Windows distribution
python build_release.py
```

The output will be placed in `dist/DownloadSorter/` and bundled into `dist/DownloadSorter-v<VERSION>-windows-x64.zip`.

---

## 🛡️ Guidelines & Code of Conduct

* **Local-first**: All core functionality must work offline, with no analytics or telemetry. The only network request is the optional once-a-day version check in `update_check.py`; don't add others.
* **Non-Destructive Operations**: Never permanently delete user files. Moves must be reversible via the undo journal (`history.jsonl`), and cleanup routines must strictly use the Windows Recycle Bin (`clean_old.py`).
* **Safe Atomic Persistence**: Configuration writes must continue to use the 5-stage atomic commit (`fsync` + `.bak` backup + atomic rename retry loop).
