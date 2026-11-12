"""Export your rules and folder setup as a file that can be copied to another PC (#25).

The export keeps what you set up (rules, folder choices, types, the clean-up and photo options)
and leaves out what is about you or this machine: the Downloads path and other absolute paths
(made relative to %USERPROFILE%), the learning history and its file-name words, the move history,
active projects, and any saved answers. Website names in "sites" rules stay, since a rule needs them.

    python setup_export.py setup.json
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

DROP_KEYS = frozenset({
    "active_project", "pending",
    "declined", "moves", "learned", "history", "last_run",
})


def _profile_root() -> str:
    return os.environ.get("USERPROFILE") or str(Path.home())


def _scrub(value: Any, profile: str) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v, profile) for k, v in value.items() if k not in DROP_KEYS}
    if isinstance(value, list):
        return [_scrub(v, profile) for v in value]
    if isinstance(value, str):
        prefix = profile.rstrip("\\/")
        if prefix and value.lower().startswith(prefix.lower()):
            return "%USERPROFILE%" + value[len(prefix):]
    return value


def shareable_setup(config: dict) -> dict:
    """The setup as a dict, with nothing personal or local left in it."""
    setup = _scrub(copy.deepcopy(config), _profile_root())
    setup["schema_version"] = int(config.get("schema_version", 2) or 2)
    setup["exported_from"] = "Download Sorter setup export"
    return setup


def export(config: dict, out_path: Path | str) -> Path:
    target = Path(out_path)
    target.write_text(json.dumps(shareable_setup(config), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 2
    import sorter
    config = sorter.load_config()
    path = export(config, args[0])
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
