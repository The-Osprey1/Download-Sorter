from __future__ import annotations

import unittest

from ribbon_popup import (
    BASE_GAP,
    BASE_HEIGHT,
    BASE_OFFSET_X,
    BASE_OFFSET_Y,
    BASE_WIDTH,
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


if __name__ == "__main__":
    unittest.main()
