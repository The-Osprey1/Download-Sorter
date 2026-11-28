# Download Sorter v4 — Handoff

**Updated:** 2026-11-28
**Branch:** `main` carries v4 (PR #92 merged on 2026-11-28). The last v3 release is the `v3.6.1` tag.
**Version:** `4.0.0-beta.3`
**Checks:** `ruff check .`, `pytest tests_v3 -q`, `build_release.py`, `verify_release.py` must stay green. On Linux about 76 tests fail because they need Windows; compare against `main` there, never against a fixed number.

## 1. What the owner wants

> V4 must be easy to use, fluid, and intuitive, like an Apple or Windows 11 app. Holds and grouping are opt-in only. Folder setup is interactive: you design the folders and watch it work.

The owner approved a four-phase plan to v4.0 on 2026-11-28 ("Download Sorter v4.0 launch plan" Claude Doc). It is based on the research report in the project files (`research/Download Sorter improvement ideas.md`).

| Phase | Ships as | What it does | State |
|---|---|---|---|
| 1. Clean up | `4.0.0-beta.3` | Remove the v3 screens, one name per feature, fix dead settings, update these notes | Done in this version |
| 2. Polish | `4.0.0-beta.4` | Live check/cross feedback in the tester, folder counts and summaries in the tree, drag preview, helpful empty folders, a permanent "Undo last sort", teach confirms first, check Sorty/guide/installer/buttons/Watched folders, follow Windows light or dark | Next |
| 3. Release candidate | `4.0.0-rc.1` | Code signing (Azure Artifact Signing; the owner sets up the account), winget listing, v4 README/beta guide/flowchart with screenshots, one-week test on the owner's PC | Not started |
| 4. Release | `4.0.0` | Version parity, check a v3.6.1 install upgrades cleanly, tag only with the owner's yes | Not started |

After 4.0: Chrome Web Store listing, notification buttons, Do Not Disturb awareness, a "Reclaim space" page, OneDrive safety, smarter suggestions for unmatched files.

## 2. How the app is laid out now

- **One window, three tabs:** Folders, Activity, Settings.
- **Folders** (`ui/folders/folders_tab.py`) is the only place folders are designed. Every edit goes through `folder_designer.py`, which returns a `Change` or raises `DesignerError` with a message for the person.
  - Folder ⋯ menu: rename, move, combine, remove, **Find projects inside** (`project_detect.detect_projects_in_folder` → `fd.add_project_folders`).
  - **Use my existing folders** (`architecture.scan_existing_folders` → `fd.adopt_existing_folders`) is on the empty page and runs on first run.
  - Disk work runs in `MainWindow._run_folder_disk_change` off the GUI thread; `Change.fill_path`/`fill_paths` create folders and pull in matching loose files as one Undo batch.
- **Rules** that a folder sentence cannot express open `RuleEditorDialog` in `ui/main_window.py`; its text helpers live in `rule_text.py`.
- **Removed in beta.3:** `ui/flow/` (Sorting Flow, Guided Folder, Folder Setup Studio, Align Preview) and `ui/architecture_canvas.py`. Do not bring them back. `architecture.py` (the `FolderArchitecture` model) stays: the Folders screen and the service still use it.

## 3. Pitfalls

1. **Qt crashes (`Qt6Gui.dll` `0xc0000005`):** end every `QPainter` in `try/finally`; never rebuild widgets inside `dropEvent` (defer with `QTimer.singleShot(0, ...)`); parent `QDrag` to the window; never set `WA_TranslucentBackground` on child widgets.
2. **`MacToggle`** must stay visually in sync when `setChecked()` runs under `blockSignals(True)`.
3. **Router logic:** `extensions` is an AND filter; `keywords` and `domains` are OR. Plain-English text must say so.
4. **Paths:** relative destinations use `\`. On Linux a path built from `"A\\B"` becomes one folder named `A\B`, so split into parts (`Path.joinpath(*parts)`) in new code.
5. **Test gates:** `test_301_fixes.py` (no clipped buttons at 900×560, 1040×640, 1100×760, 1400×900), `test_screen_reader_names.py`, `test_plain_words.py` (no old feature names in shown text). `conftest.py` patches starter packs unless a test is marked `shipped_defaults`.
6. **The owner's real Downloads (`C:\Users\devyn\Downloads`) is never touched** during development. It has game, portable-app and Arduino bundles that must stay put, paths over 200 characters, and the display runs at 125%.

## 4. Reinstall after a build (owner's PC)

```powershell
Get-Process -Name "DownloadSorter*" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Process -FilePath ".\dist\DownloadSorter-Setup-v4.0.0-beta.3-windows-x64.exe" -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART" -Wait
```
