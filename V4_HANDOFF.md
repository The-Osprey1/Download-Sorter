# Download Sorter v4.0 — Development Handoff (for Claude Code / Opus 5.5)

**Prepared:** 2026-11-26  
**Repository:** `c:\Users\devyn\Documents\antigravity\beautiful-bell\Download-Sorter-v3`  
**Active Branch:** `feat/v4-sorting-flow` at commit `5351c54` (clean working tree)  
**Stable Branch:** `main` at `v3.6.1` (untouched)  
**Current Version:** `4.0.0-beta.1`  
**Baseline Verification:** `ruff check .` = **0 errors** · `pytest tests_v3 -q` = **574 passed, 3 skipped** · `verify_release.py` = **PREFLIGHT PASSED**

---

## 1. Product Vision & User Mandate

The user's core directive for v4 is:
> *"V4 must be easy to use, fluid, and intuitive — feel like an Apple / official Windows 11 app in terms of polish. Do not hold files or group them by default (opt-in only). Overhaul the custom folder interface so it walks the user through setup — not a passive tutorial, but an interactive interface that makes the viewer fill everything out properly."*

---

## 2. What Is Already Built & Verified (`03249af` → `5351c54`)

| Milestone | Commit(s) | Key Files |
|---|---|---|
| **Unified 3-Tab v4 UI & Pipeline** (`Sorting Flow` \| `Activity` \| `Preferences`) | `03249af`, `14cf11f`, `0c35ff4`, `7a858a8` | `pipeline.py`, `ui/flow/flow_tab.py`, `spine_panel.py`, `folder_tree_panel.py`, `tester_bar.py`, `bridge_overlay.py` |
| **Apple/Win11 Redesign, Opt-In Holds/Grouping, 2-Pane Preferences & Live Disk Alignment** | `5862cec`, `19b55f3`, `e0ecd40` | `config.py`, `ui/main_window.py`, `ui/flow/align_dialog.py`, `ui/settings_widgets.py` |
| **Real-World `Downloads` Folder Architecture Hardening** (dominant extension ownership, game/extracted/Arduino bundle guards, extension-ID & CDN host filtering, curated subfolder protection, 7 Flow→Arch handler fixes) | `5d55eec`, `c228805` | `architecture.py`, `service.py`, `ui/main_window.py` |
| **Crash Fixes & Full Drag-and-Drop Flow Editing** (`Qt6Gui.dll` `0xc0000005` paint/drag fixes, `MacToggle` `blockSignals` fix, drag rules to reorder/route, drag format groups to folders, drag folders to nest/promote with live Bezier snap) | `ce9236e` | `bridge_overlay.py`, `spine_panel.py`, `folder_tree_panel.py`, `flow_tab.py`, `settings_widgets.py`, `motion.py` |
| **Guided Custom Folder Builder, 3-Step Folder Setup Studio & Mistake-Proof Rule Composer** | `5351c54` | `ui/flow/folder_builder.py`, `ui/flow/setup_studio.py`, `ui/flow/spine_panel.py`, `ui/flow/folder_tree_panel.py`, `ui/flow/flow_tab.py`, `ui/main_window.py`, `ui/architecture_canvas.py`, `DownloadSorter.spec` |

### Key New v4 Modules
- **[`src/download_sorter/ui/flow/folder_builder.py`](src/download_sorter/ui/flow/folder_builder.py)** (`GuidedFolderDialog`):
  - 3-section mistake-proof folder composer: `1. Folder Name & Location` (name-first, 1-click presets, `Top-level in Downloads` vs `Inside an existing folder` pills, Windows/DOS/duplicate validation) → `2. What Files Belong Here?` (`Sort automatically` \| `Manual folder` switch, clickable format pills, keyword pills + dashed ghost suggestions, URL-normalizing website pills, sample-file drag-and-drop auto-fill) → `3. Plain-English Check & Live Downloads Preview` (accurate `(ext) AND (keyword OR domain)` summary, live loose-file match list + undoable `chk_align_now`, narrating primary CTA).
  - Manual folders persist via `node.premade_key == "__manual__"` and are physically created on disk (`mkdir(parents=True, exist_ok=True)`) so users can drop files into them immediately.
- **[`src/download_sorter/ui/flow/setup_studio.py`](src/download_sorter/ui/flow/setup_studio.py)** (`FolderSetupStudioDialog`):
  - 3-step guided setup: `1. Starting Point` (Scan existing Downloads with protected untouched `Games`/`Extracted`/`_Duplicates`/bundles vs. Blueprints vs. Current) → `2. Customize Folders` (checklist with `✓ Ready`, `! Needs rules`, `! Fix name`, `Manual` badges + guided chip editor + live filename mini-tester) → `3. Review & Apply` (safe dry-run preview with opt-in `chk_shift_files` + Undo).
- **[`src/download_sorter/ui/flow/spine_panel.py`](src/download_sorter/ui/flow/spine_panel.py)**:
  - Inline `_GuidedRuleComposerWidget` (zero dummy `"example"` keywords, clickable format chips, URL/ext normalization, `+ Folder...` inline creation, live plain-English preview, completeness guard, atomic edit flush on `Done`).
- **Tests:**
  - `tests_v3/test_guided_folder_builder.py` (36+ tests)
  - `tests_v3/test_setup_studio.py` (24+ tests)
  - `tests_v3/test_v4_sorting_flow.py` (core v4 pipeline, clean defaults, drag-and-drop, crash regressions, manual folder & composer flows)

---

## 3. Hard-Won Gotchas & Pitfalls (Avoid Re-Introducing These)

