"""Fix a misfiled file: put it back, move it somewhere else, or teach a rule.

The Home page's row menu uses these. There is no Qt in here, so they are easy to
test, and every file move goes through safe_ops like the sorter's own moves:
journaled, never overwriting, and undoable.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config_manager
import learning
import safe_ops
import sort_undo
import sorter


@dataclass
class Outcome:
    """What happened, in words a person can read, and whether it worked."""

    ok: bool
    message: str
    path: Path | None = None        # where the file is now
    suggestion: str | None = None   # id of a rule the learner wants to ask about


# Why safe_ops refuses to touch a file, as the end of "Couldn't move report.pdf: ...".
_WHY = {
    "locked": "it is open in another program",
    "missing": "it is no longer there",
    "temp-save": "it looks like a temporary file",
    "cloud-placeholder": "it is a cloud file that isn't on this PC yet",
    "destination-exists": "a file with that name just appeared there. Try again",
}


def where(folder: Path | str, root: Path | str | None) -> str:
    """A folder as a person reads it: "Downloads", "Documents\\PDF", or a full path outside Downloads."""
    if root is not None:
        try:
            relative = Path(folder).resolve().relative_to(Path(root).resolve())
            return str(relative) if relative.parts else "Downloads"
        except (ValueError, OSError):
            pass
    return str(folder)


def active_record(record_id: str) -> dict[str, Any] | None:
    """The history record of a move that has not been undone, by its id."""
    if not record_id:
        return None
    for record in sort_undo.get_active_moves(sort_undo.load_history()):
        if record.get("id") == record_id:
            return record
    return None


def put_back(record: dict[str, Any], root: Path | str | None = None) -> Outcome:
    """Undo one filed file: bring it back to where it was before it was filed."""
    dest = Path(str(record.get("dest", "")))
    src = Path(str(record.get("src", "")))
    name = dest.name or "That file"
    if not dest.exists():
        return Outcome(False, f"{name} isn't where it was filed any more, so there is nothing to put back.")
    if dest.is_dir() and src.suffix.lower() in safe_ops.ARCHIVE_SUFFIXES:
        return Outcome(False, f"{name} was unpacked from a zip. Undo leaves the unpacked folder as it is; "
                             "the zip itself is in your Archives folder.")
    if sort_undo.undo_moves([record]):
        return Outcome(True, f"Put {name} back in {where(src.parent, root)}.")
    return Outcome(False, f"Couldn't put {name} back. It may be open in another program; "
                          "try again once it's closed.")


def move_to_folder(
    record: dict[str, Any],
    dest_dir: Path | str,
    config: dict[str, Any] | None,
    root: Path | str | None,
) -> Outcome:
    """Move a filed file to a folder you choose, and let the learner see that you chose it.

    The move is journaled (so it can be undone) and never replaces a file: if the
    name is taken there, the file gets a number.
    """
    current = Path(str(record.get("dest", "")))
    name = current.name or "That file"
    target_dir = Path(dest_dir)
    if not current.is_file():
        return Outcome(False, f"{name} isn't where it was filed any more, so it can't be moved.")
    try:
        if target_dir.resolve() == current.parent.resolve():
            return Outcome(False, f"{name} is already in {where(target_dir, root)}.")
    except OSError:
        pass

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        moved = safe_ops.move(
            current, target_dir / current.name,
            batch_id=f"manual-{uuid.uuid4().hex[:8]}",
            settle_seconds=0.0,   # it was filed a while ago; you are asking for this
            honor_keep=False,
        )
    except safe_ops.Refused as refusal:
        return Outcome(False, f"Couldn't move {name}: {_WHY.get(refusal.reason, refusal.reason)}.")
    except OSError as exc:
        return Outcome(False, f"Couldn't move {name}: {exc.strerror or exc}.")

    # You chose this folder, so the sorter must leave the file there (as for a move you make in Explorer).
    try:
        sorter.remember_user_placed(moved)
    except Exception:
        logging.debug("Could not mark %s as placed by you", moved, exc_info=True)

    suggestion_id: str | None = None
    if root is not None and sorter.is_under(moved, Path(root).resolve()):
        try:
            learned = learning.observe_user_move(moved, Path(root), config or {})
            suggestion_id = ((learned or {}).get("suggestion") or {}).get("id")
        except Exception:
            logging.debug("Learning from a move failed", exc_info=True)
    return Outcome(True, f"Moved {moved.name} to {where(moved.parent, root)}.", moved, suggestion_id)


def ask_about_suggestion(suggestion_id: str, config: dict[str, Any] | None = None) -> None:
    """Show the ribbon "Put files like this in Banking?" for a rule the learner suggested."""
    cfg = config or {}
    if not (cfg.get("ribbon_enabled", True) and cfg.get("notifications", True)):
        return
    try:
        from ribbon_popup import notify_suggestion
        notify_suggestion(suggestion_id)
    except Exception as err:  # a ribbon that can't show must never get in the way
        logging.debug("Rule suggestion ribbon skipped: %s", err)


def rule_draft_for(filename: str, dest_dir: Path | str, root: Path | str | None) -> dict[str, Any]:
    """A starting point for "Always file like this": a word and the type of this file, and the folder it is in."""
    words = [w for w in learning.name_words(filename) if len(w) >= 3 and not w.isdigit()]
    word = words[0] if words else ""
    extension = Path(filename).suffix.lower()
    if word:
        name = f"{word.capitalize()} files"
    elif extension:
        name = f"{extension.lstrip('.').upper()} files"
    else:
        name = "My rule"
    return {
        "name": name,
        "folder": where(dest_dir, root) if str(dest_dir) else "",  # "" when there is no folder yet
        "match": [word] if word else [],
        "extensions": [extension] if extension else [],
        "enabled": True,
    }


def add_rule_first(rule: dict[str, Any], config_path: Path | str | None = None) -> dict[str, Any]:
    """Add a name rule at the top of the list, so it wins, and save the settings.

    A rule that is already there (same folder, words, types and websites) is
    switched on and moved to the top instead of being added twice.
    """
    config = config_manager.load_sorter_config(config_path)
    rules = list(config.get("keyword_rules", []))

    def same(existing: dict[str, Any]) -> bool:
        return (existing.get("folder") == rule.get("folder")
                and list(existing.get("match", [])) == list(rule.get("match", []))
                and list(existing.get("extensions", [])) == list(rule.get("extensions", []))
                and list(existing.get("sites", [])) == list(rule.get("sites", [])))

    stored = dict(rule)
    for existing in rules:
        if same(existing):
            existing["enabled"] = True
            rules.remove(existing)
            stored = existing
            break
    rules.insert(0, stored)
    config["keyword_rules"] = rules
    if not config_manager.save_sorter_config(config, config_path):
        raise OSError("The settings file couldn't be saved. Check that it isn't open in another program.")
    return stored
