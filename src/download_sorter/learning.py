"""Lightweight rule-learning tally for manual moves and Chrome re-routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from download_sorter.config import Config
from download_sorter.dest import to_relative_dest
from download_sorter.router import extract_host


def _store_path(data_dir: Path) -> Path:
    return data_dir / "learned_routes.json"


def _load_store(data_dir: Path) -> dict[str, Any]:
    path = _store_path(data_dir)
    if not path.is_file():
        return {"tallies": {}, "dismissed": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("tallies", {})
            data.setdefault("dismissed", [])
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"tallies": {}, "dismissed": []}


def _save_store(data_dir: Path, data: dict[str, Any]) -> None:
    path = _store_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def record_manual_route(
    *,
    destination: str,
    data_dir: Path,
    source_url: str = "",
    host: str = "",
    ext: str = "",
) -> None:
    """Record a manual file move/re-route so repeated patterns can suggest a rule."""
    clean_dest = to_relative_dest(destination, fallback="")
    if not clean_dest:
        return
    clean_host = extract_host(host or source_url)
    clean_ext = ext.strip().lower()
    if clean_ext and not clean_ext.startswith("."):
        clean_ext = "." + clean_ext

    store = _load_store(data_dir)
    tallies: dict[str, dict[str, Any]] = store["tallies"]

    if clean_host:
        key = f"host:{clean_host}->{clean_dest.lower()}"
        entry = tallies.get(key) or {
            "kind": "host",
            "host": clean_host,
            "ext": "",
            "destination": clean_dest,
            "count": 0,
        }
        entry["count"] = int(entry.get("count", 0)) + 1
        tallies[key] = entry
    elif clean_ext:
        key = f"ext:{clean_ext}->{clean_dest.lower()}"
        entry = tallies.get(key) or {
            "kind": "ext",
            "host": "",
            "ext": clean_ext,
            "destination": clean_dest,
            "count": 0,
        }
        entry["count"] = int(entry.get("count", 0)) + 1
        tallies[key] = entry

    _save_store(data_dir, store)


def get_rule_suggestions(
    cfg: Config,
    data_dir: Path,
    *,
    threshold: int = 2,
) -> list[dict[str, Any]]:
    """Return learned rule suggestions that meet ``threshold`` and aren't already rules."""
    store = _load_store(data_dir)
    dismissed = set(store.get("dismissed") or [])
    tallies: dict[str, dict[str, Any]] = store.get("tallies") or {}

    suggestions: list[dict[str, Any]] = []
    for key, entry in tallies.items():
        if key in dismissed:
            continue
        count = int(entry.get("count", 0))
        if count < threshold:
            continue
        dest = to_relative_dest(str(entry.get("destination") or ""), fallback="")
        if not dest:
            continue
        host = str(entry.get("host") or "").strip().lower()
        ext = str(entry.get("ext") or "").strip().lower()

        already_covered = any(
            r.enabled
            and to_relative_dest(r.destination, fallback="").lower() == dest.lower()
            and ((host and host in [extract_host(d) for d in r.domains]) or (ext and ext in r.extensions))
            for r in cfg.rules
        )
        if already_covered:
            continue

        label = f"files from {host}" if host else f"{ext} files"
        suggestions.append(
            {
                "key": key,
                "kind": entry.get("kind", "host" if host else "ext"),
                "host": host,
                "ext": ext,
                "destination": dest,
                "count": count,
                "summary": f"You moved {count} {label} to '{dest.replace(chr(92), '/')}'",
            }
        )

    return sorted(suggestions, key=lambda s: s["count"], reverse=True)


def dismiss_suggestion(key: str, data_dir: Path) -> None:
    """Persistently dismiss a learned rule suggestion."""
    store = _load_store(data_dir)
    dismissed = list(store.get("dismissed") or [])
    if key not in dismissed:
        dismissed.append(key)
        store["dismissed"] = dismissed
        _save_store(data_dir, store)
