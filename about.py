"""Single source of truth for Download Sorter's name, version, and attribution.

Every surface that shows credit (tray, status, Rules Manager, CLI ``--version``,
setup/update scripts, packaging metadata) reads from here so the credit line can
never drift between places.
"""

from __future__ import annotations

APP_NAME = "Download Sorter"
__version__ = "2.1.0"
AUTHOR = "Devyn Fidel"
YEAR = 2026
CREDIT = f"Made by {AUTHOR} ({YEAR})"
REPO_URL = "https://github.com/The-Osprey1/Download-Sorter"
LICENSE_NAME = "MIT"


def version_string() -> str:
    """Return the one-line banner used by every ``--version`` flag."""
    return f"{APP_NAME} {__version__} - {CREDIT}"


def handle_version_flag(argv: list[str] | None = None) -> bool:
    """For scripts without argparse: print the banner and return True on ``--version`` or ``-V``."""
    import sys

    args = sys.argv[1:] if argv is None else argv
    if "--version" in args or "-V" in args:
        print(version_string())
        return True
    return False


def about_text() -> str:
    """Multi-line text for About dialogs."""
    return (
        f"{APP_NAME} {__version__}\n"
        f"{CREDIT}\n\n"
        f"License: {LICENSE_NAME}\n"
        f"Source: {REPO_URL}"
    )
