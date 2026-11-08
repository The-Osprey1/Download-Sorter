"""Write the GitHub release notes for a version from CHANGELOG.md.

Used by .github/workflows/release.yml:

    python release_notes.py                                # print notes for about.__version__
    python release_notes.py 2.1.0                          # notes for a given version
    python release_notes.py --output dist/release_notes.md # write them as UTF-8

Writing the file directly avoids Windows' console encoding, which can't
print every character a changelog may contain.

The notes start with short install steps for people who just want the app,
followed by that version's section of the changelog.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import about

ROOT = Path(__file__).resolve().parent

INSTALL_STEPS = """\
## Install

1. Download `DownloadSorter-Setup-v{version}-windows-x64.exe` below.
2. Run it. If Windows shows "Windows protected your PC", click **More info**, then **Run anyway** \
(the app isn't code-signed yet). [How to check your download]({repo}#windows-protected-your-pc)
3. Answer the welcome steps. Nothing is moved until you finish them.

Already using Download Sorter? Run the new installer; your rules and settings are kept.

"""


def changelog_section(text: str, version: str) -> str:
    """The body of ``## [version]`` in the changelog, without its heading."""
    heading = re.compile(rf"^## \[{re.escape(version)}\][^\n]*\n", re.MULTILINE)
    match = heading.search(text)
    if not match:
        raise ValueError(f"CHANGELOG.md has no section for {version}")
    following = re.compile(r"^## \[", re.MULTILINE).search(text, match.end())
    body = text[match.end(): following.start() if following else len(text)]
    body = re.sub(r"\n-{3,}\s*$", "", body.strip()).strip()
    if not body:
        raise ValueError(f"The CHANGELOG.md section for {version} is empty")
    return body


def build_notes(version: str, changelog: str) -> str:
    install = INSTALL_STEPS.format(version=version, repo=about.REPO_URL)
    return install + "## What's new\n\n" + changelog_section(changelog, version) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    output = None
    if "--output" in args:
        at = args.index("--output")
        if at + 1 >= len(args):
            print("error: --output needs a file name", file=sys.stderr)
            return 2
        output = Path(args[at + 1])
        del args[at:at + 2]
    version = (args[0] if args else about.__version__).lstrip("vV")
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    try:
        notes = build_notes(version, text)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if output is None:
        sys.stdout.write(notes)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(notes, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
