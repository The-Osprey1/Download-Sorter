# CLAUDE.md — Download Sorter v3

Download Sorter v3 is a Windows single-process PySide6 desktop app (`DownloadSorter.exe`) with a companion command-line program (`DownloadSorter-cli.exe`). The application code is the `download_sorter` package in `src/`; the repository root holds the entry point (`main.py`), the version source (`about.py`) and the release tooling.

## Layout
- `src/download_sorter/`: `router.py` (the four-step routing engine), `service.py` (watcher and sorting), `fs_ops.py` (safe moves, undo), `db.py` (SQLite history), `config.py` (settings, v2 carry-over), `architecture.py` (Folder Architecture), `quick_switcher.py` (Recent File Insert), `ipc.py`, `cli.py`, `ui/` (windows, tray, ribbons).
- `tests_v3/`: the whole test suite. `conftest.py` gives every test its own data and Downloads folders and an offscreen Qt app.
- `about.py`: the one place the version is set. `src/download_sorter/about.py`, `installer.iss`, `pyproject.toml` (reads `about.py`) and the newest `CHANGELOG.md` heading must agree; `verify_release.py` checks it.
- `build_release.py`, `installer_builder.py`, `verify_release.py`, `release_notes.py`, `version_resource.py`, `suite_theme.py`, `DownloadSorter.spec`: building and checking a release.

## Commands
Use the project's virtual environment (create it with `python -m venv .venv` and `pip install -r requirements.txt pytest pytest-timeout pyinstaller ruff`):

```powershell
.\.venv\Scripts\python.exe -X faulthandler -m pytest tests_v3 -v   # tests
.\.venv\Scripts\python.exe -m ruff check .                           # code check (also runs on GitHub)
.\.venv\Scripts\python.exe main.py --version
.\.venv\Scripts\python.exe main.py --simulate                      # dry run on throwaway folders
.\.venv\Scripts\python.exe verify_release.py                       # release preflight
.\.venv\Scripts\python.exe build_release.py                        # build dist```

## Rules for changes
- Never delete a user's file; moves are written to the history first and can be undone.
- Anything that waits on the disk or the network stays off the Qt main thread (see `ui/workers.py`).
- The only network request is the once-a-day version check in `update_check.py`.
- Releasing: pushing a `v*` tag runs `.github/workflows/release.yml`, which builds and publishes the release. Ask before pushing a tag.
