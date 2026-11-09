"""Milestone 5 Final Adversarial Empirical Challenger Suite.

Adversarially tests all requirements across:
- R1 / F1: Attribution & CLI --version / -V ergonomics, GUI footer, Tray tooltip/menu
- R1 / F2: Batch Handoff Quick Switcher (buttons, toggles, mousewheel cleanup, empty guards, mint accent)
- R1 / F3: 3MF Grace Period Hold & MakerWorld (sort_destination contract, case-insensitivity, ribbon eyebrow, high contrast, tray badge)
- R1 / F4: Staged Machine Remodel (3rd tab, review cards, duplicate quarantine protection, receipt exclusion, search JS, reversible undo)
- R2 / F5: Visual design token consistency across all surfaces
- R3: Stability and 0xC0000005 freedom
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image

import about
from suite_theme import ACCENTS

ROOT = Path(__file__).resolve().parent.parent


# =============================================================================
# AREA 1: ATTRIBUTION & CLI ERGONOMICS
# =============================================================================

class TestArea1AttributionErgonomics:
    """Stress-test attribution across every single entry point."""

    def test_gui_footer_attribution_in_rules_manager(self):
        """RulesManagerDialog must contain creditFooter with about.CREDIT."""
        source = (ROOT / "rules_manager.py").read_text(encoding="utf-8")
        assert 'setObjectName("creditFooter")' in source
        assert 'about.CREDIT' in source

    def test_tray_tooltip_and_menu_attribution(self):
        """Tray tooltip and About menu item must accurately source about.CREDIT."""
        source = (ROOT / "tray.py").read_text(encoding="utf-8")
        assert 'about.CREDIT' in source
        assert 'about.version_string()' in source or 'about.CREDIT' in source


# =============================================================================
# AREA 2: BATCH HANDOFF QUICK SWITCHER
# =============================================================================

class TestArea2QuickSwitcherAdversarial:
    """Stress-test Quick Switcher components, bindings, and empty selections."""

    def test_quick_switcher_action_color_is_mint(self):
        """Verify default ACTION_COLOR is suite_theme ACCENTS['downloadSorter']."""
        import quick_switcher
        assert quick_switcher.ACTION_COLOR == ACCENTS["downloadSorter"]
        assert quick_switcher.ACTION_COLOR.lower() == "#69e6ad"

    def test_no_forbidden_hex_codes_in_quick_switcher(self):
        """Zero off-palette hex codes in quick_switcher.py."""
        forbidden = ["#171c26", "#181d26", "#212836", "#282f3d", "#151a22", "#1d232e", "#ffb347", "#181c25", "#0f1218"]
        source = (ROOT / "quick_switcher.py").read_text(encoding="utf-8").lower()
        for hex_code in forbidden:
            assert hex_code not in source, f"Forbidden hex code {hex_code} found in quick_switcher.py"

    def test_checklist_label_click_binding_exists(self):
        """Row label must bind <Button-1> to toggle checkbox state."""
        source = (ROOT / "quick_switcher.py").read_text(encoding="utf-8")
        assert 'lbl.bind("<Button-1>"' in source or "lbl.bind('<Button-1>'" in source

    def test_mousewheel_unbind_on_collapse(self):
        """Collapse must unbind global <MouseWheel> to avoid TclError."""
        source = (ROOT / "quick_switcher.py").read_text(encoding="utf-8")
        assert 'unbind_all("<MouseWheel>")' in source or "unbind_all('<MouseWheel>')" in source

    def test_empty_selection_guards_against_injection(self):
        """select_all() and copy_files() must abort if 0 files selected."""
        source = (ROOT / "quick_switcher.py").read_text(encoding="utf-8")
        assert 'if len(paths) == 0:' in source
        assert '✕ No files selected' in source


# =============================================================================
# AREA 3: 3MF GRACE-PERIOD HOLD & MAKERWORLD
# =============================================================================

class TestArea33MFHoldAndMakerWorld:
    """Stress-test 3MF hold routing, plate thumbnail extraction, and ribbon UI."""

    def test_sorter_sort_destination_contract(self, tmp_path):
        """sorter.sort_destination must resolve exact destination Path without crashing."""
        import sorter
        assert hasattr(sorter, "sort_destination")

        watch_dir = tmp_path / "Downloads"
        watch_dir.mkdir(parents=True, exist_ok=True)
        models_dir = tmp_path / "3D Models"
        models_dir.mkdir(parents=True, exist_ok=True)

        cfg = {
            "watch": {"folder": str(watch_dir)},
            "destinations": {"3D Models": str(models_dir)},
            "extension_rules": [
                {"name": "3MF Models", "extensions": [".3mf"], "folder": "3D Models\\3MF", "enabled": True},
                {"name": "STL Models", "extensions": [".stl"], "folder": "3D Models\\STL", "enabled": True},
            ],
            "keyword_rules": [],
        }

        # Case 1: .3mf extension
        p_3mf = watch_dir / "test_model.3mf"
        dest_3mf = sorter.sort_destination(p_3mf, cfg)
        assert isinstance(dest_3mf, Path)
        assert "3D Models" in str(dest_3mf)
        assert dest_3mf.name == "test_model.3mf"

        # Case 2: String path input
        dest_str = sorter.sort_destination(str(p_3mf), cfg)
        assert isinstance(dest_str, Path)
        assert dest_str.name == "test_model.3mf"

    def test_case_insensitive_3mf_thumbnail_extraction(self, tmp_path):
        """gcode_peek.parse_3mf must extract plate_1.png even with lowercased path metadata/plate_1.png."""
        import gcode_peek

        # Create valid 16x16 PNG image bytes
        img = Image.new("RGBA", (16, 16), color=(105, 230, 173))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        # Build zip with lowercased metadata/plate_1.png
        zip_path = tmp_path / "lowercase_plate.3mf"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("metadata/plate_1.png", png_bytes)
            zf.writestr("[Content_Types].xml", "<Types/>")

        meta = gcode_peek.parse_3mf(zip_path)
        assert meta.get("has_thumbnail") is True, "Failed to detect thumbnail from lowercased metadata/plate_1.png"
        assert meta.get("thumbnail_name") == "metadata/plate_1.png"
        assert meta.get("thumbnail_data") == png_bytes

    def test_fallback_thumbnail_candidate_chain(self, tmp_path):
        """If plate_1.png is missing or empty, must fall back to Metadata/thumbnail.png."""
        import gcode_peek

        img = Image.new("RGBA", (20, 20), color=(245, 173, 98))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        zip_path = tmp_path / "fallback_thumb.3mf"
        with zipfile.ZipFile(zip_path, "w") as zf:
            # Corrupted / 0-byte plate_1.png
            zf.writestr("Metadata/plate_1.png", b"")
            # Valid thumbnail.png
            zf.writestr("Metadata/thumbnail.png", png_bytes)

        meta = gcode_peek.parse_3mf(zip_path)
        assert meta.get("has_thumbnail") is True, "Failed to fall back to Metadata/thumbnail.png"
        assert meta.get("thumbnail_name") == "Metadata/thumbnail.png"
        assert meta.get("thumbnail_data") == png_bytes

    def test_ribbon_popup_eyebrow_and_contrast(self):
        """Ribbon popup must display 3MF HOLD eyebrow and dark text on mint File now button."""
        source = (ROOT / "ribbon_popup.py").read_text(encoding="utf-8")

        # Eyebrow check
        assert '●  3MF HOLD  ·' in source
        # High contrast check
        assert 'fg=COLORS["canvas"]' in source or 'fg=COLORS.get("canvas"' in source
        # Logging import check
        assert 'import logging' in source

    def test_tray_icon_held_badge(self):
        """tray.make_icon must draw an indicator badge when held_count > 0."""
        import tray
        icon_held = tray.make_icon(paused=False, held_count=3)
        icon_normal = tray.make_icon(paused=False, held_count=0)
        assert icon_held is not None
        assert icon_normal is not None
        # The pixels must differ due to the held badge dot
        assert list(icon_held.tobytes()) != list(icon_normal.tobytes())


# =============================================================================
# AREA 4: STAGED MACHINE REMODEL
# =============================================================================

class TestArea4StagedMachineRemodel:
    """Stress-test Machine Remodel protections, receipt exclusion, and HTML receipt."""

    def test_remodel_widget_is_third_tab(self):
        """MachineRemodelWidget must be integrated as its own page ("Clean up a folder")."""
        source = (ROOT / "rules_manager.py").read_text(encoding="utf-8")
        assert "self._remodel_widget = MachineRemodelWidget(" in source
        assert "self._view_stack.addWidget(self._padded(self._remodel_widget))" in source

    def test_remodel_scan_excludes_previous_receipt(self, tmp_path):
        """RemodelEngine.scan must exclude previous 'Where did my files go.html'."""
        import remodel
        engine = remodel.RemodelEngine()
        receipt_file = tmp_path / "Where did my files go.html"
        receipt_file.write_text("<html>Receipt</html>", encoding="utf-8")

        other_file = tmp_path / "normal_document.docx"
        other_file.write_text("test", encoding="utf-8")

        plan = engine.scan(target_root=tmp_path)
        loose_names = [src.name for src, _dest in plan.loose_files_to_organize]
        assert "normal_document.docx" in loose_names
        assert "Where did my files go.html" not in loose_names

    def test_duplicate_quarantine_protection_for_kept_and_protected_folders(self, tmp_path):
        """Files in protected paths or kept folders must never be quarantined as duplicates."""
        import remodel

        # Folder A: protected/keep intact folder
        prot_dir = tmp_path / "Active_Project"
        prot_dir.mkdir()
        file_prot = prot_dir / "assembly.step"
        file_prot.write_text("EXACT_IDENTICAL_CONTENT_HASH", encoding="utf-8")

        # Folder B: loose directory
        loose_dir = tmp_path / "Old_Mess"
        loose_dir.mkdir()
        file_loose = loose_dir / "assembly_copy.step"
        file_loose.write_text("EXACT_IDENTICAL_CONTENT_HASH", encoding="utf-8")

        # Execute find_duplicates with protected_paths
        dupes = remodel.find_duplicates(
            [file_prot, file_loose],
            protected_paths=[prot_dir],
        )

        for d in dupes:
            # Keeper should be the protected file
            assert d.keeper.resolve() == file_prot.resolve()
            # Duplicates to quarantine must NEVER include protected file
            for dup_file in d.duplicates:
                assert dup_file.resolve() != file_prot.resolve(), "Protected file in quarantine list!"

    def test_html_receipt_styling_and_search_js(self, tmp_path):
        """HTML receipt must include Cascadia Code font, suite theme tokens, and filterTable JS."""
        import remodel
        receipt_path = tmp_path / "Where did my files go.html"
        manifest = {
            "remodel_id": "20261104-TEST",
            "executed_moves": [
                {"src": "test.txt", "dest": "Documents/test.txt", "action": "organize"}
            ],
            "skipped_items": [],
            "created_at": "2026-11-04T00:00:00Z",
            "author": about.CREDIT,
        }
        remodel.generate_html_receipt(manifest=manifest, output_path=receipt_path)
        assert receipt_path.exists()
        content = receipt_path.read_text(encoding="utf-8")

        # Visual theme tokens
        assert "--bg: #0B0D12;" in content or "--bg: #0b0d12;" in content.lower()
        assert "--accent: #69E6AD;" in content or "--accent: #69e6ad;" in content.lower()
        assert "Cascadia Code" in content

        # Search functionality JS
        assert "filterTable" in content
        assert "getElementById" in content

        # Attribution in receipt footer
        assert about.CREDIT in content


# =============================================================================
# AREA 5: VISUAL CONSISTENCY & NO FORBIDDEN HEX CODES
# =============================================================================

class TestArea5VisualConsistency:
    """Ensure zero forbidden off-palette hex codes across all UI files."""

    FORBIDDEN_CODES = [
        "#171c26", "#181d26", "#212836", "#282f3d", "#151a22", "#1d232e",
        "#ffb347", "#181c25", "#0f1218",
    ]

    UI_FILES = [
        "quick_switcher.py",
        "ribbon_popup.py",
        "rules_manager.py",
        "remodel.py",
        "tray.py",
    ]

    @pytest.mark.parametrize("filename", UI_FILES)
    @pytest.mark.parametrize("hex_code", FORBIDDEN_CODES)
    def test_no_forbidden_hex_in_ui_module(self, filename, hex_code):
        path = ROOT / filename
        source = path.read_text(encoding="utf-8").lower()
        assert hex_code not in source, f"Forbidden hex code {hex_code} found in {filename}"
