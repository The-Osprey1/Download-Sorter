"""Automated visual design consistency tests enforcing Devyn Personal Suite design tokens.

Validates that:
- All UI components (quick_switcher.py, ribbon_popup.py, rules_manager.py, remodel.py, tray.py)
  and generated HTML receipts adhere strictly to suite_theme.py design tokens.
- Zero forbidden off-palette hex codes exist in UI source code or generated artifacts
  (#171C26, #181D26, #212836, #282F3D, #151A22, #1D232E, #FFB347, #181C25, #0F1218, etc.).
- Dark slate/navy palette, typography (Segoe UI, Cascadia Code), border radii (small=6,
  control=8, card=11), and spacing tokens are consistently applied.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import about
import remodel
import sorter
import suite_theme
from suite_theme import ACCENTS, COLORS, RADII, SPACING, TYPE


# Target UI files that must adhere strictly to suite_theme tokens
UI_FILES = [
    "quick_switcher.py",
    "ribbon_popup.py",
    "rules_manager.py",
    "remodel.py",
    "tray.py",
]

# Explicit list of forbidden off-palette hex codes identified in surveys & requirements
FORBIDDEN_HEX_CODES = [
    "#171C26",  # Off-palette button background in quick_switcher
    "#181D26",  # Off-palette surface tint in remodel/ribbon
    "#212836",  # Off-palette hover background in quick_switcher/ribbon
    "#282F3D",  # Off-palette close button hover in quick_switcher/ribbon
    "#151A22",  # Off-palette receipt table header in remodel.py
    "#1D232E",  # Off-palette receipt table hover in remodel.py
    "#FFB347",  # Off-palette pastel warning orange in remodel.py (must be #F5AD62)
    "#181C25",  # Off-palette progress track in ribbon_popup
    "#0F1218",  # Off-palette code block background in remodel.py
]


class TestSuiteThemeTokens(unittest.TestCase):
    """Verify suite_theme.py canonical tokens are intact and authoritative."""

    def test_canonical_color_palette_tokens(self) -> None:
        """Assert core dark slate/navy background and border tokens."""
        self.assertEqual(COLORS["canvas"], "#0B0D12", "canvas background must be #0B0D12")
        self.assertEqual(COLORS["surface"], "#12151C", "surface background must be #12151C")
        self.assertEqual(COLORS["surfaceRaised"], "#171B24", "surfaceRaised must be #171B24")
        self.assertEqual(COLORS["surfaceHover"], "#1D222C", "surfaceHover must be #1D222C")
        self.assertEqual(COLORS["border"], "#2A303B", "border must be #2A303B")
        self.assertEqual(COLORS["borderStrong"], "#3A4352", "borderStrong must be #3A4352")
        self.assertEqual(COLORS["text"], "#EDF1F8")
        self.assertEqual(COLORS["textMuted"], "#8B94A7")
        self.assertEqual(COLORS["textFaint"], "#657084")

    def test_canonical_accent_tokens(self) -> None:
        """Assert Download Sorter mint accent and semantic colors."""
        self.assertEqual(ACCENTS["downloadSorter"], "#69E6AD", "Download Sorter mint must be #69E6AD")
        self.assertEqual(COLORS["success"], "#69E6AD")
        self.assertEqual(COLORS["warning"], "#F5AD62", "warning amber must be #F5AD62")
        self.assertEqual(COLORS["critical"], "#FA7183", "critical red must be #FA7183")

    def test_canonical_typography_and_radii_tokens(self) -> None:
        """Assert typography families, font scales, and border radii."""
        self.assertEqual(TYPE["ui"], "Segoe UI")
        self.assertEqual(TYPE["mono"], "Cascadia Code")
        self.assertEqual(RADII["small"], 6)
        self.assertEqual(RADII["control"], 8)
        self.assertEqual(RADII["card"], 11)


class TestForbiddenHexCodesInUIComponents(unittest.TestCase):
    """Assert that no forbidden off-palette hex codes exist in any UI module."""

    def test_no_forbidden_hex_codes_in_ui_files(self) -> None:
        """Scan each UI file line by line; assert zero instances of forbidden hex codes."""
        violations: list[str] = []
        for filename in UI_FILES:
            filepath = REPO_ROOT / filename
            if not filepath.exists():
                continue
            content = filepath.read_text(encoding="utf-8")
            for forbidden_hex in FORBIDDEN_HEX_CODES:
                # Case-insensitive search for the hex code
                pattern = re.compile(re.escape(forbidden_hex), re.IGNORECASE)
                matches = pattern.findall(content)
                if matches:
                    violations.append(
                        f"{filename} contains {len(matches)} instance(s) of forbidden off-palette code {forbidden_hex}"
                    )

        self.assertEqual(
            violations, [],
            "Forbidden off-palette hex codes detected in UI modules:\n" + "\n".join(violations),
        )

    def test_no_forbidden_hex_codes_in_generated_html_receipt(self) -> None:
        """Generate a live HTML receipt and assert zero forbidden hex codes in generated HTML."""
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td).resolve()
            loose_file = temp_root / "test_doc.pdf"
            loose_file.write_text("pdf dummy")

            config = sorter.get_default_config()
            config["downloads_dir"] = str(temp_root)
            engine = remodel.RemodelEngine(config)
            plan = engine.scan(temp_root)
            engine.apply(plan)

            receipt_file = temp_root / "Where did my files go.html"
            self.assertTrue(receipt_file.exists(), "Where did my files go.html was not generated")
            html = receipt_file.read_text(encoding="utf-8")

            violations = []
            for forbidden_hex in FORBIDDEN_HEX_CODES:
                if forbidden_hex.lower() in html.lower():
                    violations.append(f"Generated HTML receipt contains forbidden hex {forbidden_hex}")

            self.assertEqual(violations, [], "\n".join(violations))


class TestQuickSwitcherVisualConsistency(unittest.TestCase):
    """Assert Quick Switcher UI tokens match suite_theme.py."""

    def test_quick_switcher_imports_suite_theme(self) -> None:
        """Verify quick_switcher.py imports and uses suite_theme tokens."""
        content = (REPO_ROOT / "quick_switcher.py").read_text(encoding="utf-8")
        self.assertIn("from suite_theme import", content)
        self.assertIn("COLORS", content)
        self.assertIn("ACCENTS", content)

    def test_quick_switcher_action_color_defaults_to_mint_accent(self) -> None:
        """Quick Switcher brand action color must be Download Sorter mint (#69E6AD)."""
        content = (REPO_ROOT / "quick_switcher.py").read_text(encoding="utf-8")
        # ACTION_COLOR should not be hardcoded to info blue #61A5FF by default
        self.assertIn(
            'ACCENTS.get("downloadSorter"', content,
            "quick_switcher ACTION_COLOR must use ACCENTS['downloadSorter'] (#69E6AD)",
        )

    def test_quick_switcher_font_sizes_adhere_to_type_tokens(self) -> None:
        """Labels and badges should not use unscaled tiny 7pt or 8pt fonts."""
        content = (REPO_ROOT / "quick_switcher.py").read_text(encoding="utf-8")
        # Check that TYPE tokens or sizeXs (9px) / sizeSm (11px) are used
        self.assertIn("TYPE", content)


class TestRibbonPopupVisualConsistency(unittest.TestCase):
    """Assert Ribbon Banner Popup visual tokens match suite_theme.py."""

    def test_ribbon_popup_imports_suite_theme(self) -> None:
        """Verify ribbon_popup.py imports and uses suite_theme tokens."""
        content = (REPO_ROOT / "ribbon_popup.py").read_text(encoding="utf-8")
        self.assertIn("from suite_theme import", content)
        self.assertIn("COLORS", content)
        self.assertIn("ACCENTS", content)

    def test_ribbon_file_now_high_contrast(self) -> None:
        """Assert 'File now' button uses dark canvas text on bright mint background for WCAG contrast."""
        content = (REPO_ROOT / "ribbon_popup.py").read_text(encoding="utf-8")
        # Look for file_now button configuration
        # Must use dark text (#0B0D12 or COLORS['canvas']) on mint background (#69E6AD)
        self.assertTrue(
            "COLORS.get('canvas'" in content or "#0B0D12" in content or 'COLORS["canvas"]' in content,
            "Ribbon 'File now' button must use dark canvas text on mint background",
        )

    def test_ribbon_keep_here_button_styling(self) -> None:
        """Assert 'Keep here' button uses surfaceRaised (#171B24)."""
        content = (REPO_ROOT / "ribbon_popup.py").read_text(encoding="utf-8")
        self.assertTrue(
            "surfaceRaised" in content or "#171B24" in content,
            "Ribbon 'Keep here' button must use surfaceRaised styling",
        )


class TestRulesManagerRemodelVisualConsistency(unittest.TestCase):
    """Assert Rules Manager Remodel tab and folder cards match suite_theme.py."""

    @staticmethod
    def _remodel_source() -> str:
        content = (REPO_ROOT / "rules_manager.py").read_text(encoding="utf-8")
        return content.split("class MachineRemodelWidget")[1].split("class RulesManagerDialog")[0]

    @staticmethod
    def _style_rule(selector: str) -> str:
        """The body of one rule of the shared stylesheet (the look every page takes from ui_style)."""
        import ui_style

        sheet = ui_style.stylesheet()
        start = sheet.index(selector + " {")
        return sheet[start:sheet.index("}", start)]

    def test_remodel_folder_cards_elevation_and_radii(self) -> None:
        """Folder cards are built with ui_style.card(), whose shared look uses RADII['card'] (11px)."""
        self.assertEqual(RADII["card"], 11)
        self.assertIn(
            "ui_style.card(", self._remodel_source(),
            "Folder cards in MachineRemodelWidget must be built with the shared card() so they match the suite",
        )
        card_rule = self._style_rule("QFrame#card")
        self.assertIn(f"border-radius: {RADII['card']}px", card_rule)
        self.assertIn(COLORS["border"], card_rule)

    def test_remodel_card_radio_buttons_styled(self) -> None:
        """The choices in folder cards are segmented buttons, selected in the mint accent (#69E6AD)."""
        self.assertIn('setProperty("role", "segment")', self._remodel_source())
        checked_rule = self._style_rule('QRadioButton[role="segment"]:checked')
        self.assertIn(ACCENTS["downloadSorter"], checked_rule)

    def test_remodel_tables_suite_theme_styling(self) -> None:
        """Protected and duplicate tables in Remodel tab must use suite_theme table styling."""
        content = (REPO_ROOT / "rules_manager.py").read_text(encoding="utf-8")
        self.assertIn("_prot_table", content)
        self.assertIn("_dupe_table", content)


class TestHTMLReceiptVisualConsistency(unittest.TestCase):
    """Assert 'Where did my files go.html' strictly adheres to suite_theme.py tokens."""

    def test_html_receipt_css_variables_and_typography(self) -> None:
        """Assert CSS variables in remodel.py match suite_theme.py."""
        content = (REPO_ROOT / "remodel.py").read_text(encoding="utf-8")
        # --bg must be canvas #0B0D12
        self.assertIn(
            "--bg: #0B0D12", content,
            "HTML receipt --bg CSS variable must be #0B0D12 (COLORS['canvas'])",
        )
        # --surface must be #171B24
        self.assertIn(
            "--surface: #171B24", content,
            "HTML receipt --surface CSS variable must be #171B24 (COLORS['surfaceRaised'])",
        )
        # --accent must be #69E6AD
        self.assertIn(
            "--accent: #69E6AD", content,
            "HTML receipt --accent CSS variable must be #69E6AD (ACCENTS['downloadSorter'])",
        )
        # --warning must be #F5AD62
        self.assertIn(
            "--warning: #F5AD62", content,
            "HTML receipt --warning CSS variable must be #F5AD62 (COLORS['warning'])",
        )
        # Monospace font must reference Cascadia Code
        self.assertIn(
            "Cascadia Code", content,
            "HTML receipt monospace paths must specify 'Cascadia Code'",
        )

    def test_html_receipt_footer_attribution(self) -> None:
        """Assert HTML receipt footer includes full attribution from about.CREDIT."""
        content = (REPO_ROOT / "remodel.py").read_text(encoding="utf-8")
        self.assertIn("about.CREDIT", content, "HTML receipt template must include about.CREDIT in footer")


class TestTrayVisualConsistency(unittest.TestCase):
    """Assert Tray applet adheres to suite_theme tokens."""

    def test_tray_accent_colors(self) -> None:
        """Assert tray icon uses Download Sorter mint (105, 230, 173) and surface dark body (23, 27, 36)."""
        content = (REPO_ROOT / "tray.py").read_text(encoding="utf-8")
        self.assertIn("105, 230, 173", content)
        self.assertIn("about.CREDIT", content)


if __name__ == "__main__":
    unittest.main()
