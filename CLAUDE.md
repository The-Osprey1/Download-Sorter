# CLAUDE.md — Download Sorter (v4 Development on `feat/v4-sorting-flow`)

Download Sorter is a Windows local-first, single-process PySide6 desktop app (`DownloadSorter.exe`) with a companion CLI (`DownloadSorter-cli.exe`) and Chrome extension (`chrome_extension/`). The application code lives in `src/download_sorter/`; the repo root holds `main.py`, `about.py`, and release tooling.

> **Active v4 handoff:** See [`V4_HANDOFF.md`](V4_HANDOFF.md) for the full v4.0.0-beta.1 architecture map, completed milestones, Qt6/Windows pitfalls, and remaining v4.0 GA roadmap.

## Branching & Safety
- **`main` (`v3.6.1`)** is the stable v3 line — keep it untouched until v4.0 GA is ready to merge.
- **`feat/v4-sorting-flow` (`v4.0.0-beta.2`)** is the active v4 development branch.
- **NEVER delete, move, or modify files inside `C:\Users\devyn\Downloads`** during development or testing. Always use `tmp_path` / `app_paths` fixtures or read-only dry-run previews.

## Layout
- `src/download_sorter/`:
  - `pipeline.py` — Unified v4 trace & Teach-by-Example engine (`trace_file`, `TYPE_BUCKETS`, `infer_teach_cues`, `build_rule_from_cues`).
  - `router.py` — Deterministic routing engine (`evaluate`, `extract_host`, `read_zone_url`, specificity ordering). Note: when a rule has both `extensions` and `keywords`/`domains`, `extensions` acts as an `AND` filter (`ext in extensions`) combined with `(any keyword OR any domain)`.
  - `architecture.py` — Multi-tier `FolderArchitecture`, `scan_folder_architecture`, `apply_architecture_to_config`, presets, and Tier-2 suggestions.
  - `service.py` — Watcher, `preview_architecture_shift`, `apply_architecture_and_shift`, bundle protection (`_is_project_or_bundle_dir`), and background sorting.
  - `fs_ops.py` (safe moves, `.staging`, undo), `db.py` (SQLite history), `config.py` (settings, opt-in defaults: `NEW_INSTALL_HOLD_3MF = False`, `project_grouping_enabled = False`, `auto_align_existing = False`), `quick_switcher.py`, `ipc.py`, `cli.py`, `chrome_bridge.py`.
  - `folder_designer.py` — Pure, mistake-proof folder edits for the Folders screen (`add_folder`, `set_cues`, `rename_folder`, `move_folder`, `merge_folder`, `remove_folder`, `teach`, `check_folder_name`, `why_cannot_move`). Every edit returns a `Change` (new `Config` + on-disk moves) or raises `DesignerError` with a user-facing message.
  - `ui/folders/folders_tab.py` — `FoldersTab`, the first tab since 4.0.0-beta.2: sidebar tester + folder tree (validated drag-to-nest) + Handled for you; folder page as a clickable sentence; Still in Downloads tray with teach; `TidyReviewDialog`. MainWindow runs its disk moves via `_run_folder_disk_change` and Undo via `folders:undo_disk`.
  - `ui/flow/` — v4 Sorting Flow UI (since beta.2 kept hidden; its dialogs are still used):
    - `flow_tab.py` — `SortingFlowTab` (orchestrates TesterBar, SpinePanel, FolderTreePanel, BridgeOverlay, background disk scan, drag-and-drop).
    - `spine_panel.py` — `SpinePanel`, `_RuleRowWidget`, `_GuidedRuleComposerWidget`, `_TypeRowWidget` (inline guided rule composer + drag-to-reorder / drag-to-folder).
    - `folder_tree_panel.py` — `FolderTreePanel`, `FolderRowWidget`, `_RootDropHeader` (live Downloads tree, sync status bar, `+ New Folder`, `Folder Setup`, drag-to-nest/promote).
    - `bridge_overlay.py` — `BridgeOverlay` (QPainter Bezier wires + live drag wire preview).
    - `folder_builder.py` — `GuidedFolderDialog` (3-section mistake-proof custom folder composer with chips, URL/ext normalization, sample-file drop, Manual/Auto switch, live Downloads match preview).
    - `setup_studio.py` — `FolderSetupStudioDialog` (3-step guided folder architecture studio: Scan/Blueprint/Current → Checklist + Guided Editor → Safe Review & Apply).
    - `align_dialog.py` — `AlignPreviewDialog` (dry-run selective loose-file alignment).
    - `tester_bar.py` — `TesterBar` (live X-Ray filename/URL tester with OS file drop).
  - `ui/main_window.py` — 3-tab window (`Folders` | `Activity` | `Settings` 2-pane sidebar) + `RuleEditorDialog` + dialogs.
  - `ui/architecture_canvas.py` — Advanced Node Canvas + upgraded `CustomFolderNodeDialog`.
  - `ui/settings_widgets.py` (`MacToggle`, `SettingCard`, `SettingRow`), `ui/motion.py`, `ui/theme.py`, `ui/toasts.py`, `ui/tray_app.py`, `ui/workers.py`.
- `tests_v3/`: Full pytest suite (`574 passed, 3 skipped`). `conftest.py` patches legacy starter packs via an `autouse` fixture unless a test is marked `@pytest.mark.shipped_defaults`.
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
- **`BridgeOverlay` & `QPainter` safety:** Guard every widget pointer with `shiboken6.isValid(...)`, clear transient wires before `rebuild()`, wrap `QPainter(self)` in `try ... finally: painter.end()`, and NEVER set `WA_TranslucentBackground` on child widgets.
- **Drag-and-drop safety (`Qt6Gui.dll` `0xc0000005` prevention):** Always parent `QDrag` to `self.window() or self` (never a transient row widget), defer all `dropEvent` mutations/rebuilds via `QTimer.singleShot(0, ...)`, and hide + `setParent(None)` widgets before `deleteLater()` when clearing layouts.
- **UI regression gates:** Every `QAbstractButton`/`QLineEdit` needs text, tooltip, or `accessibleName` (`tests_v3/test_screen_reader_names.py`), and no visible button may clip below `sizeHint().width() - 1` at `900×560`, `1040×640`, `1100×760`, or `1400×900` (`tests_v3/test_301_fixes.py`).
- **Releasing:** Pushing a `v*` tag triggers `.github/workflows/release.yml`. Always ask before pushing tags.
