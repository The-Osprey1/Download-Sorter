"""Download a newer Download Sorter and hand over to its installer.

The update notice on Home (see update_check.py) offers "Download and install"
when this can work: only in the installed app, never when running from source.

1. ``download`` streams the installer from this repository's GitHub release
   into ``%LOCALAPPDATA%\\download-sorter\\updates``, reporting progress.
2. It then downloads the release's ``checksums.txt`` and checks the file's size
   and SHA-256 against it. A file that doesn't match is deleted, never run.
   (A file downloaded this way has no "came from the internet" mark, so
   Windows SmartScreen won't look at it; this check takes its place.)
3. ``start_install`` starts the installer with ``/S /D=<this folder> /UPDATE``.
   The installer closes the running app itself (``DownloadSorter.exe --quit``),
   replaces the files and opens the app again. Rules and settings live in
   ``%LOCALAPPDATA%\\download-sorter`` and are not touched.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

import update_check

CHUNK = 64 * 1024
READ_TIMEOUT_SECONDS = 30.0
MAX_INSTALLER_BYTES = 512 * 1024 * 1024


class UpdateError(Exception):
    """A download or check failed; the message is shown to the user."""


class Cancelled(UpdateError):
    pass


# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------

def install_dir() -> Path | None:
    """The folder holding DownloadSorter.exe, for the installed app only."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def can_self_update() -> bool:
    """True in the installed app on Windows, in a folder it may write to."""
    folder = install_dir()
    if sys.platform != "win32" or folder is None:
        return False
    return os.access(folder, os.W_OK)


def updates_dir() -> Path:
    import sorter

    return Path(sorter.LOG_DIR) / "updates"


def has_installer(release: dict | None) -> bool:
    return bool(release and release.get("installer_url") and release.get("checksums_url"))


# ---------------------------------------------------------------------------
# Downloading and checking
# ---------------------------------------------------------------------------

def _open(url: str):
    import urllib.request

    if not url.startswith(update_check.DOWNLOAD_PREFIX):
        raise UpdateError("The update isn't hosted on Download Sorter's release page.")
    import about

    request = urllib.request.Request(
        url, headers={"User-Agent": f"{about.APP_NAME.replace(' ', '-')}/{about.__version__}"}
    )
    return urllib.request.urlopen(request, timeout=READ_TIMEOUT_SECONDS)


def expected_sha256(checksums_text: str, file_name: str) -> str | None:
    """The SHA-256 listed for ``file_name`` in a checksums.txt ("<hash>  <name>")."""
    for line in checksums_text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == file_name and len(parts[0]) == 64:
            return parts[0].lower()
    return None


def download(
    release: dict,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Download and check the release's installer. Returns the checked file."""
    if not has_installer(release):
        raise UpdateError("This release has no installer to download.")
    name = str(release["installer_name"])
    if Path(name).name != name or not name.lower().endswith(".exe"):
        raise UpdateError("The installer's name looks wrong.")
    total = int(release.get("size") or 0)
    if total > MAX_INSTALLER_BYTES:
        raise UpdateError("The installer is larger than expected.")

    folder = updates_dir()
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / name
    partial = folder / (name + ".part")
    digest = hashlib.sha256()
    received = 0

    try:
        with _open(str(release["checksums_url"])) as response:
            checksums = response.read(64 * 1024).decode("utf-8", errors="replace")
        wanted = expected_sha256(checksums, name)
        if not wanted:
            raise UpdateError("The release doesn't list a checksum for the installer.")

        with _open(str(release["installer_url"])) as response, partial.open("wb") as out:
            length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
            if not total and length and str(length).isdigit():
                total = int(length)
            if progress:
                progress(0, total)
            while True:
                if cancelled and cancelled():
                    raise Cancelled("Download cancelled.")
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_INSTALLER_BYTES:
                    raise UpdateError("The installer is larger than expected.")
                digest.update(chunk)
                out.write(chunk)
                if progress:
                    progress(received, total)

        if total and received != total:
            raise UpdateError("The download was incomplete.")
        if digest.hexdigest().lower() != wanted:
            raise UpdateError("The download didn't match the published file.")
        os.replace(partial, final)
        return final
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except Exception as exc:
        partial.unlink(missing_ok=True)
        logging.warning("Update download failed: %s", exc)
        raise UpdateError("Couldn't download the update.") from exc


# ---------------------------------------------------------------------------
# Handing over to the installer
# ---------------------------------------------------------------------------

def installer_command(installer: Path, folder: Path) -> list[str]:
    return [str(installer), "/S", f"/D={folder}", "/UPDATE"]


def start_install(installer: Path, folder: Path | None = None) -> None:
    """Start the installer on its own; the caller should then close.

    The installer closes the background sorter and any window itself, so it
    can replace the files, and opens Download Sorter again when it is done.
    """
    folder = folder or install_dir()
    if folder is None:
        raise UpdateError("Only the installed app can update itself.")
    if not Path(installer).is_file():
        raise UpdateError("The downloaded installer is missing.")
    flags = 0
    if sys.platform == "win32":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    subprocess.Popen(installer_command(Path(installer), Path(folder)), creationflags=flags, close_fds=True)


def clean_old_downloads() -> None:
    """Delete downloaded installers for versions that are no longer newer."""
    folder = updates_dir()
    if not folder.is_dir():
        return
    for item in folder.iterdir():
        name = item.name
        found = re.search(r"-v(\d+(?:\.\d+)*)-", name)
        if name.endswith(".part") or not found or not update_check.is_newer(found.group(1)):
            try:
                item.unlink()
            except OSError:
                pass
