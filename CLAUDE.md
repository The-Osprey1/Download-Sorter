# CLAUDE.md — Download Sorter (v4 on `main`)

Download Sorter is a Windows local-first, single-process PySide6 desktop app (`DownloadSorter.exe`) with a companion CLI (`DownloadSorter-cli.exe`) and Chrome extension (`chrome_extension/`). The application code lives in `src/download_sorter/`; the repo root holds `main.py`, `about.py`, and release tooling.

> **Handoff:** See [`V4_HANDOFF.md`](V4_HANDOFF.md) for where v4 stands, the Qt6/Windows pitfalls, and the four-phase plan to v4.0.

## Branching & Safety
- **`main` carries v4** (`4.0.0-beta.3`) since PR #92 merged on 2026-11-28. Work on a branch and land it on `main` through a pull request. The last v3 release is the `v3.6.1` tag.
- **NEVER delete, move, or modify files inside `C:\Users\devyn\Downloads`** during development or testing. Always use `tmp_path` / `app_paths` fixtures or read-only dry-run previews.

## Layout
- `src/download_sorter/`:
  - `pipeline.py` — Unified v4 trace & Teach-by-Example engine (`trace_file`, `TYPE_BUCKETS`, `infer_teach_cues`, `build_rule_from_cues`).
  - `router.py` — Deterministic routing engine (`evaluate`, `extract_host`, `read_zone_url`, specificity ordering). Note: when a rule has both `extensions` and `keywords`/`domains`, `extensions` acts as an `AND` filter (`ext in extensions`) combined with `(any keyword OR any domain)`.
  - `architecture.py` — Multi-tier `FolderArchitecture`, `scan_folder_architecture`, `apply_architecture_to_config`, presets, and Tier-2 suggestions.
  - `service.py` — Watcher, `preview_architecture_shift`, `apply_architecture_and_shift`, bundle protection (`_is_project_or_bundle_dir`), and background sorting.
  - `fs_ops.py` (safe moves, `.staging`, undo), `db.py` (SQLite history), `config.py` (settings, opt-in defaults: `NEW_INSTALL_HOLD_3MF = False`, `auto_align_existing = False`; `project_grouping_enabled` and `show_advanced_tools` are still saved but no longer shown), `quick_switcher.py`, `ipc.py`, `cli.py`, `chrome_bridge.py`.
  - `folder_designer.py` — Pure, mistake-proof folder edits for the Folders screen (`add_folder`, `set_cues`, `rename_folder`, `move_folder`, `merge_folder`, `remove_folder`, `teach`, `add_project_folders`, `adopt_existing_folders`, `check_folder_name`, `why_cannot_move`). Every edit returns a `Change` (new `Config` + on-disk moves + `fill_path`/`fill_paths`) or raises `DesignerError` with a user-facing message.
  - `rule_text.py` — Qt-free token cleanup (`normalize_ext_tokens`, `normalize_domain_tokens`, `registrable_domain_tokens`) and plain-English rule text, shared by the Folders screen and `RuleEditorDialog`.
  - `project_detect.py` — `detect_projects_in_folder`, used by the Folders screen's "Find projects inside".
  - `ui/folders/folders_tab.py` — `FoldersTab`, the first tab and the only place folders are designed: sidebar tester + folder tree (validated drag-to-nest) + Handled for you; folder page as a clickable sentence; Still in Downloads tray with teach; `TidyReviewDialog`; `PickListDialog` for "Find projects inside" and "Use my existing folders" (also the first-run screen). MainWindow runs its disk moves via `_run_folder_disk_change` (including `Change.fill_path` → `service.fill_folder_on_disk`, which creates a new folder and pulls in matching loose files) and Undo via `folders:undo_disk`. The tree paints its own rows (`_FolderRowDelegate`, `drawBranches`) so no style draws a selection bar.
  - `ui/main_window.py` — 3-tab window (`Folders` | `Activity` | `Settings` 2-pane sidebar) + `RuleEditorDialog` + dialogs. The v3 Sorting Flow, Folder Architecture canvas and their dialogs were removed in 4.0.0-beta.3; do not bring them back.
  - `ui/settings_widgets.py` (`MacToggle`, `SettingCard`, `SettingRow`), `ui/motion.py`, `ui/theme.py`, `ui/toasts.py`, `ui/tray_app.py`, `ui/workers.py`.
- `tests_v3/`: Full pytest suite. `conftest.py` patches legacy starter packs via an `autouse` fixture unless a test is marked `@pytest.mark.shipped_defaults`.
- `about.py`, `src/download_sorter/about.py`, `installer.iss`, `pyproject.toml`, and `CHANGELOG.md` must stay in version parity (`verify_release.py` enforces this).

## Commands (Windows PowerShell 5.1 — use `;` not `&&`)
```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -X faulthandler -m pytest tests_v3 -q
.\.venv\Scripts\python.exe main.py --version
.\.venv\Scripts\python.exe main.py --simulate
.\.venv\Scripts\python.exe build_release.py
.\.venv\Scripts\python.exe verify_release.py
```

## Critical Rules & Qt6/Windows Invariants
- **Never delete a user's file**; moves are recorded in SQLite first and remain undoable.
- **Keep disk/network work off the Qt main thread** (see `ui/workers.py`). The only network request is the once-a-day update check in `update_check.py`.
- **`QPainter` safety:** Guard every widget pointer with `shiboken6.isValid(...)`, wrap `QPainter(self)` in `try ... finally: painter.end()`, and NEVER set `WA_TranslucentBackground` on child widgets.
- **Plain words:** one name per feature (Tidy, Folders, Rules, Old Files). `tests_v3/test_plain_words.py` fails if an old name (Tier, Sorting Flow, Folder Architecture, Align, Shift Files, Clean Up Now, Detox, Custom Rules) appears in text people see.
- **Drag-and-drop safety (`Qt6Gui.dll` `0xc0000005` prevention):** Always parent `QDrag` to `self.window() or self` (never a transient row widget), defer all `dropEvent` mutations/rebuilds via `QTimer.singleShot(0, ...)`, and hide + `setParent(None)` widgets before `deleteLater()` when clearing layouts.
- **UI regression gates:** Every `QAbstractButton`/`QLineEdit` needs text, tooltip, or `accessibleName` (`tests_v3/test_screen_reader_names.py`), and no visible button may clip below `sizeHint().width() - 1` at `900×560`, `1040×640`, `1100×760`, or `1400×900` (`tests_v3/test_301_fixes.py`).
- **Releasing:** Pushing a `v*` tag triggers `.github/workflows/release.yml`. Always ask before pushing tags.
