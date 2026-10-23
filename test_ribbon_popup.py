from __future__ import annotations

from pathlib import Path
import unittest

from ribbon_popup import (
    BASE_GAP,
    BASE_HEIGHT,
    BASE_OFFSET_X,
    BASE_OFFSET_Y,
    BASE_WIDTH,
    CATEGORY_GLYPHS,
    RIBBON_DISMISS_FILE,
    RibbonPopup,
    add_to_recent_docs,
    blend_colors,
    dismiss_active_ribbons,
    get_accent_color,
    get_category_info,
    hex_to_rgb,
    render_category_icon,
    scaled_ribbon_geometry,
)


class RibbonGeometryTests(unittest.TestCase):
    def test_supported_windows_dpi_scales(self) -> None:
        work_area = (0, 0, 2560, 1400)
        for scale in (1.0, 1.25, 1.5, 1.75, 2.0):
            with self.subTest(scale=scale):
                width, height, x, y = scaled_ribbon_geometry(scale, 0, work_area)
                self.assertEqual(width, round(BASE_WIDTH * scale))
                self.assertEqual(height, round(BASE_HEIGHT * scale))
                self.assertEqual(x, work_area[2] - width - round(BASE_OFFSET_X * scale))
                self.assertEqual(y, round(BASE_OFFSET_Y * scale))

    def test_stacking_and_negative_monitor_coordinates(self) -> None:
        work_area = (-1920, 40, 0, 1080)
        first = scaled_ribbon_geometry(1.5, 0, work_area)
        fourth = scaled_ribbon_geometry(1.5, 3, work_area)
        self.assertLess(first[2], 0)
        self.assertEqual(
            fourth[3] - first[3],
            3 * (round(BASE_HEIGHT * 1.5) + round(BASE_GAP * 1.5)),
        )


class RibbonGraphicsAndAnimationTests(unittest.TestCase):
    def test_color_blending(self) -> None:
        # Full weight color 1
        self.assertEqual(blend_colors("#ffffff", "#000000", 1.0), "#ffffff")
        # Full weight color 2
        self.assertEqual(blend_colors("#ffffff", "#000000", 0.0), "#000000")
        # Midpoint
        mid = blend_colors("#ffffff", "#000000", 0.5)
        self.assertEqual(mid, "#808080")
        # Handles 3-char shorthand
        self.assertEqual(hex_to_rgb("#fff"), (255, 255, 255))

    def test_category_info_and_accent_colors(self) -> None:
        cases = [
            ("bracket.step", "CAD", "cad", "#69E6AD"),
            ("part.stl", "3D Models", "cad", "#69E6AD"),
            ("spindle.nc", "Haas", "cnc", "#D74A4A"),
            ("plate.gcode", "Print Files", "print", "#E8833A"),
            ("lecture.pdf", "School", "pdf", "#FA7183"),
            ("notes.docx", "Documents", "doc", "#61A5FF"),
            ("budget.xlsx", "Spreadsheets", "sheet", "#69E6AD"),
            ("render.png", "Images", "image", "#A855F7"),
            ("clip.mp4", "Videos", "video", "#EC4899"),
            ("track.mp3", "Audio", "audio", "#8B5CF6"),
            ("archive.zip", "Archives", "archive", "#06B6D4"),
            ("installer.exe", "Installers", "installer", "#F43F5E"),
            ("script.py", "Code", "code", "#F5AD62"),
            ("unknown.xyz", "Random", "default", "#69E6AD"),
        ]
        for filename, folder, expected_type, expected_color in cases:
            with self.subTest(file=filename, folder=folder):
                cat_type, color = get_category_info(Path(filename), folder)
                self.assertEqual(cat_type, expected_type)
                self.assertEqual(color, expected_color)
                # Ensure get_accent_color matches
                self.assertEqual(get_accent_color(Path(filename), folder), expected_color)

    def test_render_category_icons(self) -> None:
        categories = [
            "cad", "cnc", "print", "pdf", "doc", "sheet",
            "image", "video", "audio", "archive", "installer", "code", "default"
        ]
        for cat in categories:
            with self.subTest(category=cat):
                img = render_category_icon(cat, "#69E6AD", size=38)
                self.assertIsNotNone(img)
                self.assertEqual(img.size, (38, 38))

    def test_fallback_glyphs_coverage(self) -> None:
        categories = [
            "cad", "cnc", "print", "pdf", "doc", "sheet",
            "image", "video", "audio", "archive", "installer", "code", "default"
        ]
        for cat in categories:
            self.assertIn(cat, CATEGORY_GLYPHS)
            self.assertTrue(len(CATEGORY_GLYPHS[cat]) > 0)

    def test_add_to_recent_docs_safety(self) -> None:
        # Should execute safely without raising exceptions even on mock/non-existent paths
        add_to_recent_docs(Path("C:/test/file.step"))

    def test_dismiss_active_ribbons_signal(self) -> None:
        test_file = Path("C:/Downloads/Part.step")
        popup = RibbonPopup(test_file, enable_animation=False)
        self.assertFalse(popup.is_closing)

        # Trigger dismiss signal after popup has started
        dismiss_active_ribbons(test_file)
        self.assertTrue(RIBBON_DISMISS_FILE.exists())

        popup._check_dismiss_signal()
        self.assertTrue(popup.is_closing)
        try:
            popup.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
