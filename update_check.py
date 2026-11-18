"""Tell the user when a newer Download Sorter has been published.

At most once a day, this asks GitHub's public "latest release" address which
version is newest and compares it with the running version. Nothing about the
user, their computer or their files is sent: the request carries only the app
name and version as its User-Agent, like any web request. Any failure (offline,
GitHub down, a proxy) is silent; the next check simply happens a day later.

Turn it off in Settings ("Check for new versions") or with
``"update_check": {"enabled": false}`` in the config. Setting the environment
variable DOWNLOAD_SORTER_NO_UPDATE_CHECK=1 also turns it off (the tests do).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import about

REPO = "The-Osprey1/Download-Sorter"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
# Files are only ever downloaded from this repository's own release files.
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
CHECK_EVERY_SECONDS = 24 * 60 * 60
TIMEOUT_SECONDS = 5.0
DISABLE_ENV = "DOWNLOAD_SORTER_NO_UPDATE_CHECK"
STATE_NAME = "update_check.json"

# {"version": "v2.2.0", "url": release page, and when the release has them:
#  "installer_url", "installer_name", "size", "checksums_url"}
Release = dict


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

def parse_version(text: str | None) -> tuple[int, ...] | None:
    """'v2.1.0' -> (2, 1, 0). None when there is no number in it."""
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
    old = parse_version(current if current is not None else about.__version__)
    return new is not None and old is not None and new > old


# ---------------------------------------------------------------------------
# Settings and saved state
# ---------------------------------------------------------------------------

def is_enabled(config: dict | None) -> bool:
    if os.environ.get(DISABLE_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    block = (config or {}).get("update_check", {})
    if not isinstance(block, dict):
        return True
    return bool(block.get("enabled", True))


def _state_path() -> Path:
    import sorter

    return Path(sorter.LOG_DIR) / STATE_NAME


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logging.debug("Could not save the update check state", exc_info=True)


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------

def fetch_latest(timeout: float = TIMEOUT_SECONDS) -> Release | None:
    """Ask GitHub for the newest published release. None on any failure."""
    import urllib.request

    request = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{about.APP_NAME.replace(' ', '-')}/{about.__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        logging.debug("Update check failed", exc_info=True)
        return None
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
    config: dict | None,
    now: float | None = None,
    fetch: Callable[[], Release | None] = fetch_latest,
    force: bool = False,
) -> Release | None:
    """The newer release, if there is one. Asks GitHub at most once a day;
    in between it answers from what the last check saw."""
    if not force and not is_enabled(config):
        return None
    now = time.time() if now is None else now
    state = load_state()
    latest = state.get("latest") if isinstance(state.get("latest"), dict) else None
    last = float(state.get("checked_at", 0) or 0)
    if force or now - last >= CHECK_EVERY_SECONDS or now < last:
        fetched = fetch()
        if fetched:
            latest = fetched
        state["checked_at"] = now
        state["latest"] = latest
        _save_state(state)
    if latest and is_newer(latest.get("version")):
        return latest
    return None


def cached_update() -> Release | None:
    """The newer release the last check found, without asking GitHub."""
    latest = load_state().get("latest")
    if isinstance(latest, dict) and is_newer(latest.get("version")):
        return latest
    return None


def should_notify(release: Release) -> bool:
    """True once per new version, so the tray mentions each release only once."""
    state = load_state()
    if state.get("notified") == release.get("version"):
        return False
    state["notified"] = release.get("version")
    _save_state(state)
    return True


def display_version(release: Release) -> str:
    version = str(release.get("version", ""))
    return version[1:] if version[:1] in ("v", "V") else version
