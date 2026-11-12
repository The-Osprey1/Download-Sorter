"""A file the rules could not place is offered to the folder the learner recognises (#25)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import learning
import sorter


class FallbackSuggestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "Downloads"
        self.root.mkdir()
        self.config = sorter.get_default_config()
        self.config["watch"] = {"folder": str(self.root)}
        self._patch = patch.object(sorter, "LOG_DIR", str(Path(self._tmp.name) / "log"))
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def _teach_banking(self) -> None:
        banking = self.root / "Banking"
        banking.mkdir()
        for n in range(3):
            moved = banking / f"statement_{n}.pdf"
            moved.write_text("x", encoding="utf-8")
            learning.observe_user_move(moved, self.root, self.config)
        # The third move already offered the rule; start with no question waiting.
        model = learning.load_model()
        model.extra["pending"] = {}
        learning.save_model(model)

    def test_unplaced_file_is_suggested_to_the_learned_folder(self) -> None:
        self._teach_banking()
        other = self.root / "Other"
        other.mkdir()
        new = other / "statement_march.pdf"
        new.write_text("x", encoding="utf-8")

        suggestion = learning.fallback_suggestion(new, self.root, self.config)

        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["folder"], "Banking")
        self.assertIsNotNone(learning.pending_suggestion(suggestion["id"]))

    def test_a_question_already_waiting_is_not_asked_again(self) -> None:
        self._teach_banking()
        model = learning.load_model()
        learning.observe_user_move(self.root / "Banking" / "statement_9.pdf", self.root, self.config)
        other = self.root / "Other"
        other.mkdir()
        new = other / "statement_june.pdf"
        new.write_text("x", encoding="utf-8")
        self.assertIsNone(learning.fallback_suggestion(new, self.root, self.config))

    def test_files_outside_the_default_folder_are_left_alone(self) -> None:
        self._teach_banking()
        placed = self.root / "Documents" / "statement_april.pdf"
        placed.parent.mkdir()
        placed.write_text("x", encoding="utf-8")
        self.assertIsNone(learning.fallback_suggestion(placed, self.root, self.config))


if __name__ == "__main__":
    unittest.main()
