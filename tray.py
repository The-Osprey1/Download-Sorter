"""Tray icon for the download sorter: pause, sort now, undo, open folders.

Run this instead of watch.py and the watcher lives inside it, so there is one
thing to start at login and one icon to click.

    pythonw tray.py
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw
import pystray

import watch
from sorter import (
    HISTORY_FILE,
    LOG_DIR,
    LOG_FILE,
    is_paused,
    load_config,
    set_paused,
    setup_logging,
    sort_existing,
    sort_existing_documents,
    watch_root,
)

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

ACCENT = (14, 110, 107)      # the folder body
ACCENT_LIGHT = (76, 194, 182)
PAUSED_GREY = (122, 138, 134)


def make_icon(paused: bool) -> Image.Image:
    """A folder with a down arrow, greyed out while paused."""
    body = PAUSED_GREY if paused else ACCENT
    tab = PAUSED_GREY if paused else ACCENT_LIGHT
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 18, 58, 54), radius=6, fill=body)
    draw.rounded_rectangle((6, 11, 28, 22), radius=3, fill=tab)
    if paused:
        draw.rectangle((24, 28, 30, 46), fill=(255, 255, 255))
        draw.rectangle((34, 28, 40, 46), fill=(255, 255, 255))
    else:
        draw.polygon([(32, 46), (21, 33), (43, 33)], fill=(255, 255, 255))
        draw.rectangle((28, 24, 36, 34), fill=(255, 255, 255))
    return image


def moves_today() -> int:
    """How many files the sorter has filed since midnight."""
    if not HISTORY_FILE.exists():
        return 0
    today = datetime.now(timezone.utc).astimezone().date()
    count = 0
    try:
        with HISTORY_FILE.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("action") == "undo":
                    continue
                stamp = record.get("timestamp", "")
                try:
                    when = datetime.fromisoformat(stamp).astimezone().date()
                except ValueError:
                    continue
                if when == today:
                    count += 1
    except OSError:
        return 0
    return count


def open_in_explorer(path: Path) -> None:
    try:
        os.startfile(str(path))  # noqa: S606 - opening the user's own folder
    except OSError as exc:
        logging.warning("Could not open %s: %s", path, exc)


def run_script(name: str, *args: str) -> subprocess.CompletedProcess | None:
    """Run one of the sibling command-line tools without flashing a console."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        return subprocess.run(
            [str(PYTHON), str(ROOT / name), *args],
            capture_output=True, text=True, creationflags=flags, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logging.warning("Could not run %s: %s", name, exc)
        return None


class SorterTray:
    def __init__(self) -> None:
        self.config = load_config()
        self.downloads = watch_root(self.config)
        self.stop_event = threading.Event()
        self.icon = pystray.Icon(
            "download-sorter",
            icon=make_icon(is_paused()),
            title=self.tooltip(),
            menu=self.build_menu(),
        )

    # ---- presentation ----

    def tooltip(self) -> str:
        state = "Paused" if is_paused() else "Watching Downloads"
        return f"Download Sorter - {state}\n{moves_today()} file(s) filed today"

    def refresh(self) -> None:
        self.icon.icon = make_icon(is_paused())
        self.icon.title = self.tooltip()
        self.icon.update_menu()

    def build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(lambda _i: f"{moves_today()} file(s) filed today", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _i: "Resume sorting" if is_paused() else "Pause sorting",
                self.on_toggle_pause,
                default=True,
            ),
            pystray.MenuItem("Sort Downloads now", self.on_sort_now),
            pystray.MenuItem("Undo last batch", self.on_undo),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Downloads", self.on_open_downloads),
            pystray.MenuItem("Open log folder", self.on_open_logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.on_quit),
        )

    def notify(self, message: str, title: str = "Download Sorter") -> None:
        try:
            self.icon.notify(message, title)
        except Exception:  # notifications are a nicety, never a failure
            logging.debug("Tray notification unavailable")

    # ---- menu actions ----

    def on_toggle_pause(self, _icon=None, _item=None) -> None:
        paused = not is_paused()
        set_paused(paused)
        logging.info("Sorting %s from the tray", "paused" if paused else "resumed")
        self.refresh()
        self.notify("Sorting paused." if paused else "Sorting resumed.")

    def on_sort_now(self, _icon=None, _item=None) -> None:
        def work() -> None:
            batch_id = uuid.uuid4().hex[:8]
            moved = sort_existing(self.config, batch_id=batch_id)
            moved += sort_existing_documents(self.config, batch_id=batch_id)
            self.refresh()
            self.notify(f"Filed {moved} file(s)." if moved else "Nothing needed sorting.")
        threading.Thread(target=work, daemon=True).start()

    def on_undo(self, _icon=None, _item=None) -> None:
        def work() -> None:
            result = run_script("sort_undo.py")
            self.refresh()
            if result is None:
                self.notify("Undo could not run - see the log.")
            else:
                tail = (result.stdout or result.stderr or "").strip().splitlines()
                self.notify(tail[-1] if tail else "Nothing to undo.")
        threading.Thread(target=work, daemon=True).start()

    def on_open_downloads(self, _icon=None, _item=None) -> None:
        open_in_explorer(self.downloads)

    def on_open_logs(self, _icon=None, _item=None) -> None:
        open_in_explorer(LOG_DIR if LOG_DIR.exists() else LOG_FILE.parent)

    def on_quit(self, _icon=None, _item=None) -> None:
        logging.info("Tray quit requested; stopping the watcher")
        self.stop_event.set()
        self.icon.stop()

    # ---- lifecycle ----

    def on_sorted(self, count: int) -> None:
        self.refresh()

    def start_watcher(self) -> None:
        def work() -> None:
            try:
                watch.run(stop=self.stop_event, on_sorted=self.on_sorted)
            except Exception:
                logging.exception("Watcher stopped unexpectedly")
                self.notify("The watcher stopped. See the log for details.")
        threading.Thread(target=work, daemon=True, name="sorter-watch").start()

    def run(self) -> int:
        self.start_watcher()
        self.icon.run()
        self.stop_event.set()
        return 0


def main() -> int:
    setup_logging()
    lock = watch.acquire_single_instance_lock()
    if not lock:
        logging.warning("Download Sorter is already running. Exiting.")
        return 0
    return SorterTray().run()


if __name__ == "__main__":
    sys.exit(main())
