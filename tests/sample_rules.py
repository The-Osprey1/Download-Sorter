"""Example keyword rules used by the routing tests.

These used to ship as the app's built-in defaults. The defaults are now generic
(the welcome wizard adds a starter set for the chosen profile), so tests that
exercise keyword routing add these rules to their own config instead.
"""

from __future__ import annotations

import copy

SCHOOL_RULES: list[dict] = [
    {
        "name": "IME 3311 Coursework",
        "folder": "Documents\\School\\IME 3311",
        "match": [
            "ime3311", "ime 3311", "ime-3311", "solidcast",
            "casting congress", "metal casting", "foundry",
        ],
        "extensions": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".rtf", ".odt"],
        "enabled": True,
    },
    {
        "name": "School Documents",
        "folder": "Documents\\School",
        "match": [
            "transcript", "homework", "assignment", "syllabus",
            "calpoly", "cal poly", "pomona",
            "me3210", "me 3210", "exam", "review",
        ],
        "extensions": [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt"],
        "enabled": True,
    },
]


def with_school_rules(config: dict) -> dict:
    """Return config with the example school rules placed first."""
    updated = copy.deepcopy(config)
    updated["keyword_rules"] = copy.deepcopy(SCHOOL_RULES) + list(updated.get("keyword_rules", []))
    return updated