1. **`Qt6Gui.dll` `0xc0000005` Access Violations:**
   - **Never** leave a `QPainter(self)` open if an exception can be thrown (`BridgeOverlay.paintEvent` MUST validate every endpoint with `shiboken6.isValid(...)` and wrap painting in `try ... finally: painter.end()`).
   - **Never** trigger `rebuild()` / `deleteLater()` synchronously inside `dropEvent` or `mousePressEvent` while `QDrag.exec()` / Win32 `DoDragDrop` is on the C++ stack. Always parent `QDrag` to `self.window() or self` and defer mutations via `QTimer.singleShot(0, ...)`.
   - **Never** set `WA_TranslucentBackground` on child widgets (`BridgeOverlay`, `ConfettiOverlayWidget`).
   - Clear `self._peek_wires` and `self.overlay.clear_wires()` BEFORE `rebuild()` destroys row widgets; call `w.hide(); w.setParent(None); w.deleteLater()` on removed layout items to avoid transient ghost overlap.
2. **`MacToggle` Visual State Sync:**
   - `MacToggle` must stay visually synced even when `setChecked()` is called under `blockSignals(True)` (handled in `checkStateSet()` + `paintEvent()` in `ui/settings_widgets.py`).
3. **Router Boolean Semantics (`AND` vs. `OR`):**
   - In `router.py`, `extensions` is an `AND` filter (`ext in rule.extensions`), while `keywords` and `domains` are `OR` triggers (`any keyword OR any domain`). Every plain-English preview must reflect this accurately.
4. **Test Suite Invariants:**
   - `tests_v3/conftest.py` has an `autouse` fixture `_legacy_new_install_packs` that patches `NEW_INSTALL_*` defaults unless a test is decorated with `@pytest.mark.shipped_defaults`.
   - `tests_v3/test_301_fixes.py` enforces zero button clipping at `(900, 560)`, `(1040, 640)`, `(1100, 760)`, and `(1400, 900)` across all 3 tabs.
   - `tests_v3/test_screen_reader_names.py` enforces that every `QAbstractButton` and `QLineEdit` has text, tooltip, or `accessibleName`.
5. **User's Real `Downloads` Environment (`C:\Users\devyn\Downloads`):**
   - Contains 14 top-level dirs (`3D Models`, `3D Printing`, `Archives`, `Audio`, `CAD`, `Code`, `Documents`, `Extracted`, `Games`, `Images`, `Installers`, `Other`, `Videos`, `_Duplicates`), 296 paths >200 chars (`LongPathsEnabled = 1`), and game/portable-app/Arduino bundles that MUST remain untouched.
   - User's display runs at `125%` DPI scaling (`2048×1152` logical / `2560×1440` physical).

---

## 4. Remaining Work to Reach v4.0.0 GA

1. **Real-World UX & Polish Pass (User Feedback Loop):**
   - Get user feedback after trying the newly installed `5351c54` build (`+ New Folder`, `Folder Setup`, inline Guided Rule Composer, and drag-and-drop).
   - **Small visual polish opportunity spotted in `GuidedFolderDialog` & `FolderSetupStudioDialog`:** At default dialog height (`~720px–760px`), the Step 2 scroll area slightly clips the bottom row of dashed ghost chips above the pinned Step 3 preview card (`Website source` requires a slight scroll). Tightening vertical rhythm / collapsible advanced chips or raising default dialog height slightly on `>=1080p` screens will make all 3 chip rows visible at a glance.
   - **First-run experience alignment:** Consider whether `WalkthroughDialog` (old 3-step v3 slideshow) should be replaced or directly link into `FolderSetupStudioDialog` on clean installs so first-time users immediately experience the guided interactive setup rather than a passive tutorial.
   - **Rule collision preview in `GuidedFolderDialog`:** Surface an amber inline note if an existing higher-priority rule already catches one of the matched loose files in `Downloads` (showing which rule wins or offering to place the new rule ahead of it).
2. **Documentation & Release Prep for v4.0.0:**
   - Update `CHANGELOG.md` (`[4.0.0-beta.1]` / `[4.0.0]` entry) to reflect all commits since `03249af` (opt-in holds/grouping, 2-pane Preferences, live Downloads alignment, drag-and-drop flow editing, `GuidedFolderDialog`, `FolderSetupStudioDialog`, `GuidedRuleComposer`, crash fixes).
   - Update `README.md`, `BETA_TESTING.md`, and `FLOWCHART.md` screenshots/descriptions from v3 to v4 Sorting Flow.
   - When ready for stable release: bump version in `about.py`, `src/download_sorter/about.py`, `installer.iss`, and `CHANGELOG.md` from `4.0.0-beta.1` → `4.0.0`, run `ruff`, `pytest tests_v3`, `build_release.py`, and `verify_release.py`, then merge `feat/v4-sorting-flow` → `main` (ask user before pushing git tags).

---

## 5. Clean Local Reinstall Command (After Building a New Installer)

Never touch `C:\Users\devyn\Downloads`. To cleanly replace the local Windows install after `build_release.py`:
```powershell
Get-Process -Name "DownloadSorter*" -ErrorAction SilentlyContinue | Stop-Process -Force
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1 -PurgeUserData -Silent
Start-Process -FilePath ".\dist\DownloadSorter-Setup-v4.0.0-beta.1-windows-x64.exe" -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/MERGETASKS=desktopicon" -Wait
```

---

## 6. Starter Prompt for Claude Code (`Opus 5.5`)

> Read `CLAUDE.md` and `V4_HANDOFF.md` in `Download-Sorter-v3`. We are on branch `feat/v4-sorting-flow` (commit `5351c54`, `574` tests passing, `v4.0.0-beta.1` installed locally). Do not touch `main` or `C:\Users\devyn\Downloads`. Continue v4 development from the remaining roadmap in `V4_HANDOFF.md` (and any new feedback I share), keeping `ruff`, `pytest tests_v3`, `build_release.py`, and `verify_release.py` green.
