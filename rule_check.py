"""Checks for a rule being saved (#25): will it ever run, and what does it already catch?

* ``shadowed_by`` names the earlier rules that take every file this rule would take, so the
  rule can never run (the rules are checked top to bottom, the first match wins).
* ``matching_downloads`` lists the loose files in Downloads that the rule would file right now.
* ``move_matching`` files them, through safe_ops, so Undo still works.
"""

from __future__ import annotations

import logging
from pathlib import Path


def _words(rule: dict) -> list[str]:
    return [str(w).lower() for w in rule.get("match", []) if str(w).strip()]


def _exts(rule: dict) -> set[str]:
    return {str(e).lower() for e in rule.get("extensions", []) if str(e).strip()}


def shadowed_by(rules: list[dict], index: int) -> list[str]:
    """Names of enabled earlier name rules that catch every file ``rules[index]`` would catch."""
    rule = rules[index]
    if not rule.get("enabled", True) or not _words(rule):
        return []
    found: list[str] = []
    for earlier in rules[:index]:
        if not earlier.get("enabled", True) or earlier.get("sites") or not _words(earlier):
            continue
        # Every word the rule looks for contains one of the earlier rule's words, so any file
        # the rule would match already contains an earlier word.
        covers = all(any(e in word for e in _words(earlier)) for word in _words(rule))
        earlier_types = _exts(earlier)
        types_ok = not earlier_types or (_exts(rule) and _exts(rule) <= earlier_types)
        if covers and types_ok:
            found.append(str(earlier.get("name") or earlier.get("folder") or "an earlier rule"))
    return found


def matching_downloads(config: dict, rule: dict) -> list[Path]:
    """Loose files in Downloads that the rule would file now (judged with ``config``)."""
    import sorter

    root = sorter.watch_root(config)
    found: list[Path] = []
    if not root.is_dir():
        return found
    for path in root.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            routing = sorter.evaluate_routing(path.name, config, file_path=path)
        except Exception:
            continue
        if routing.get("winning_rule") == rule.get("name") and routing.get("match_type") == "keyword":
            found.append(path)
    return found


def move_matching(config: dict, rule: dict, batch_id: str = "rule_move") -> int:
    """File the matching loose downloads into the rule's folder. Returns how many moved."""
    import sorter
    import safe_ops

    target_dir = sorter.folder_path(sorter.watch_root(config), str(rule.get("folder") or "Other"))
    moved = 0
    for path in matching_downloads(config, rule):
        try:
            safe_ops.move(path, sorter.unique_destination(target_dir, path.name), batch_id=batch_id,
                          settle_seconds=0.0, honor_keep=False, skip_preflight=True)
            moved += 1
        except (OSError, safe_ops.Refused) as exc:
            logging.info("Left %s in Downloads: %s", path.name, exc)
    return moved
