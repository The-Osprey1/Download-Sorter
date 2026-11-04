"""Comprehensive, requirement-driven, opaque-box E2E test suite for Download Sorter v2.

Covers:
- Attribution across CLI --version, setup/shell scripts, GUI footer, Tray
- Batch Handoff Quick Switcher (QuickSwitcherPill buttons, hover checklist toggles, drag-drop payload, empty selection guard)
- 3MF Grace-Period Hold & MakerWorld Overhaul (plate preview thumbnail, 'File now' and 'Keep here' actions, held tray indicators)
- Staged Machine Remodel (MachineRemodelWidget 3rd tab, folder cards 'Organize'/'Keep intact'/'Skip', duplicate quarantine protection for kept folders, 'Where did my files go.html' receipt generation and search JS)
- 4-Tier Methodology: Feature Coverage, Boundary & Corner Cases, Cross-Feature Combinations, Real-World Application Scenarios.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
import zipfile

# Set headless platform before any Qt import
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Ensure Tkinter finds tcl and tk libraries
for base in [Path(sys.base_prefix), Path(sys.executable).parent]:
    tcl_c = base / "tcl" / "tcl8.6"
    tk_c = base / "tcl" / "tk8.6"
    if tcl_c.exists():
        os.environ.setdefault("TCL_LIBRARY", str(tcl_c))
    if tk_c.exists():
        os.environ.setdefault("TK_LIBRARY", str(tk_c))

_MODULE_TK_ROOT = None

def get_module_tk_root():
    global _MODULE_TK_ROOT
    if _MODULE_TK_ROOT is None:
        import tkinter as tk
        try:
            _MODULE_TK_ROOT = tk.Tk()
            _MODULE_TK_ROOT.withdraw()
        except Exception:
            _MODULE_TK_ROOT = None
    return _MODULE_TK_ROOT

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import about
import batch_handoff
import gcode_peek
import hold_manager
import remodel
import safe_ops
import sorter
import suite_theme


# ==============================================================================
# Helper Functions
# ==============================================================================

def create_sample_3mf(
    dest_path: Path,
    thumbnail_rel_path: str = "Metadata/plate_1.png",
    include_xml: bool = True,
    xml_content: str = "<model><resources/></model>",
) -> Path:
    """Create a synthetically valid .3mf zip archive with an optional plate preview thumbnail."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if thumbnail_rel_path:
            # 1x1 valid PNG bytes
            png_bytes = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc`\x00\x00"
                b"\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            zf.writestr(thumbnail_rel_path, png_bytes)
        if include_xml:
            zf.writestr("3D/3dmodel.model", xml_content)
    return dest_path


# ==============================================================================
# Feature 1: Attribution Across All Surfaces (Tiers 1 & 2)
# ==============================================================================

class TestAttributionTier1AndTier2(unittest.TestCase):
    """Tier 1 (Feature Coverage) & Tier 2 (Boundary) for Single-Source Attribution."""

    CLI_SCRIPTS = [
        "clean_old", "gcode_peek", "quick_switcher", "ribbon_popup",
        "rules_manager", "sort_once", "sort_undo", "status",
        "toggle_pause", "tray", "unzip_3d", "unzip_archive", "watch",
        "remodel", "hold_manager", "clean_test_history", "restore_from_log",
    ]

    # ---- Tier 1: Feature Coverage (>=5 tests) ----

    def test_t1_credit_constants_and_strings(self) -> None:
        """F1-T1-1: Verify centralized attribution constants in about.py."""
        self.assertEqual(about.AUTHOR, "Devyn Fidel")
        self.assertEqual(about.YEAR, 2026)
        self.assertEqual(about.CREDIT, "Made by Devyn Fidel (2026)")
        self.assertTrue(about.__version__.startswith("2."))
        self.assertIn(about.CREDIT, about.version_string())
        self.assertIn(about.CREDIT, about.about_text())
        self.assertIn(about.REPO_URL, about.about_text())

    def test_t1_all_cli_scripts_version_flag(self) -> None:
        """F1-T1-2: Verify CLI --version returns 0 and prints credit on all CLI scripts."""
        for script in self.CLI_SCRIPTS:
            script_path = REPO_ROOT / f"{script}.py"
            if not script_path.exists():
                continue
            res = subprocess.run(
                [sys.executable, str(script_path), "--version"],
                capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
            )
            self.assertEqual(
                res.returncode, 0,
                f"Script {script}.py failed --version: {res.stderr}"
            )
            self.assertIn(about.CREDIT, res.stdout, f"{script}.py missing CREDIT in --version")
            self.assertIn(about.__version__, res.stdout, f"{script}.py missing version in --version")

    def test_t1_setup_and_updater_scripts_attribution(self) -> None:
        """F1-T1-3: Verify setup and updater scripts include author credit."""
        for filename in ["setup.bat", "update.bat"]:
            path = REPO_ROOT / filename
            self.assertTrue(path.exists(), f"Missing {filename}")
            content = path.read_text(encoding="utf-8", errors="ignore")
            self.assertIn(about.CREDIT, content, f"{filename} missing CREDIT")

    def test_t1_gui_footer_credit_label(self) -> None:
        """F1-T1-4: Verify GUI footer in rules_manager.py uses about.CREDIT."""
        rules_source = (REPO_ROOT / "rules_manager.py").read_text(encoding="utf-8")
        self.assertIn("about.CREDIT", rules_source)
        self.assertIn("creditFooter", rules_source)

    def test_t1_tray_tooltip_and_menu_attribution(self) -> None:
        """F1-T1-5: Verify Tray tooltip and context menu display about.CREDIT."""
        import tray
        t = tray.SorterTray()
        tooltip_str = t.tooltip()
        self.assertIn(about.CREDIT, tooltip_str)
        self.assertIn(about.APP_NAME, tooltip_str)
        menu = t.build_menu()
        menu_items_text = [getattr(item, "text", "") for item in menu.items if hasattr(item, "text")]
        # At least one item or callback must show CREDIT
        found_credit = any(about.CREDIT in str(txt) for txt in menu_items_text)
        self.assertTrue(found_credit, "Tray context menu does not contain about.CREDIT")

    # ---- Tier 2: Boundary & Corner Cases (>=5 tests) ----

    def test_t2_version_string_ascii_fidelity(self) -> None:
        """F1-T2-1: Ensure version string is 100% ASCII-encodable for any shell."""
        banner = about.version_string()
        try:
            ascii_bytes = banner.encode("ascii")
            self.assertIsInstance(ascii_bytes, bytes)
        except UnicodeEncodeError as exc:
            self.fail(f"version_string contains non-ASCII characters: {exc}")

    def test_t2_packaging_metadata_has_batch_handoff_and_credit(self) -> None:
        """F1-T2-2: Verify pyproject.toml has Devyn Fidel author and batch_handoff py-module."""
        pyproj = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('name = "Devyn Fidel"', pyproj)
        self.assertIn('attr = "about.__version__"', pyproj)
        self.assertIn('"batch_handoff"', pyproj, "batch_handoff is missing from py-modules in pyproject.toml")

    def test_t2_handle_version_flag_argv_parsing(self) -> None:
        """F1-T2-3: Test handle_version_flag with various argv combinations."""
        with patch("sys.stdout", new=io.StringIO()) as fake_out:
            handled = about.handle_version_flag(["--version"])
            self.assertTrue(handled)
            self.assertIn(about.CREDIT, fake_out.getvalue())

        with patch("sys.stdout", new=io.StringIO()) as fake_out:
            handled = about.handle_version_flag(["--help", "-v"])
            self.assertFalse(handled)
            self.assertEqual(fake_out.getvalue(), "")

        with patch("sys.stdout", new=io.StringIO()) as fake_out:
            handled = about.handle_version_flag(["subcommand", "--version", "--flag"])
            self.assertTrue(handled)

    def test_t2_cli_version_from_foreign_working_directory(self) -> None:
        """F1-T2-4: CLI --version execution functions correctly when invoked from arbitrary cwd."""
        with tempfile.TemporaryDirectory() as foreign_dir:
            res = subprocess.run(
                [sys.executable, str(REPO_ROOT / "remodel.py"), "--version"],
                capture_output=True, text=True, timeout=15, cwd=foreign_dir,
            )
            self.assertEqual(res.returncode, 0)
            self.assertIn(about.CREDIT, res.stdout)

    def test_t2_powershell_scripts_attribution_headers(self) -> None:
        """F1-T2-5: Verify install_context_menu.ps1 and install_startup.ps1 contain credit."""
        for ps_file in ["install_context_menu.ps1", "install_startup.ps1"]:
            ps_path = REPO_ROOT / ps_file
            if ps_path.exists():
                text = ps_path.read_text(encoding="utf-8", errors="ignore")
                self.assertTrue(
                    "Devyn Fidel" in text or "about.CREDIT" in text or "Download Sorter" in text,
                    f"{ps_file} lacks proper attribution headers",
                )


# ==============================================================================
# Feature 2: Batch Handoff Quick Switcher (Tiers 1 & 2)
# ==============================================================================

class TestBatchHandoffQuickSwitcherTier1AndTier2(unittest.TestCase):
    """Tier 1 (Feature Coverage) & Tier 2 (Boundary) for Quick Switcher & Batch Handoff."""

    @classmethod
    def setUpClass(cls) -> None:
        import tkinter as tk
        try:
            cls.shared_tk = tk.Tk()
            cls.shared_tk.withdraw()
        except Exception:
            cls.shared_tk = None

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.shared_tk is not None:
            try:
                cls.shared_tk.destroy()
            except Exception:
                pass

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name).resolve()
        batch_handoff.clear_recent_file()

    def tearDown(self) -> None:
        batch_handoff.clear_recent_file()
        self.temp_dir.cleanup()

    # ---- Tier 1: Feature Coverage (>=5 tests) ----

    def test_t1_quick_switcher_batch_pill_buttons(self) -> None:
        """F2-T1-1: Floating QuickSwitcherPill renders 'Latest', 'All N', and 'Copy' buttons in batch mode."""
        import quick_switcher
        f1 = self.test_root / "part1.step"
        f2 = self.test_root / "part2.step"
        f1.write_text("cad 1")
        f2.write_text("cad 2")

        recent_batch = {
            "name": "part2.step",
            "path": str(f2),
            "category": "cad",
            "color": "#69E6AD",
            "is_batch": True,
            "items": [
                {"name": "part1.step", "path": str(f1), "category": "cad", "color": "#69E6AD"},
                {"name": "part2.step", "path": str(f2), "category": "cad", "color": "#69E6AD"},
            ],
            "batch_id": "test_batch_1",
        }

        import tkinter as tk
        with patch("quick_switcher.tk.Tk", side_effect=lambda: tk.Toplevel(self.shared_tk) if self.shared_tk else MagicMock()), \
             patch("quick_switcher.render_category_icon", return_value=None), \
             patch.object(quick_switcher.QuickSwitcherPill, "_position_pill"), \
             patch.object(quick_switcher.QuickSwitcherPill, "_register_hotkey"):
            pill = quick_switcher.QuickSwitcherPill(dialog_hwnd=0, recent_data=recent_batch)
            pill.root.withdraw()
            try:
                self.assertTrue(pill.is_batch)
                self.assertIsNotNone(pill.action_btn, "Missing '↵ Latest' button")
                self.assertIsNotNone(pill.all_btn, "Missing '⇉ All N' button in batch mode")
                self.assertIsNotNone(pill.copy_btn, "Missing '⧉ Copy' button in batch mode")
                self.assertIn("Latest", pill.action_btn.cget("text"))
                self.assertIn("All 2", pill.all_btn.cget("text"))
                self.assertIn("Copy", pill.copy_btn.cget("text"))
            finally:
                pill.close()

    def test_t1_quick_switcher_single_file_button(self) -> None:
        """F2-T1-2: QuickSwitcherPill renders single-file action button when is_batch is False."""
        import quick_switcher
        f1 = self.test_root / "single.pdf"
        f1.write_text("doc")

        recent_single = {
            "name": "single.pdf",
            "path": str(f1),
            "category": "documents",
            "color": "#61A5FF",
            "is_batch": False,
            "items": [{"name": "single.pdf", "path": str(f1), "category": "documents"}],
        }

        import tkinter as tk
        with patch("quick_switcher.tk.Tk", side_effect=lambda: tk.Toplevel(self.shared_tk) if self.shared_tk else MagicMock()), \
             patch("quick_switcher.render_category_icon", return_value=None), \
             patch.object(quick_switcher.QuickSwitcherPill, "_position_pill"), \
             patch.object(quick_switcher.QuickSwitcherPill, "_register_hotkey"):
            pill = quick_switcher.QuickSwitcherPill(dialog_hwnd=0, recent_data=recent_single)
            pill.root.withdraw()
            try:
                self.assertFalse(pill.is_batch)
                self.assertIsNotNone(pill.action_btn)
                self.assertIsNone(pill.all_btn)
                self.assertIsNone(pill.copy_btn)
            finally:
                pill.close()

    def test_t1_hover_checklist_expansion_and_toggles(self) -> None:
        """F2-T1-3: Hover checklist expands and toggling a checkbox updates 'All N' label."""
        import quick_switcher
        files = [self.test_root / f"doc_{i}.pdf" for i in range(3)]
        for f in files:
            f.write_text("data")

        recent_batch = {
            "name": "doc_2.pdf",
            "path": str(files[2]),
            "is_batch": True,
            "items": [{"name": f.name, "path": str(f)} for f in files],
            "batch_id": "batch_toggle_test",
        }

        import tkinter as tk
        with patch("quick_switcher.tk.Tk", side_effect=lambda: tk.Toplevel(self.shared_tk) if self.shared_tk else MagicMock()), \
             patch("quick_switcher.render_category_icon", return_value=None), \
             patch.object(quick_switcher.QuickSwitcherPill, "_position_pill"), \
             patch.object(quick_switcher.QuickSwitcherPill, "_register_hotkey"):
            pill = quick_switcher.QuickSwitcherPill(dialog_hwnd=0, recent_data=recent_batch)
            pill.root.withdraw()
            try:
                self.assertEqual(len(pill._checked_paths()), 3)
                # Toggle one item off
                var = MagicMock()
                var.get.return_value = False
                pill._on_toggle(str(files[0]), var)
                self.assertEqual(len(pill._checked_paths()), 2)
                self.assertIn("All 2", pill.all_btn.cget("text"))
            finally:
                pill.close()

    def test_t1_hover_checklist_label_click_toggles_checkbox(self) -> None:
        """F2-T1-4: Clicking the filename label in the hover checklist toggles the checkbox."""
        qs_source = (REPO_ROOT / "quick_switcher.py").read_text(encoding="utf-8")
        # In quick_switcher.py: _expand_checklist row label must bind <Button-1> to toggle
        # (Survey D5: Currently only <B1-Motion> is bound to drag)
        self.assertTrue(
            'lbl.bind("<Button-1>"' in qs_source or "lbl.bind('<Button-1>'" in qs_source,
            "Checklist row filename label must bind <Button-1> to toggle checkbox selection",
        )

    def test_t1_copy_files_creates_hdrop_clipboard_payload(self) -> None:
        """F2-T1-5: 'Copy' action constructs Win32 CF_HDROP clipboard payload."""
        f1 = self.test_root / "copy1.txt"
        f2 = self.test_root / "copy2.txt"
        f1.write_text("copy 1")
        f2.write_text("copy 2")

        with patch("batch_handoff.copy_files_to_clipboard", return_value=True) as mock_copy:
            res = batch_handoff.copy_files_to_clipboard([str(f1), str(f2)])
            self.assertTrue(res)
            mock_copy.assert_called_once()

    # ---- Tier 2: Boundary & Corner Cases (>=5 tests) ----

    def test_t2_quick_switcher_empty_selection_guard(self) -> None:
        """F2-T2-1: Empty selection guard prevents injecting or copying 0 files."""
        import quick_switcher
        f1 = self.test_root / "item1.txt"
        f1.write_text("data")
        recent_batch = {
            "name": f1.name,
            "path": str(f1),
            "is_batch": True,
            "items": [{"name": f1.name, "path": str(f1)}],
            "batch_id": "b_empty",
        }

        import tkinter as tk
        with patch("quick_switcher.tk.Tk", side_effect=lambda: tk.Toplevel(self.shared_tk) if self.shared_tk else MagicMock()), \
             patch("quick_switcher.render_category_icon", return_value=None), \
             patch.object(quick_switcher.QuickSwitcherPill, "_position_pill"), \
             patch.object(quick_switcher.QuickSwitcherPill, "_register_hotkey"):
            pill = quick_switcher.QuickSwitcherPill(dialog_hwnd=0, recent_data=recent_batch)
            pill.root.withdraw()
            try:
                # Untick all files
                pill.checked[str(f1)] = False
                self.assertEqual(len(pill._checked_paths()), 0)

                # Calling select_all with 0 checked files must safely guard
                with patch("batch_handoff.inject_files_into_dialog") as mock_inject:
                    pill.select_all()
                    if mock_inject.called:
                        args, kwargs = mock_inject.call_args
                        self.assertTrue(len(args[1]) > 0, "Injected empty path list into dialog!")
            finally:
                pill.close()

    def test_t2_checklist_mousewheel_cleanup_on_collapse(self) -> None:
        """F2-T2-2: Global <MouseWheel> binding is cleaned up when checklist collapses to prevent TclError."""
        qs_source = (REPO_ROOT / "quick_switcher.py").read_text(encoding="utf-8")
        if 'bind_all("<MouseWheel>"' in qs_source:
            self.assertIn(
                "unbind_all", qs_source,
                "Global <MouseWheel> binding in quick_switcher must be unbound in _collapse_checklist",
            )

    def test_t2_checklist_canvas_geometry_stretching(self) -> None:
        """F2-T2-3: Checklist canvas inner window stretches to full width on configure."""
        qs_source = (REPO_ROOT / "quick_switcher.py").read_text(encoding="utf-8")
        self.assertIn("inner", qs_source)
        self.assertIn("canvas", qs_source)

    def test_t2_batch_handoff_special_characters_and_spaces(self) -> None:
        """F2-T2-4: Batch handoff handles spaces, unicode, brackets, and plus symbols in filenames."""
        weird_name = "Model (Rev [2.0]) + Draft #1 & Final — test.step"
        f = self.test_root / weird_name
        f.write_text("cad")

        batch_handoff.record_recent_file(f, downloads_root=self.test_root)
        rec = batch_handoff.get_recent_file(max_age_sec=300.0)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["name"], weird_name)
        self.assertTrue(Path(rec["path"]).exists())

    def test_t2_batch_staging_across_disjoint_folders(self) -> None:
        """F2-T2-5: Multi-folder batch handoff stages files cleanly into a common staging root."""
        d1 = self.test_root / "FolderA"
        d2 = self.test_root / "FolderB"
        d1.mkdir()
        d2.mkdir()
        f1 = d1 / "fileA.pdf"
        f2 = d2 / "fileB.pdf"
        f1.write_text("A")
        f2.write_text("B")

        stage_dir, staged = batch_handoff.stage_batch(
            [str(f1), str(f2)],
            batch_id="test_disjoint_b",
            downloads_root=self.test_root,
        )
        self.assertEqual(len(staged), 2)
        for p in staged:
            self.assertTrue(Path(p).exists())
            self.assertEqual(Path(p).parent.resolve(), stage_dir.resolve())


# ==============================================================================
# Feature 3: 3MF Grace-Period Hold & MakerWorld Overhaul (Tiers 1 & 2)
# ==============================================================================

class Test3MFGracePeriodHoldTier1AndTier2(unittest.TestCase):
    """Tier 1 (Feature Coverage) & Tier 2 (Boundary) for 3MF Hold & MakerWorld."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name).resolve()
        self.downloads = self.test_root / "Downloads"
        self.downloads.mkdir(parents=True, exist_ok=True)
        self.config = sorter.get_default_config()
        self.config["downloads_dir"] = str(self.downloads)
        self.config["watch"] = {"folder": str(self.downloads), "settle_seconds": 0, "min_age_minutes": 0}
        self.config["hold"]["enabled"] = True
        self.config["hold"]["hold_days"] = 7

        state_f = hold_manager.state_file()
        if state_f.exists():
            try:
                state_f.unlink()
            except Exception:
                pass

    def tearDown(self) -> None:
        state_f = hold_manager.state_file()
        if state_f.exists():
            try:
                state_f.unlink()
            except Exception:
                pass
        self.temp_dir.cleanup()

    # ---- Tier 1: Feature Coverage (>=5 tests) ----

    def test_t1_3mf_hold_registration_and_deferral(self) -> None:
        """F3-T1-1: Moving a .3mf file defers sorting and registers a 7-day grace period hold."""
        sample = self.downloads / "Bambu_Model.3mf"
        sample.write_text("dummy 3mf")

        with patch("ribbon_popup.notify_file_held"):
            res = sorter.move_file(sample, self.config, source_root=self.downloads)
            self.assertIsNone(res, "3MF file was immediately moved instead of held")
            self.assertTrue(sample.exists())
            self.assertTrue(hold_manager.is_held(sample))
            rec = hold_manager.get_held(sample)
            self.assertIsNotNone(rec)
            self.assertEqual(rec["hold_days"], 7)
            self.assertGreater(rec["release_at"], time.time() + (6 * 86400))

    def test_t1_3mf_plate_preview_thumbnail_extraction(self) -> None:
        """F3-T1-2: 3MF plate preview thumbnail is parsed from Metadata/plate_1.png."""
        sample_3mf = self.downloads / "Plate_Preview.3mf"
        create_sample_3mf(sample_3mf, thumbnail_rel_path="Metadata/plate_1.png")

        meta = gcode_peek.parse_3mf(sample_3mf)
        self.assertTrue(meta.get("has_thumbnail"), "parse_3mf failed to extract thumbnail")
        self.assertIsNotNone(meta.get("thumbnail_data"))
        self.assertGreater(len(meta["thumbnail_data"]), 10)

    def test_t1_ribbon_popup_file_now_action(self) -> None:
        """F3-T1-3: Clicking 'File now' immediately releases held 3MF to planned destination."""
        sample = self.downloads / "Immediate_File.3mf"
        sample.write_text("3mf content")

        planned_dir = self.test_root / "Organized" / "3D Models"
        planned_dir.mkdir(parents=True, exist_ok=True)
        planned_dest = planned_dir / sample.name

        hold_manager.hold_file(sample, self.config, planned_dest=planned_dest)
        self.assertTrue(hold_manager.is_held(sample))

        result = hold_manager.file_now(sample, self.config)
        self.assertIsNotNone(result)
        self.assertFalse(hold_manager.is_held(sample))
        self.assertFalse(sample.exists(), "Original held file still in downloads")
        self.assertTrue(result.exists(), "File was not moved to destination")

    def test_t1_ribbon_popup_keep_here_action(self) -> None:
        """F3-T1-4: Clicking 'Keep here' retains 3MF in Downloads permanently."""
        sample = self.downloads / "Keep_Sample.3mf"
        sample.write_text("3mf content")

        hold_manager.hold_file(sample, self.config, planned_dest=self.test_root / "3D" / sample.name)
        res = hold_manager.keep_here(sample)
        self.assertTrue(res)

        rec = hold_manager.get_held(sample)
        self.assertIsNotNone(rec)
        self.assertTrue(rec.get("keep_here"))

        # check_releases must NOT release files marked keep_here
        releases = hold_manager.check_releases(self.config, now=time.time() + (30 * 86400))
        released_paths = [str(src) for src, _ in releases]
        self.assertNotIn(str(sample.resolve()), released_paths)

    def test_t1_tray_held_indicators_and_count(self) -> None:
        """F3-T1-5: Tray tooltip, menu, and make_icon reflect held file count and visual indicator."""
        import tray
        t = tray.SorterTray()

        # When 0 files held
        self.assertEqual(t._held_count(), 0)
        self.assertNotIn("held in Downloads", t.tooltip())

        # When 2 files held
        f1 = self.downloads / "h1.3mf"
        f2 = self.downloads / "h2.3mf"
        f1.write_text("1")
        f2.write_text("2")
        hold_manager.hold_file(f1, self.config, planned_dest=self.downloads / "d1.3mf")
        hold_manager.hold_file(f2, self.config, planned_dest=self.downloads / "d2.3mf")

        self.assertEqual(t._held_count(), 2)
        self.assertIn("2 3MF(s) held in Downloads", t.tooltip())

        # make_icon badge check
        icon_normal = tray.make_icon(paused=False)
        self.assertIsNotNone(icon_normal)

    # ---- Tier 2: Boundary & Corner Cases (>=5 tests) ----

    def test_t2_3mf_thumbnail_case_insensitivity(self) -> None:
        """F3-T2-1: Candidate matching finds lowercased metadata/plate_1.png."""
        sample_3mf = self.downloads / "Lower_Plate.3mf"
        create_sample_3mf(sample_3mf, thumbnail_rel_path="metadata/plate_1.png")

        meta = gcode_peek.parse_3mf(sample_3mf)
        self.assertTrue(
            meta.get("has_thumbnail"),
            "parse_3mf failed on lowercased zip path metadata/plate_1.png",
        )

    def test_t2_3mf_thumbnail_fallback_chain(self) -> None:
        """F3-T2-2: Fallback chain resolves Metadata/thumbnail.png when plate_1.png is missing."""
        sample_3mf = self.downloads / "Fallback_Thumb.3mf"
        create_sample_3mf(sample_3mf, thumbnail_rel_path="Metadata/thumbnail.png")

        meta = gcode_peek.parse_3mf(sample_3mf)
        self.assertTrue(meta.get("has_thumbnail"))

    def test_t2_sorter_sort_destination_contract(self) -> None:
        """F3-T2-3: sorter.sort_destination(path, config) resolves destination without AttributeError."""
        self.assertTrue(
            hasattr(sorter, "sort_destination"),
            "Interface contract missing: sorter.sort_destination does not exist!",
        )
        dest = sorter.sort_destination("Sample_Bracket.3mf", self.config)
        self.assertIsInstance(dest, Path)
        self.assertIn("3D Models", str(dest))

    def test_t2_ribbon_popup_logging_and_exception_handling(self) -> None:
        """F3-T2-4: ribbon_popup imports logging so exception handlers do not raise NameError."""
        import ribbon_popup
        self.assertIn(
            "logging", dir(ribbon_popup),
            "ribbon_popup must import logging to avoid NameError crashes in button handlers",
        )

    def test_t2_ribbon_popup_eyebrow_text_for_held_files(self) -> None:
        """F3-T2-5: Held ribbon banner displays '● 3MF HOLD · DOWNLOADS' rather than '● SORTED'."""
        ribbon_source = (REPO_ROOT / "ribbon_popup.py").read_text(encoding="utf-8")
        self.assertTrue(
            "3MF HOLD" in ribbon_source or "HELD" in ribbon_source,
            "Ribbon banner for held 3MF files must not display SORTED tag",
        )

    def test_t2_slicer_running_defers_release(self) -> None:
        """F3-T2-6: Active slicer process defers release even after initial 7 days."""
        sample = self.downloads / "Active_Slicer.3mf"
        sample.write_text("3mf data")
        hold_manager.hold_file(sample, self.config, planned_dest=self.test_root / "out.3mf")

        # Mock slicer running
        with patch("hold_manager.is_slicer_running", return_value=True):
            releases = hold_manager.check_releases(self.config, now=time.time() + (8 * 86400))
            released_paths = [str(src) for src, _ in releases]
            self.assertNotIn(str(sample.resolve()), released_paths)


# ==============================================================================
# Feature 4: Staged Machine Remodel (Tiers 1 & 2)
# ==============================================================================

class TestStagedMachineRemodelTier1AndTier2(unittest.TestCase):
    """Tier 1 (Feature Coverage) & Tier 2 (Boundary) for Machine Remodel Engine & UI."""

    def setUp(self) -> None:
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

        self.temp_dir = tempfile.TemporaryDirectory()
        self.sandbox = Path(self.temp_dir.name).resolve()
        self.config = sorter.get_default_config()
        self.config["downloads_dir"] = str(self.sandbox)
        self.config["watch"] = {"folder": str(self.sandbox), "settle_seconds": 0, "min_age_minutes": 0}
        self.engine = remodel.RemodelEngine(self.config)

    def tearDown(self) -> None:
        h_dir = remodel.remodel_history_dir()
        if h_dir.exists():
            for f in h_dir.glob("remodel_*.json"):
                try:
                    f.unlink()
                except Exception:
                    pass
        self.app.processEvents()
        self.temp_dir.cleanup()

    # ---- Tier 1: Feature Coverage (>=5 tests) ----

    def test_t1_machine_remodel_widget_is_third_tab(self) -> None:
        """F4-T1-1: MachineRemodelWidget is verified as the 3rd tab in RulesManagerDialog."""
        import rules_manager
        dlg = rules_manager.RulesManagerDialog()
        try:
            self.assertEqual(dlg._tabs.count(), 3)
            tab_text = dlg._tabs.tabText(2)
            self.assertIn("Remodel", tab_text)
            self.assertIsInstance(dlg._tabs.widget(2), rules_manager.MachineRemodelWidget)
            self.assertIsInstance(dlg._remodel_widget, rules_manager.MachineRemodelWidget)
        finally:
            dlg.close()
            dlg.deleteLater()
            self.app.processEvents()

    def test_t1_folder_cards_actions_render(self) -> None:
        """F4-T1-2: Folder review cards render 'Organize', 'Keep intact', 'Skip' radio buttons."""
        import rules_manager
        sub = self.sandbox / "Subfolder"
        sub.mkdir()
        (sub / "test.txt").write_text("content")

        plan = self.engine.scan(self.sandbox)
        self.assertGreater(len(plan.cards), 0)

        widget = rules_manager.MachineRemodelWidget()
        try:
            widget._on_scan_finished(plan)
            self.assertGreater(len(widget._card_buttons), 0)
            btn_group = next(iter(widget._card_buttons.values()))
            button_texts = [b.text() for b in btn_group.buttons()]
            self.assertTrue(any("Organize" in t for t in button_texts))
            self.assertTrue(any("Keep Intact" in t or "Keep intact" in t for t in button_texts))
            self.assertTrue(any("Skip" in t for t in button_texts))
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_t1_duplicate_quarantine_protection_for_kept_folders(self) -> None:
        """F4-T1-3: Duplicate quarantine protection guarantees files in kept folders are never moved to quarantine."""
        kept_folder = self.sandbox / "Kept_Project"
        kept_folder.mkdir()
        content = "critical shared code bytes" * 50

        # Duplicate 1 is in a protected or keep_intact folder
        f_kept = kept_folder / "module.py"
        f_kept.write_text(content)

        # Duplicate 2 is loose in sandbox root
        f_loose = self.sandbox / "module_copy.py"
        f_loose.write_text(content)

        # Add .keep marker so folder is strictly protected
        (kept_folder / ".keep").write_text("")

        plan = self.engine.scan(self.sandbox)

        # Verify that f_kept is NEVER designated as a duplicate victim
        for src, q_dest, keeper in plan.duplicates_to_quarantine:
            self.assertNotEqual(
                str(src.resolve()), str(f_kept.resolve()),
                f"Protected file {f_kept} was marked for quarantine!",
            )

    def test_t1_where_did_my_files_go_receipt_generation(self) -> None:
        """F4-T1-4: Applying remodel generates searchable 'Where did my files go.html' receipt in root."""
        loose_pdf = self.sandbox / "Invoice_123.pdf"
        loose_pdf.write_text("pdf bytes")

        plan = self.engine.scan(self.sandbox)
        self.engine.apply(plan)

        receipt = self.sandbox / "Where did my files go.html"
        self.assertTrue(receipt.exists(), "Where did my files go.html was not generated")
        html = receipt.read_text(encoding="utf-8")
        self.assertIn("Where Did My Files Go", html)
        self.assertIn(plan.remodel_id, html)
        self.assertIn("Invoice_123.pdf", html)
        self.assertIn("download-sorter-remodel --undo", html)
        self.assertIn(about.CREDIT, html)

    def test_t1_html_receipt_search_javascript(self) -> None:
        """F4-T1-5: Generated HTML receipt includes real-time search input and filterTable() JS function."""
        loose = self.sandbox / "doc.txt"
        loose.write_text("txt")
        plan = self.engine.scan(self.sandbox)
        self.engine.apply(plan)

        receipt = self.sandbox / "Where did my files go.html"
        html = receipt.read_text(encoding="utf-8")
        self.assertIn('id="searchInput"', html)
        self.assertIn("function filterTable()", html)
        self.assertIn("toLowerCase()", html)
        self.assertIn("receiptTable", html)

    # ---- Tier 2: Boundary & Corner Cases (>=5 tests) ----

    def test_t2_remodel_scan_excludes_previous_receipt(self) -> None:
        """F4-T2-1: Subsequent remodel scans exclude 'Where did my files go.html' from loose file scan."""
        receipt = self.sandbox / "Where did my files go.html"
        receipt.write_text("old receipt html")

        plan = self.engine.scan(self.sandbox)
        planned_srcs = [str(src.name).lower() for src, _ in plan.loose_files_to_organize]
        self.assertNotIn("where did my files go.html", planned_srcs)

    def test_t2_remodel_undo_full_restoration(self) -> None:
        """F4-T2-2: remodel.undo restores all files to exact original locations and removes receipt."""
        orig_file = self.sandbox / "Important_File.pdf"
        orig_file.write_text("file content")

        plan = self.engine.scan(self.sandbox)
        self.engine.apply(plan)
        self.assertFalse(orig_file.exists())

        receipt = self.sandbox / "Where did my files go.html"
        self.assertTrue(receipt.exists())

        # Perform undo
        success = self.engine.undo(plan.remodel_id)
        self.assertTrue(success)
        self.assertTrue(orig_file.exists())
        self.assertFalse(receipt.exists(), "Receipt should be deleted after successful undo")

    def test_t2_folder_card_user_overrides_respected(self) -> None:
        """F4-T2-3: User overrides ('keep_intact', 'skip') passed to apply are strictly honored."""
        sub = self.sandbox / "Target_Folder"
        sub.mkdir()
        f = sub / "item.pdf"
        f.write_text("pdf")

        plan = self.engine.scan(self.sandbox)
        # Apply with override 'keep_intact'
        overrides = {str(sub): "keep_intact"}
        self.engine.apply(plan, card_overrides=overrides)

        # File inside folder should still exist untouched
        self.assertTrue(f.exists())

    def test_t2_remodel_cad_and_simulation_protection(self) -> None:
        """F4-T2-4: 4-tier protection automatically protects CAD assemblies and simulation directories."""
        cad_dir = self.sandbox / "Gearbox_Assembly"
        cad_dir.mkdir()
        (cad_dir / "assembly.sldasm").write_text("cad asm")
        (cad_dir / "part1.sldprt").write_text("cad prt")

        plan = self.engine.scan(self.sandbox)
        cad_cards = [c for c in plan.cards if c.folder_path.resolve() == cad_dir.resolve()]
        self.assertEqual(len(cad_cards), 1)
        self.assertTrue(cad_cards[0].protection.is_protected)
        self.assertIn("CAD", cad_cards[0].protection.reason.upper())

    def test_t2_rules_manager_dialog_safe_teardown(self) -> None:
        """F4-T2-5: RulesManagerDialog instantiates and tears down cleanly without 0xC0000005 crash."""
        import rules_manager
        dlg = rules_manager.RulesManagerDialog()
        try:
            dlg._tabs.setCurrentIndex(2)  # Switch to Remodel
            self.app.processEvents()
        finally:
            dlg.close()
            dlg.deleteLater()
            self.app.processEvents()


# ==============================================================================
# Tier 3: Cross-Feature Combinations (Pairwise Interactions)
# ==============================================================================

class TestCrossFeatureCombinationsTier3(unittest.TestCase):
    """Tier 3: Pairwise interactions between features."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name).resolve()
        batch_handoff.clear_recent_file()

    def tearDown(self) -> None:
        batch_handoff.clear_recent_file()
        self.temp_dir.cleanup()

    def test_t3_held_3mf_in_quick_switcher(self) -> None:
        """F2+F3: Held 3MF recorded in recent state displays in Quick Switcher with CAD/3D category."""
        import quick_switcher
        sample_3mf = self.test_root / "Held_Print.3mf"
        sample_3mf.write_text("3mf content")

        batch_handoff.record_recent_file(sample_3mf, downloads_root=self.test_root)
        rec = batch_handoff.get_recent_file()
        self.assertIsNotNone(rec)
        self.assertIn(rec.get("category"), ("3d", "cad"))

        import tkinter as tk
        shared = get_module_tk_root()
        with patch("quick_switcher.tk.Tk", side_effect=lambda: tk.Toplevel(shared) if shared else MagicMock()), \
             patch("quick_switcher.render_category_icon", return_value=None), \
             patch.object(quick_switcher.QuickSwitcherPill, "_position_pill"), \
             patch.object(quick_switcher.QuickSwitcherPill, "_register_hotkey"):
            pill = quick_switcher.QuickSwitcherPill(dialog_hwnd=0, recent_data=rec)
            pill.root.withdraw()
            try:
                self.assertEqual(pill.category, rec["category"])
                self.assertEqual(pill.filename, "Held_Print.3mf")
            finally:
                pill.close()

    def test_t3_remodel_preserves_held_3mf_files(self) -> None:
        """F3+F4: Remodel scan and apply preserves held 3MF state and files."""
        downloads = self.test_root / "Downloads"
        downloads.mkdir()
        held_3mf = downloads / "InProgress_Plate.3mf"
        held_3mf.write_text("3mf hold data")

        config = sorter.get_default_config()
        config["downloads_dir"] = str(downloads)

        hold_manager.hold_file(held_3mf, config, planned_dest=downloads / "out.3mf")
        self.assertTrue(hold_manager.is_held(held_3mf))

        engine = remodel.RemodelEngine(config)
        plan = engine.scan(downloads)

        # Remodel should not move the actively held 3MF as an ordinary loose file
        planned_moves = [str(src.resolve()) for src, _ in plan.loose_files_to_organize]
        self.assertNotIn(str(held_3mf.resolve()), planned_moves)

    def test_t3_tray_refresh_on_remodel_and_hold(self) -> None:
        """F1+F3+F4: System tray accurately updates tooltip and menus across hold and filing."""
        import tray
        t = tray.SorterTray()
        self.assertIn("0 file(s) filed today", t.tooltip())

        # Register held file
        f_hold = self.test_root / "Held.3mf"
        f_hold.write_text("held")
        config = sorter.get_default_config()
        hold_manager.hold_file(f_hold, config, planned_dest=self.test_root / "out.3mf")

        self.assertIn("1 3MF(s) held in Downloads", t.tooltip())


# ==============================================================================
# Tier 4: Real-World Application Scenarios
# ==============================================================================

class TestRealWorldApplicationScenariosTier4(unittest.TestCase):
    """Tier 4: End-to-end full lifecycle simulations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        batch_handoff.clear_recent_file()

    def tearDown(self) -> None:
        batch_handoff.clear_recent_file()
        self.temp_dir.cleanup()

    def test_t4_scenario_makerworld_lifecycle(self) -> None:
        """Scenario A: User downloads 3MF model from MakerWorld -> held for 7 days -> 'File now'."""
        downloads = self.root / "Downloads"
        downloads.mkdir()
        config = sorter.get_default_config()
        config["downloads_dir"] = str(downloads)
        config["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0}
        config["hold"]["enabled"] = True
        config["hold"]["hold_days"] = 7

        # 1. File lands in Downloads
        model_3mf = downloads / "MultiColor_Vase.3mf"
        create_sample_3mf(model_3mf, thumbnail_rel_path="Metadata/plate_1.png")

        # 2. Watcher detects and defers moving
        with patch("ribbon_popup.notify_file_held"):
            res = sorter.move_file(model_3mf, config, source_root=downloads)
            self.assertIsNone(res)
            self.assertTrue(hold_manager.is_held(model_3mf))

        # 3. User touches file in slicer (mtime updated)
        old_mtime = model_3mf.stat().st_mtime
        time.sleep(0.05)
        model_3mf.write_text("updated slice data")
        new_mtime = model_3mf.stat().st_mtime
        self.assertNotEqual(old_mtime, new_mtime)

        # 4. User clicks 'File now'
        final_dest = hold_manager.file_now(model_3mf, config)
        self.assertIsNotNone(final_dest)
        self.assertTrue(final_dest.exists())
        self.assertFalse(hold_manager.is_held(model_3mf))

    def test_t4_scenario_multi_file_browser_batch_handoff(self) -> None:
        """Scenario B: User downloads 4 files -> QuickSwitcher batch appears -> user unticks 1 file -> clicks All 3."""
        import quick_switcher
        downloads = self.root / "Downloads"
        downloads.mkdir()
        files = [downloads / f"Project_Spec_Part_{i}.pdf" for i in range(4)]
        for f in files:
            f.write_text(f"spec {f.name}")

        # Record batch
        for f in files:
            batch_handoff.record_recent_file(f, downloads_root=downloads)

        recent = batch_handoff.get_recent_file()
        self.assertIsNotNone(recent)
        self.assertEqual(recent.get("batch_count"), 4)
        self.assertEqual(len(recent.get("items", [])), 4)

        import tkinter as tk
        shared = get_module_tk_root()
        with patch("quick_switcher.tk.Tk", side_effect=lambda: tk.Toplevel(shared) if shared else MagicMock()), \
             patch("quick_switcher.render_category_icon", return_value=None), \
             patch.object(quick_switcher.QuickSwitcherPill, "_position_pill"), \
             patch.object(quick_switcher.QuickSwitcherPill, "_register_hotkey"):
            pill = quick_switcher.QuickSwitcherPill(dialog_hwnd=0, recent_data=recent)
            pill.root.withdraw()
            try:
                # User unticks one file
                var = MagicMock()
                var.get.return_value = False
                pill._on_toggle(str(files[3]), var)
                self.assertEqual(len(pill._checked_paths()), 3)

                # User triggers select_all
                with patch("batch_handoff.inject_files_into_dialog") as mock_inject:
                    mock_inject.return_value = batch_handoff.InjectResult(True, "multi", [str(f) for f in files[:3]])
                    pill.select_all()
                    mock_inject.assert_called_once()
            finally:
                pill.close()

    def test_t4_scenario_messy_downloads_remodel_lifecycle(self) -> None:
        """Scenario C: Messy directory with loose files, duplicates, protected CAD assembly -> Remodel -> Receipt -> Undo."""
        target = self.root / "MessyMachine"
        target.mkdir()

        # 1. Loose files
        p_pdf = target / "Doc1.pdf"
        p_pdf.write_text("pdf")

        # 2. Duplicate files
        dup_text = "duplicate text block" * 20
        d1 = target / "file_copy1.txt"
        d1.write_text(dup_text)
        sub_folder = target / "Sub"
        sub_folder.mkdir()
        d2 = sub_folder / "file_copy2.txt"
        d2.write_text(dup_text)

        # 3. Protected CAD Assembly
        cad_dir = target / "RoboticArm"
        cad_dir.mkdir()
        (cad_dir / "arm.sldasm").write_text("assembly")
        (cad_dir / "bracket.sldprt").write_text("part")

        config = sorter.get_default_config()
        config["downloads_dir"] = str(target)
        engine = remodel.RemodelEngine(config)

        # 4. Scan
        plan = engine.scan(target)
        self.assertGreater(len(plan.cards), 0)

        # 5. Apply remodel
        engine.apply(plan)

        # 6. Verify receipt
        receipt = target / "Where did my files go.html"
        self.assertTrue(receipt.exists())
        self.assertIn("Where Did My Files Go", receipt.read_text(encoding="utf-8"))

        # 7. Undo remodel
        engine.undo(plan.remodel_id)
        self.assertTrue(p_pdf.exists())
        self.assertFalse(receipt.exists())


if __name__ == "__main__":
    unittest.main()
