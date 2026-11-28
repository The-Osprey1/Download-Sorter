"""Words the app shows people use one plain name per feature.

Older names (from the v3 screens that were removed in 4.0.0-beta.3) must not come back in text
that people see: button labels, tooltips, messages and settings. Docstrings, comments, log lines
and code names are not checked.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "download_sorter"

# The old name and the plain name that replaced it.
BANNED: dict[str, str] = {
    r"\bTiers?\b": "folder or level",
    r"Sorting Flow": "Folders",
    r"Folder Architecture": "folders",
    r"\bArchitecture\b": "folders",
    r"Folder Setup": "Folders",
    r"Shift Files": "Tidy",
    r"Clean Up Now": "Tidy",
    r"\bAlign(ed|ing)?\b": "Tidy",
    r"\bDetox\b": "Old files",
    r"Naive Bayes|TF-IDF": "plain words",
    r"Native Messaging": "the Chrome extension",
    r"Custom Rules": "rules",
}

# Files whose strings people see.
UI_FILES = sorted(
    [*(SRC / "ui").rglob("*.py"), SRC / "service.py", SRC / "chrome_bridge.py", SRC / "folder_designer.py"]
)


def _shown_strings(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip: set[int] = set()
    for node in ast.walk(tree):
        # Docstrings
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                skip.add(id(body[0].value))
        # logging.x(...) and log.x(...) calls
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id in {"logging", "log", "logger", "LOG"}:
                for arg in ast.walk(node):
                    skip.add(id(arg))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            text = node.value
            # Style sheets are not shown text.
            if "{" in text and ";" in text:
                continue
            # Only text with a space or a capital letter is shown; keys and ids are not.
            if " " in text.strip() or re.search(r"[A-Z]", text):
                out.append((node.lineno, text))
    return out


@pytest.mark.parametrize("path", UI_FILES, ids=lambda p: p.relative_to(SRC).as_posix())
def test_no_old_feature_names_in_shown_text(path: Path) -> None:
    problems = []
    for line, text in _shown_strings(path):
        for pattern, plain in BANNED.items():
            if re.search(pattern, text):
                problems.append(f"line {line}: {text[:70]!r} (say {plain!r})")
    assert not problems, "\n".join(problems)
