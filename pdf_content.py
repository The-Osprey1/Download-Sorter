"""Tell apart PDFs that all look alike by the words at the top of their first page (#25).

Bank statements, tax forms, shipping labels and manuals otherwise all land in Documents\\PDF.
Only the first lines of text are read (sorter.pdf_head_text), and only when PDF text reading is
turned on in Settings, because it needs pypdf and opens the file.
"""

from __future__ import annotations

import re

# (folder, phrases). A folder is chosen when at least one phrase appears; the folder with the
# most phrases wins. Folders are inside Documents, like the other document rules.
CONTENT_FOLDERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Documents\\Statements", (
        r"statement period", r"statement date", r"account summary", r"opening balance",
        r"closing balance", r"new balance", r"minimum payment due",
    )),
    ("Documents\\Tax", (
        r"form 1099", r"\bw-2\b", r"\bw-4\b", r"form 1040", r"tax year", r"internal revenue service",
        r"notice of assessment", r"\bsales tax\b",
    )),
    ("Documents\\Shipping", (
        r"tracking number", r"shipping label", r"ship to:", r"packing slip", r"\bups ground\b",
        r"\bfedex\b", r"\busps\b", r"return label",
    )),
    ("Documents\\Manuals", (
        r"user manual", r"owner'?s manual", r"quick start guide", r"installation guide",
        r"table of contents", r"safety information",
    )),
)

_COMPILED = [
    (folder, [re.compile(p) for p in phrases]) for folder, phrases in CONTENT_FOLDERS
]


def folder_for_text(text: str) -> str | None:
    """The folder for a PDF whose first page reads like ``text`` (lowercased), or None."""
    if not text:
        return None
    best: tuple[int, str] | None = None
    for folder, patterns in _COMPILED:
        hits = sum(1 for pattern in patterns if pattern.search(text))
        if hits and (best is None or hits > best[0]):
            best = (hits, folder)
    return best[1] if best else None
