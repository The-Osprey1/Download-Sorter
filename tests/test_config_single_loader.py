"""One settings loader for the watcher and the window (RFC #2).

The window used to recover from a damaged settings file through its .bak copy while the watcher
fell back to defaults, so it kept sorting without your rules. Both now use sorter.load_config_from.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import config_manager
import sorter


class SingleLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.target = self.dir / "config.json"
        self.rules = [{"name": "Banking", "folder": "Documents\\Banking", "match": ["statement"]}]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_damaged_settings_fall_back_to_the_backup_in_both_loaders(self) -> None:
        self.target.write_text("{ not json", encoding="utf-8")
        (self.dir / "config.json.bak").write_text(
            json.dumps({"keyword_rules": self.rules, "extension_rules": []}), encoding="utf-8")

        from_sorter = sorter.load_config_from(self.target)
        from_window = config_manager.load_sorter_config(self.target)

        self.assertEqual(from_sorter["keyword_rules"], self.rules)
        self.assertEqual(from_window["keyword_rules"], self.rules)

    def test_good_settings_are_read_the_same_way(self) -> None:
        self.target.write_text(json.dumps({"keyword_rules": self.rules, "extension_rules": []}), encoding="utf-8")
        self.assertEqual(sorter.load_config_from(self.target), config_manager.load_sorter_config(self.target))

    def test_missing_settings_start_from_the_template_once(self) -> None:
        template = self.dir / "template.json"
        template.write_text(json.dumps({"keyword_rules": self.rules, "extension_rules": []}), encoding="utf-8")
        loaded = sorter.load_config_from(self.target, template)
        self.assertEqual(loaded["keyword_rules"], self.rules)
        self.assertTrue(self.target.exists(), "the template is copied into place")

    def test_no_file_and_no_backup_gives_defaults(self) -> None:
        self.assertEqual(sorter.load_config_from(self.target)["extension_rules"],
                         sorter.get_default_config()["extension_rules"])


if __name__ == "__main__":
    unittest.main()
