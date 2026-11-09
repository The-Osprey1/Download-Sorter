"""Crash reports and pre-filled bug reports (problem_report.py)."""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import about
import problem_report
import sorter

ROOT = Path(__file__).resolve().parent.parent


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _raise_and_catch(message: str = "boom"):
    try:
        raise ValueError(message)
    except ValueError:
        return sys.exc_info()


# ---------------------------------------------------------------------------
# Bug report address
# ---------------------------------------------------------------------------

def test_issue_url_fills_in_version_and_system():
    url = problem_report.issue_url()
    assert url.startswith(f"{about.REPO_URL}/issues/new?")
    q = _query(url)
    assert q["template"] == "bug_report.yml"
    assert q["version"] == about.__version__
    assert f"Download Sorter: {about.__version__}" in q["system"]
    assert "title" not in q and "logs" not in q


def test_issue_url_fields_match_the_bug_report_form():
    """GitHub only fills in fields whose id matches a query parameter."""
    form = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(encoding="utf-8")
    ids = set(re.findall(r"^\s+id:\s*(\S+)", form, flags=re.MULTILINE))
    q = _query(problem_report.issue_url(title="t", description="d", logs="l"))
    for field in ("version", "system", "description", "logs"):
        assert field in q
        assert field in ids, f"bug_report.yml has no field with id '{field}'"


def test_long_logs_are_shortened_and_keep_the_end():
    lines = [f"  File \"x.py\", line {n}, in f{n}" for n in range(2000)]
    logs = "\n".join(lines + ["ValueError: the real error"])
    url = problem_report.issue_url(logs=logs)
    assert len(url) <= problem_report.MAX_URL_LENGTH
    assert _query(url)["logs"].endswith("ValueError: the real error")


def test_one_very_long_line_is_shortened_too():
    url = problem_report.issue_url(logs="x" * 20000 + "END")
    assert len(url) <= problem_report.MAX_URL_LENGTH
    assert _query(url)["logs"].endswith("END")


def test_system_details_has_no_folders_or_user_name(tmp_path):
    details = problem_report.system_details()
    assert str(Path.home()) not in details
    assert str(sorter.LOG_DIR) not in details
    assert "Runs as: From source" in details  # tests never run frozen


# ---------------------------------------------------------------------------
# Crash reports
# ---------------------------------------------------------------------------

def test_crash_report_is_saved_in_the_data_folder():
    path = problem_report.write_crash_report(*_raise_and_catch("disk on fire"), where="tests")
    assert path is not None and path.exists()
    assert path.parent == Path(sorter.LOG_DIR) / "crash-reports"
    text = path.read_text(encoding="utf-8")
    assert "Where: tests" in text
    assert about.__version__ in text
    assert "ValueError: disk on fire" in text
    assert problem_report.recent_crash_reports()[0] == path


def test_only_the_newest_crash_reports_are_kept():
    folder = problem_report.crash_dir()
    folder.mkdir(parents=True)
    for n in range(problem_report.KEEP_CRASH_REPORTS + 5):
        (folder / f"crash-20260101-0000{n:02d}-000000.txt").write_text("old")
    problem_report.write_crash_report(*_raise_and_catch())
    reports = problem_report.recent_crash_reports()
    assert len(reports) == problem_report.KEEP_CRASH_REPORTS
    assert "crash-20260101-000000-" not in " ".join(p.name for p in reports)


def test_ctrl_c_and_exit_are_not_crashes():
    assert problem_report.handle_exception(KeyboardInterrupt, KeyboardInterrupt(), None) is None
    assert problem_report.handle_exception(SystemExit, SystemExit(0), None) is None
    assert problem_report.recent_crash_reports() == []


def test_crash_is_logged(caplog):
    with caplog.at_level("CRITICAL"):
        problem_report.handle_exception(*_raise_and_catch("logged"), where="tests")
    assert "Unexpected error in tests" in caplog.text


def test_no_dialog_in_tests(monkeypatch):
    """A message box would wait for a click forever on the test machine."""
    monkeypatch.setattr(problem_report, "_dialog_shown", False)
    monkeypatch.setattr(problem_report.sys, "platform", "win32")
    opened = []
    monkeypatch.setattr(problem_report, "open_issue", lambda *a, **k: opened.append(a))
    problem_report.handle_exception(*_raise_and_catch(), show_dialog=True)
    assert opened == []
    assert problem_report._dialog_shown is False


# ---------------------------------------------------------------------------
# Hooks (conftest.py puts the original hooks back after each test)
# ---------------------------------------------------------------------------

def test_unhandled_error_in_main_thread_is_saved(capsys):
    problem_report.install_crash_handler("tests", show_dialog=False)
    sys.excepthook(*_raise_and_catch("main thread"))
    [report] = problem_report.recent_crash_reports()
    assert "main thread" in report.read_text(encoding="utf-8")
    # With a console, the error is still printed as usual.
    assert "ValueError: main thread" in capsys.readouterr().err


def test_unhandled_error_in_other_thread_is_saved(monkeypatch):
    problem_report.install_crash_handler("tests", show_dialog=True)
    shown = []
    monkeypatch.setattr(problem_report, "_show_crash_dialog", lambda *a: shown.append(a))

    def fail():
        raise RuntimeError("background task")

    worker = threading.Thread(target=fail, name="worker-1")
    worker.start()
    worker.join()
    [report] = problem_report.recent_crash_reports()
    text = report.read_text(encoding="utf-8")
    assert "RuntimeError: background task" in text
    assert "worker-1" in text
    assert shown == [], "a background error should not interrupt the person"


def test_installing_twice_keeps_one_hook():
    problem_report.install_crash_handler("first", show_dialog=False)
    hook = sys.excepthook
    problem_report.install_crash_handler("second", show_dialog=False)
    assert sys.excepthook is hook
    sys.excepthook(*_raise_and_catch())
    [report] = problem_report.recent_crash_reports()
    assert "Where: second" in report.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Where people find it
# ---------------------------------------------------------------------------

def test_tray_menu_has_report_a_problem():
    source = (ROOT / "tray.py").read_text(encoding="utf-8")
    assert '"Report a problem..."' in source
    assert "problem_report.install_crash_handler" in source


def test_main_installs_the_crash_handler():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "problem_report.install_crash_handler" in source


@pytest.mark.parametrize("name", ["BETA_TESTING.md", ".github/ISSUE_TEMPLATE/beta_feedback.yml",
                                  ".github/ISSUE_TEMPLATE/config.yml"])
def test_beta_files_exist(name):
    assert (ROOT / name).exists()


def test_readme_links_the_beta_guide():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "(BETA_TESTING.md)" in readme


def test_problem_report_is_packaged():
    assert "'problem_report'" in (ROOT / "DownloadSorter.spec").read_text(encoding="utf-8")
    assert '"problem_report"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
