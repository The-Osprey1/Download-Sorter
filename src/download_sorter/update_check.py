"""Tell the user when a newer Download Sorter has been published.

At most once a day, this asks GitHub's public "latest release" address which
version is newest and compares it with the running version. Nothing about the
user, their computer or their files is sent: the request carries only the app
name and version as its User-Agent, like any web request. Any failure (offline,
GitHub down, a proxy) is silent; the next check simply happens a day later.

Turn it off in Settings ("Check for new versions"), with ``"check_for_updates":
false`` in the config, or by setting the environment variable
DOWNLOAD_SORTER_NO_UPDATE_CHECK=1.

Pre-releases and drafts are never offered: GitHub's "latest" address skips
them, and ``fetch_latest`` ignores any that slip through.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any

from download_sorter.about import __version__

APP_AGENT = "Download-Sorter"
REPO = "The-Osprey1/Download-Sorter"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
# Files are only ever downloaded from this repository's own release files.
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
CHECK_EVERY_SECONDS = 24 * 60 * 60
TIMEOUT_SECONDS = 5.0
DISABLE_ENV = "DOWNLOAD_SORTER_NO_UPDATE_CHECK"
STATE_NAME = "update_check.json"

# {"version": "v3.0.1", "url": release page, and when the release has them:
#  "installer_url", "installer_name", "size", "checksums_url"}
Release = dict


def user_agent() -> str:
    return f"{APP_AGENT}/{__version__}"


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------
def parse_version(text: str | None) -> tuple[int, ...] | None:
    """'v3.0.1' -> (3, 0, 1). None when there is no number in it."""
    if not text:
        return None
    numbers = re.findall(r"\d+", str(text).split("-")[0].split("+")[0])
    if not numbers:
        return None
    parts = [int(n) for n in numbers[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(latest: str | None, current: str | None = None) -> bool:
    new = parse_version(latest)
    old = parse_version(current if current is not None else __version__)
    return new is not None and old is not None and new > old


def display_version(release: Release) -> str:
    version = str(release.get("version", ""))
    return version[1:] if version[:1] in ("v", "V") else version


# ---------------------------------------------------------------------------
# Settings and saved state
# ---------------------------------------------------------------------------
def is_enabled(check_for_updates: bool = True) -> bool:
    if os.environ.get(DISABLE_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    return bool(check_for_updates)


def state_path(data_dir: Path) -> Path:
    return Path(data_dir) / STATE_NAME


def load_state(data_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads(state_path(data_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(data_dir: Path, state: dict[str, Any]) -> None:
    path = state_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.part")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logging.debug("Could not save the update check state", exc_info=True)


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------
def release_from_api(data: Any) -> Release | None:
    """Turn GitHub's "release" answer into what the app needs, or None to ignore it."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    version = str(data.get("tag_name") or "")
    if parse_version(version) is None:
        return None
    url = str(data.get("html_url") or RELEASES_PAGE)
    if not url.startswith("https://github.com/"):
        url = RELEASES_PAGE
    release: Release = {"version": version, "url": url}
    release.update(_installer_assets(data.get("assets"), version))
    return release


def fetch_latest(timeout: float = TIMEOUT_SECONDS) -> Release | None:
    """Ask GitHub for the newest published release. None on any failure."""
    import urllib.request

    request = urllib.request.Request(
        RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": user_agent()},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        logging.debug("Update check failed", exc_info=True)
        return None
    return release_from_api(data)


def installer_name(version: str) -> str:
    return f"DownloadSorter-Setup-v{display_version({'version': version})}-windows-x64.exe"


def _installer_assets(assets: Any, version: str) -> dict[str, Any]:
    """The installer and checksums.txt of a release, when both are there and
    hosted on this repository's own release downloads."""
    if not isinstance(assets, list):
        return {}
    wanted = installer_name(version)
    found: dict[str, Any] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        link = str(asset.get("browser_download_url") or "")
        if not link.startswith(DOWNLOAD_PREFIX):
            continue
        if name == wanted:
            try:
                size = int(asset.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            found.update({"installer_url": link, "installer_name": name, "size": size})
        elif name == "checksums.txt":
            found["checksums_url"] = link
    if "installer_url" not in found or "checksums_url" not in found:
        return {}
    return found


def check(
    data_dir: Path,
    *,
    enabled: bool = True,
    now: float | None = None,
    fetch: Callable[[], Release | None] | None = None,
    force: bool = False,
) -> Release | None:
    """The newer release, if there is one. Asks GitHub at most once a day;
    in between it answers from what the last check saw."""
    if not force and not is_enabled(enabled):
        return None
    now = time.time() if now is None else now
    state = load_state(data_dir)
    latest = state.get("latest") if isinstance(state.get("latest"), dict) else None
    last = float(state.get("checked_at", 0) or 0)
    if force or now - last >= CHECK_EVERY_SECONDS or now < last:
        fetched = (fetch or fetch_latest)()
        if fetched:
            latest = fetched
        state["checked_at"] = now
        state["latest"] = latest
        _save_state(data_dir, state)
    if latest and is_newer(latest.get("version")):
        return latest
    return None


def cached_update(data_dir: Path) -> Release | None:
    """The newer release the last check found, without asking GitHub."""
    latest = load_state(data_dir).get("latest")
    if isinstance(latest, dict) and is_newer(latest.get("version")):
        return latest
    return None
