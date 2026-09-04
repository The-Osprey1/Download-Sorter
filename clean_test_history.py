"""Remove pytest-generated records from sorter history after creating a backup."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import tempfile

from sorter import HISTORY_FILE


def is_test_record(record: dict) -> bool:
    values = (str(record.get("src", "")), str(record.get("dest", "")))
    return any("\\Temp\\pytest-of-" in value or "/Temp/pytest-of-" in value for value in values)


def clean_history(path: Path, apply: bool = False) -> tuple[int, int, Path | None]:
    if not path.exists():
        return 0, 0, None
    kept: list[str] = []
    removed = 0
    malformed = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                kept.append(line)
                continue
            if isinstance(record, dict) and is_test_record(record):
                removed += 1
            else:
                kept.append(line)

    if not apply or removed == 0:
        return removed, malformed, None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.writelines(kept)
        temporary = Path(handle.name)
    temporary.replace(path)
    return removed, malformed, backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up and rewrite the live history")
    parser.add_argument("--path", type=Path, default=HISTORY_FILE, help="History file to inspect")
    args = parser.parse_args()
    removed, malformed, backup = clean_history(args.path.resolve(), apply=args.apply)
    action = "Removed" if args.apply else "Would remove"
    print(f"{action} {removed} pytest record(s); preserved {malformed} malformed line(s).")
    if backup:
        print(f"Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
