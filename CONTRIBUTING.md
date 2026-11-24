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
pip install pyinstaller pytest pytest-timeout ruff
```

---

## 🧪 Running Tests

We maintain high test coverage across core sorting invariants, GUI persistence, adversarial edge cases, and simulation parity. Always run the full test suite before opening a Pull Request:

```powershell
# Run the complete test suite
python -m pytest tests_v3 -v
```

All tests should pass with 0 errors.

GitHub also runs a quick code check that catches misspelled names, leftover imports and unused variables. Run it yourself with `pip install ruff` and `ruff check .`; `ruff check . --fix` fixes most of what it finds.

The tests leave a Download Sorter that is installed or running on your PC alone: each test uses its own temporary data and Downloads folders (`tests_v3/conftest.py`), and tests that talk to the single-instance socket use a pipe name of their own. A test that starts the setup program runs it in another process, so it must pass `/NO-REGISTRY` (and `/NO-SHORTCUTS`).

Tests marked `desktop` open real Windows dialogs and need an unlocked desktop session; skip them with `pytest -m "not desktop"`. A test that runs for more than 5 minutes is stopped and the run shows where it was stuck (this needs `pytest-timeout`, installed above).

---

## 📚 Contributing New Rule Presets

Have a rule category that would help others (engineering software, audio production, photography, video editing)? Open a [rule preset request](https://github.com/The-Osprey1/Download-Sorter/issues/new?template=rule_preset_request.yml) with the words and file types it should match, or add the starter rules in `src/download_sorter/config.py` and a test in `tests_v3/test_router_and_config.py`, then run `python -m pytest tests_v3`.

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
* **Non-Destructive Operations**: Never permanently delete user files. Moves must be reversible via the undo journal (the history in `state.db`), and cleanup routines must strictly use the Windows Recycle Bin (`fs_ops.py`).
* **Safe Atomic Persistence**: Configuration writes must continue to use the 5-stage atomic commit (`fsync` + `.bak` backup + atomic rename retry loop).
