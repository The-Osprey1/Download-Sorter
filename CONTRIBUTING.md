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
pip install pyinstaller pytest
```

---

## 🧪 Running Tests

We maintain high test coverage across core sorting invariants, GUI persistence, adversarial edge cases, and simulation parity. Always run the full test suite before opening a Pull Request:

```powershell
# Run the complete test suite
pytest tests/ -v
```

All 750+ tests should pass with 0 errors.

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

* **100% Offline**: All core functionality must remain strictly local-first with zero external network dependencies, analytics, or telemetry.
* **Non-Destructive Operations**: Never permanently delete user files. Moves must be reversible via the undo journal (`history.jsonl`), and cleanup routines must strictly use the Windows Recycle Bin (`clean_old.py`).
* **Safe Atomic Persistence**: Configuration writes must continue to use the 5-stage atomic commit (`fsync` + `.bak` backup + atomic rename retry loop).
