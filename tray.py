"""Tray icon for the download sorter: sort now, undo, pause, the last files filed, open folders.

Run this instead of watch.py and the watcher lives inside it, so there is one
thing to start at login and one icon to click.

    pythonw tray.py
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from PIL import Image, ImageDraw
import tray_qt

import about
import problem_report
import watch
from sorter import (
    HISTORY_FILE,
    IncrementalHistoryReader,
    LOG_DIR,
    LOG_FILE,
    SERVICE_DISABLED_FILE,
    is_paused,
    load_config,
    read_saved_config,
    set_paused,
    setup_logging,
    sort_existing,
    sort_existing_documents,
    watch_root,
    write_runtime_state,
)

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)
    if PYTHON.name.lower() == "pythonw.exe":
        sibling = PYTHON.with_name("python.exe")
        if sibling.exists():
            PYTHON = sibling
_HISTORY_READER = IncrementalHistoryReader(HISTORY_FILE)

# Align with the shared design tokens (suite_theme.py): ACCENTS['downloadSorter'] = #69E6AD
ACCENT = (23, 27, 36)            # dark surface body
ACCENT_LIGHT = (105, 230, 173)   # suite mint tab & arrow
PAUSED_GREY = (101, 112, 132)    # textFaint


def make_icon(paused: bool = False, held_count: int = 0) -> Image.Image:
    """A folder with a down arrow, greyed out while paused, with held badge if held_count > 0."""
    body = PAUSED_GREY if paused else ACCENT
    tab = PAUSED_GREY if paused else ACCENT_LIGHT
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 18, 58, 54), radius=6, fill=body, outline=tab, width=2)
    draw.rounded_rectangle((6, 11, 28, 22), radius=3, fill=tab)
    if paused:
        draw.rectangle((24, 28, 30, 46), fill=(255, 255, 255))
        draw.rectangle((34, 28, 40, 46), fill=(255, 255, 255))
    else:
        draw.polygon([(32, 46), (21, 33), (43, 33)], fill=tab)
        draw.rectangle((28, 24, 36, 34), fill=tab)
    if held_count > 0:
        # Amber/orange indicator badge dot on top-right (COLORS["warning"] #F5AD62)
        badge_fill = (245, 173, 98)
        badge_border = (11, 13, 18)
        draw.ellipse((42, 6, 58, 22), fill=badge_fill, outline=badge_border, width=2)
    return image


def moves_today() -> int:
    """How many files the sorter has filed since midnight."""
    return _HISTORY_READER.moves_today()


def open_in_explorer(path: Path) -> None:
    try:
        os.startfile(str(path))  # noqa: S606 - opening the user's own folder
    except OSError as exc:
        logging.warning("Could not open %s: %s", path, exc)


def reveal_in_explorer(path: Path | str) -> None:
    """Open File Explorer with ``path`` selected (the folder it was in, if the file is gone).

    The path is its own argument after "/select,". Joined into one argument
    ("/select,C:\\a b\\c.pdf"), Windows quotes the whole thing and Explorer then
    ignores it for any path with a space in it.
    """
    target = Path(path)
    try:
        if target.exists():
            subprocess.Popen(["explorer.exe", "/select,", str(target)])
        elif target.parent.exists():
            subprocess.Popen(["explorer.exe", str(target.parent)])
    except OSError as exc:
        logging.warning("Could not show %s in Explorer: %s", target, exc)


def run_script(name: str, *args: str) -> subprocess.CompletedProcess | None:
    """Run one of the sibling command-line tools without flashing a console."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        if getattr(sys, "frozen", False):
            flag_map = {
                "rules_manager.py": ["--gui"],
                "sort_undo.py": ["--undo"],
                "sort_once.py": ["--sort"],
                "status.py": ["--status"],
                "gcode_peek.py": ["--peek"],
                "unzip_3d.py": ["--unzip-3d"],
                "unzip_archive.py": ["--unzip"],
                "clean_old.py": ["--clean"],
            }
            mapped = flag_map.get(name, [name])
            return subprocess.run(
                [sys.executable, *mapped, *args],
                capture_output=True, text=True, creationflags=flags, timeout=300,
            )
        return subprocess.run(
            [str(PYTHON), str(ROOT / name), *args],
            capture_output=True, text=True, creationflags=flags, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logging.warning("Could not run %s: %s", name, exc)
        return None


def _show_shelf_here() -> None:
    try:
        import shelf
        shelf.show_in_process()
    except Exception:
        logging.exception("Could not show the shelf in the tray; opening it on its own")
        spawn_window("--shelf")


def open_shelf(icon=None) -> None:
    """The shelf shortcut was pressed. The tray already runs the window toolkit, so it shows
    the shelf itself (at once); only when it can't is a separate program started."""
    try:
        if icon is not None and icon.run_on_ui(_show_shelf_here):
            return
    except Exception:
        logging.exception("Could not hand the shelf to the tray")
    spawn_window("--shelf")


def spawn_window(*args: str) -> None:
    """Spawn a GUI window asynchronously without blocking the tray process."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    cli_args = list(args)
    if cli_args and cli_args[0] == "rules_manager.py":
        cli_args = cli_args[1:]
        if not cli_args:
            cli_args = ["--gui"]
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, *cli_args]
    else:
        python_bin = Path(sys.executable).resolve()
        if python_bin.name.lower() == "pythonw.exe":
            pass
        elif python_bin.name.lower() == "python.exe":
            pythonw = python_bin.with_name("pythonw.exe")
            if pythonw.exists():
                python_bin = pythonw
        else:
            python_bin = PYTHON
        cmd = [str(python_bin), str(ROOT / "main.py"), *cli_args]
    try:
        subprocess.Popen(cmd, creationflags=flags)
    except Exception as exc:
        logging.warning("Could not spawn window %s: %s", args, exc)


TOOLTIP_MAX = 127

RECENT_LIMIT = 5      # files in the "Recently sorted" menu
RECENT_NAME_MAX = 38  # characters of a file name shown there



def _count_text(count: int, noun: str) -> str:
    """"1 file", "3 files", "No files"."""
    if count == 0:
        return f"No {noun}s"
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _menu_text(text: str) -> str:
    """Text for a menu item. Windows reads "&" as a shortcut mark, so "Terms & Conditions" needs "&&"."""
    return text.replace("&", "&&")


def _shorten_name(name: str, limit: int = RECENT_NAME_MAX) -> str:
    """Cut a long file name in the middle, so the end (its file type) stays visible."""
    if len(name) <= limit:
        return name
    tail = 12
    return f"{name[:limit - tail - 1]}…{name[-tail:]}"


def _recent_label(record: dict) -> str:
    """"invoice.pdf  ·  Invoices & Receipts" for one entry of the recent activity list."""
    name = str(record.get("filename") or Path(str(record.get("dest", ""))).name)
    folder = str(record.get("folder") or "")
    return _menu_text(f"{_shorten_name(name)}  ·  {folder}" if folder else _shorten_name(name))


def _reveal_action(path: str):
    """The menu action for one file: show it in Explorer (the tray calls it with the icon and the item)."""
    def reveal(_icon=None, _item=None) -> None:
        reveal_in_explorer(path)
    return reveal


class SorterTray:
    def __init__(self, wait_for_setup: bool = False) -> None:
        self.wait_for_setup = wait_for_setup
        self.config = load_config()
        self.downloads = watch_root(self.config)
        self.stop_event = threading.Event()
        self.watcher_failed = False
        self._recent_cache: tuple[object, list[dict]] | None = None
        self._recent_lock = threading.Lock()
        self.icon = tray_qt.Icon(
            "download-sorter",
            icon=make_icon(is_paused(), self._held_count()),
            title=self.tooltip(),
            menu=self.build_menu(),
        )

    def current_config(self) -> dict:
        """The settings as they are saved now, for what a menu item is about to do.

        The tray starts with Windows and runs for days, while the window saves changes to the file. It
        used to keep the settings it started with, so after a change of folder Sort Downloads now and
        Open Downloads still used the old one. When the file cannot be read right now (it is being
        written), the settings the tray has stay.
        """
        saved = read_saved_config()
        if saved is not None:
            self.config = saved
            self.downloads = watch_root(saved)
        return self.config

    # ---- presentation ----

    def _held_count(self) -> int:
        try:
            import hold_manager
            return hold_manager.get_held_summary().get("total_held", 0)
        except Exception:
            return 0

    def tooltip(self) -> str:
        if watch.SETUP_PENDING.is_set():
            state = "Waiting for you to finish setup"
        elif watch.WATCH_FOLDER_MISSING.is_set():
            state = "Watch folder not found"
        else:
            state = "Paused" if is_paused() else "Watching Downloads"
        held = self._held_count()
        held_line = f"\n{_count_text(held, '3MF file')} held in Downloads" if held > 0 else ""
        update_line = ""
        try:
            import update_check
            release = update_check.cached_update()
            if release:
                update_line = f"\nVersion {update_check.display_version(release)} is available"
        except Exception:
            pass
        text = f"{about.APP_NAME} - {state}\n{_count_text(moves_today(), 'file')} filed today{held_line}{update_line}"
        with_credit = f"{text}\n{about.CREDIT}"
        # Windows tray tooltips hold at most 127 characters (Windows rejects
        # longer ones), so the credit is the first thing to go.
        if len(with_credit) <= TOOLTIP_MAX:
            return with_credit
        return text[:TOOLTIP_MAX]

    def refresh(self) -> None:
        self.icon.icon = make_icon(is_paused(), self._held_count())
        self.icon.title = self.tooltip()
        self.icon.update_menu()

    def _recent_records(self) -> list[dict]:
        """The last few files filed, newest first.

        The tray builds its menu each time it opens,
        and reading the history is a pass over the whole file, so the answer is kept
        until the history file changes.
        """
        try:
            stat = HISTORY_FILE.stat()
            signature: object = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None
        with self._recent_lock:
            if self._recent_cache is not None and self._recent_cache[0] == signature:
                return self._recent_cache[1]
        try:
            import config_manager
            records = config_manager.get_recent_activity(limit=RECENT_LIMIT)
        except Exception:
            logging.debug("Could not read the recently sorted files", exc_info=True)
            records = []
        with self._recent_lock:
            self._recent_cache = (signature, records)
        return records

    def _recent_menu_items(self) -> list[tray_qt.MenuItem]:
        """The "Recently sorted" submenu: the last files filed, each one opens Explorer on it."""
        items = []
        for record in self._recent_records():
            dest = str(record.get("dest") or "")
            if dest:
                # A file that was moved or deleted since stays listed but greyed out.
                items.append(tray_qt.MenuItem(_recent_label(record), _reveal_action(dest),
                                              enabled=Path(dest).exists()))
        return items or [tray_qt.MenuItem("Nothing sorted yet", None, enabled=False)]

    def build_menu(self) -> tray_qt.Menu:
        return tray_qt.Menu(
            tray_qt.MenuItem("Open Download Sorter", self.on_open_home, default=True),
            tray_qt.MenuItem(
                "Finish setup...",
                self.on_finish_setup,
                visible=lambda _i: watch.SETUP_PENDING.is_set(),
            ),
            tray_qt.SEPARATOR,
            # What you do from here most often sits together at the top.
            tray_qt.MenuItem("Sort Downloads now", self.on_sort_now),
            tray_qt.MenuItem("Undo last sort", self.on_undo),
            tray_qt.MenuItem(
                lambda _i: "Resume sorting" if is_paused() else "Pause sorting",
                self.on_toggle_pause,
            ),
            tray_qt.SEPARATOR,
            # What has happened: the last files filed, and the counts.
            tray_qt.MenuItem("Recently sorted", tray_qt.Menu(lambda: self._recent_menu_items())),
            tray_qt.MenuItem(lambda _i: _count_text(moves_today(), "file") + " filed today", None, enabled=False),
            tray_qt.MenuItem(lambda _i: _count_text(self._held_count(), "3MF file") + " held in Downloads",
                             None, enabled=False, visible=lambda _i: self._held_count() > 0),
            tray_qt.SEPARATOR,
            tray_qt.MenuItem("Rules...", self.on_open_rules_manager),
            tray_qt.MenuItem("Clean up a folder...", self.on_remodel),
            tray_qt.SEPARATOR,
            tray_qt.MenuItem("Open Downloads", self.on_open_downloads),
            tray_qt.MenuItem("Open log folder", self.on_open_logs),
            tray_qt.MenuItem("Report a problem...", self.on_report_problem),
            tray_qt.SEPARATOR,
            tray_qt.MenuItem(f"About {about.APP_NAME}", self.on_about),
            tray_qt.MenuItem(about.CREDIT, None, enabled=False),
            tray_qt.SEPARATOR,
            tray_qt.MenuItem("Quit", self.on_quit),
        )

    def notify(self, message: str, title: str = "Download Sorter") -> None:
        try:
            self.icon.notify(message, title)
        except Exception:  # notifications are a nicety, never a failure
            logging.debug("Tray notification unavailable")

    # ---- menu actions ----

    def on_open_home(self, _icon=None, _item=None) -> None:
        threading.Thread(target=lambda: spawn_window("--home"), daemon=True).start()

    def on_finish_setup(self, _icon=None, _item=None) -> None:
        threading.Thread(target=lambda: spawn_window("--welcome"), daemon=True).start()

    def on_toggle_pause(self, _icon=None, _item=None) -> None:
        paused = not is_paused()
        set_paused(paused)
        watch.WAKE_EVENT.set()
        logging.info("Sorting %s from the tray", "paused" if paused else "resumed")
        self.refresh()
        self.notify(
            "Sorting is paused. New downloads stay in Downloads until you resume."
            if paused else
            "Sorting is on. New downloads are filed a few seconds after they finish."
        )

    def on_sort_now(self, _icon=None, _item=None) -> None:
        def work() -> None:
            config = self.current_config()
            root = watch_root(config)
            if not root.is_dir():
                self.notify(f"The folder to sort was not found: {root}")
                return
            batch_id = uuid.uuid4().hex[:8]
            moved = sort_existing(config, batch_id=batch_id)
            moved += sort_existing_documents(config, batch_id=batch_id)
            self.refresh()
            self.notify(f"Filed {_count_text(moved, 'file')}." if moved else "Nothing needed sorting.")
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

    # These windows stay open as long as the user likes, so they are spawned rather than
    # run as a child process: run_script() has a 5-minute timeout that would kill them.
    def on_open_rules_manager(self, _icon=None, _item=None) -> None:
        spawn_window("rules_manager.py")

    def on_remodel(self, _icon=None, _item=None) -> None:
        spawn_window("rules_manager.py", "--tab", "remodel")

    def on_open_downloads(self, _icon=None, _item=None) -> None:
        self.current_config()
        if not self.downloads.exists():
            self.notify(f"The folder was not found: {self.downloads}")
            return
        open_in_explorer(self.downloads)

    def on_open_logs(self, _icon=None, _item=None) -> None:
        open_in_explorer(LOG_DIR if LOG_DIR.exists() else LOG_FILE.parent)

    def on_report_problem(self, _icon=None, _item=None) -> None:
        if not problem_report.open_issue():
            self.notify("Couldn't open your browser. Report problems at "
                        f"{about.REPO_URL}/issues")

    def on_about(self, _icon=None, _item=None) -> None:
        self.notify(f"{about.__version__} - {about.CREDIT}\n{about.REPO_URL}", f"About {about.APP_NAME}")

    def on_quit(self, _icon=None, _item=None) -> None:
        logging.info("Tray quit requested; stopping the watcher")
        SERVICE_DISABLED_FILE.parent.mkdir(parents=True, exist_ok=True)
        SERVICE_DISABLED_FILE.touch(exist_ok=True)
        self.stop_event.set()
        watch.WAKE_EVENT.set()
        self.icon.stop()

    # ---- lifecycle ----

    def on_sorted(self, count: int) -> None:
        self.refresh()

    def start_watcher(self) -> None:
        def work() -> None:
            try:
                watch.run(
                    stop=self.stop_event,
                    on_sorted=self.on_sorted,
                    wait_for_setup=self.wait_for_setup,
                )
                if not self.stop_event.is_set():
                    raise RuntimeError("Watcher returned without a shutdown request")
            except Exception as exc:
                self.watcher_failed = True
                logging.exception("Watcher stopped unexpectedly")
                write_runtime_state("crashed", f"{type(exc).__name__}: {exc}")
                self.notify("The watcher stopped. See the log for details.")
                self.icon.stop()
        threading.Thread(target=work, daemon=True, name="sorter-watch").start()

    def check_for_update(self) -> None:
        """Once a day at most: mention a newer version, once per version."""
        def work() -> None:
            import update_check
            while True:
                try:
                    release = update_check.check(self.config)
                    if release and update_check.should_notify(release):
                        self.notify(
                            f"Version {update_check.display_version(release)} is available. "
                            "Open Download Sorter to download it."
                        )
                        self.refresh()
                except Exception:
                    logging.debug("Update check failed", exc_info=True)
                if self.stop_event.wait(update_check.CHECK_EVERY_SECONDS):
                    break
        threading.Thread(target=work, daemon=True, name="sorter-update-check").start()

    def on_quit_requested(self) -> None:
        """``DownloadSorter.exe --quit`` (the installer, before an update): close
        like the Quit item, but without turning the background sorter off, so it
        starts again normally afterwards."""
        logging.info("Quit requested by another process (installer); stopping the watcher")
        self.stop_event.set()
        watch.WAKE_EVENT.set()
        self.icon.stop()

    def listen_for_quit(self) -> None:
        try:
            import quit_signal
            self._quit_listener = quit_signal.QuitListener(reset=True)
            self._quit_listener.watch(self.on_quit_requested, self.stop_event)
        except Exception:
            logging.debug("Quit listener unavailable", exc_info=True)

    def run(self) -> int:
        self.start_watcher()
        self.listen_for_quit()
        self.check_for_update()
        self.icon.run()
        self.stop_event.set()
        watch.WAKE_EVENT.set()
        return 1 if self.watcher_failed else 0


def main() -> int:
    if about.handle_version_flag():
        return 0
    setup_logging()
    problem_report.install_crash_handler("tray")
    lock = watch.acquire_single_instance_lock()
    if not lock:
        logging.info("Download Sorter is already running. Opening Home Dashboard.")
        spawn_window("--home")
        return 0
    config = load_config()
    first_run = not config.get("onboarding_completed", False)
    if first_run:
        # Nothing is sorted until the wizard is finished, so the user's profile
        # and "organize existing files" choice apply from the very first move.
        logging.info("First run detected: spawning Welcome Wizard; sorting waits for it.")
        spawn_window("--welcome")
    try:
        import safe_ops
        safe_ops.recover_pending_journal(min_age_seconds=10.0)
    except Exception:
        logging.exception("Failed to run pending journal recovery")
    SERVICE_DISABLED_FILE.unlink(missing_ok=True)
    logging.info("Download Sorter tray starting (PID %s)", os.getpid())
    write_runtime_state("starting", "tray initializing")
    tray_holder: dict = {}
    shelf_cfg = config.get("shelf") if isinstance(config.get("shelf"), dict) else {}
    if shelf_cfg.get("enabled", True):
        spec = str(shelf_cfg.get("hotkey") or "Ctrl+Shift+Space")
        try:
            import hotkey
            if not hotkey.start(lambda: open_shelf(tray_holder.get("icon")), spec=spec):
                logging.info("Recent-files shortcut (%s) not registered", spec)
        except Exception:
            logging.exception("Could not start the recent-files shortcut")
    try:
        sorter_tray = SorterTray(wait_for_setup=first_run)
        tray_holder["icon"] = getattr(sorter_tray, "icon", None)
        result = sorter_tray.run()
    except BaseException as exc:
        logging.exception("Download Sorter tray crashed")
        write_runtime_state("crashed", f"{type(exc).__name__}: {exc}")
        raise
    if result == 0:
        logging.info("Download Sorter tray stopped cleanly")
        write_runtime_state("stopped", "clean shutdown")
    return result


if __name__ == "__main__":
    sys.exit(main())
