"""Regression tests for the first-run fixes found in the 2.0.2 review.

1. The tray sorted Downloads (and Documents) before the welcome wizard was
   finished, so unticking "Organize existing files" had no effect.
2. Personal keyword rules (one student's courses and school) shipped as defaults.
3. 3D/CAD files were pulled out of Documents for every profile, not just makers.
4. The "Recommended for your PC" scan matched keywords inside other words and
   read camera filenames (IMG_1234.jpg) as course codes.
5. The dashboard's Auto-Unpack ZIPs switch saved a setting nothing reads.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

import config_manager
import sorter
import watch


@pytest.fixture
def downloads(tmp_path) -> Path:
    root = tmp_path / "Downloads"
    root.mkdir()
    return root


@pytest.fixture
def config(downloads) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0,
                    "auto_unzip": False, "auto_unpack_3d_zips": False}
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    cfg["hold"]["enabled"] = False
    cfg["batch_handoff"]["enabled"] = False
    cfg["quick_switcher_enabled"] = False
    return cfg


def _make(path: Path, age: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"data")
    if age:
        old = time.time() - age
        os.utime(path, (old, old))
    return path


def _wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


# ---------------------------------------------------------------------------
# 2 + 3. Shipped defaults are generic
# ---------------------------------------------------------------------------

def test_defaults_have_no_personal_keyword_rules():
    cfg = sorter.get_default_config()
    assert cfg["keyword_rules"] == []
    shipped = json.loads((Path(sorter.__file__).parent / "config.json").read_text(encoding="utf-8"))
    assert shipped["keyword_rules"] == []
    for source in (cfg, shipped):
        assert "antigravity" not in source["documents_ignore_dirs"]


def test_everyday_pdf_is_not_filed_as_school_work(config):
    for name in ("Performance Review.pdf", "Eye Exam Results.pdf", "Cal Poly Pomona map.pdf"):
        assert sorter.evaluate_routing(name, config)["destination"] == "Documents\\PDF", name


def test_documents_are_not_scanned_by_default():
    assert sorter.get_default_config()["document_sources"] == []
    shipped = json.loads((Path(sorter.__file__).parent / "config.json").read_text(encoding="utf-8"))
    assert shipped["document_sources"] == []


@pytest.mark.parametrize("persona", ["everyday_home", "academic_student", "office_admin"])
def test_non_maker_profiles_turn_documents_scan_off(persona):
    cfg = sorter.get_default_config()
    cfg["document_sources"] = list(sorter.MAKER_DOCUMENT_SOURCES)
    updated = config_manager.apply_persona_profile(cfg, persona)
    assert updated["document_sources"] == []


def test_maker_profile_turns_documents_scan_on_and_keeps_custom_sources():
    updated = config_manager.apply_persona_profile(sorter.get_default_config(), "maker_3d")
    assert updated["document_sources"] == sorter.MAKER_DOCUMENT_SOURCES

    custom = sorter.get_default_config()
    custom["document_sources"] = ["D:\\Projects"]
    assert config_manager.apply_persona_profile(custom, "maker_3d")["document_sources"] == ["D:\\Projects"]


# ---------------------------------------------------------------------------
# 4. Profile recommendation
# ---------------------------------------------------------------------------

def _recommend(tmp_path: Path, *names: str) -> str:
    folder = tmp_path / "scan"
    folder.mkdir(exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"")
    return config_manager.recommend_persona_profile(folder)["recommended_id"]


def test_photos_and_installers_are_recommended_everyday(tmp_path):
    assert _recommend(
        tmp_path, "IMG_1234.jpg", "IMG_1235.jpg", "DSC_0042.jpg",
        "ChromeSetup.exe", "vacation.mp4", "song.mp3",
    ) == "everyday_home"


@pytest.mark.parametrize("name", [
    "teams_installer.exe",     # "ams"
    "amount_due.pdf",          # "mount"
    "template.docx",           # "plate"
    "decade_photos.zip",       # "cad"
    "syntax_cheatsheet.pdf",   # "tax"
    "usb_adapter_driver.exe",  # part word on a non-archive
])
def test_keywords_inside_other_words_do_not_count(tmp_path, name):
    assert _recommend(tmp_path, name) == "everyday_home"


@pytest.mark.parametrize("name, expected", [
    ("ME3210_syllabus.pdf", "academic_student"),
    ("CS101 notes.pdf", "academic_student"),
    ("bracket_v2.zip", "maker_3d"),
    ("benchy.3mf", "maker_3d"),
    ("Bambu_Lab_A1.pdf", "maker_3d"),
    ("invoice_2026.pdf", "office_admin"),
    ("tax_return_2025.pdf", "office_admin"),
])
def test_real_signals_still_recommend_a_profile(tmp_path, name, expected):
    assert _recommend(tmp_path, name) == expected


# ---------------------------------------------------------------------------
# 1. Existing-files cutoff and waiting for the wizard
# ---------------------------------------------------------------------------

def test_existing_files_cutoff_reads_only_numbers():
    assert sorter.existing_files_cutoff({}) is None
    assert sorter.existing_files_cutoff({"skip_files_before": True}) is None
    assert sorter.existing_files_cutoff({"skip_files_before": "soon"}) is None
    assert sorter.existing_files_cutoff({"skip_files_before": 12.5}) == 12.5


def test_sort_existing_skips_files_from_before_the_cutoff(config, downloads):
    # Windows also records when a file was created, which a test cannot backdate,
    # so put a real gap between the old file, the cutoff and the new file.
    old = _make(downloads / "old_report.pdf")
    time.sleep(0.3)
    cutoff = time.time()
    time.sleep(0.3)
    new = _make(downloads / "new_report.pdf")

    moved = sorter.sort_existing(config, batch_id="t1", skip_before=cutoff)

    assert moved == 1
    assert old.exists()
    assert not new.exists()
    assert (downloads / "Documents" / "PDF" / "new_report.pdf").exists()


def test_explicit_sort_ignores_the_cutoff(config, downloads):
    old = _make(downloads / "old_report.pdf", age=3600)
    config["skip_files_before"] = time.time()
    assert sorter.sort_existing(config, batch_id="t2") == 1
    assert not old.exists()


def _start_watcher(wait_for_setup: bool) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    thread = threading.Thread(
        target=watch.run,
        kwargs={"stop": stop, "wait_for_setup": wait_for_setup},
        daemon=True,
    )
    thread.start()
    return thread, stop


def _stop_watcher(thread: threading.Thread, stop: threading.Event) -> None:
    stop.set()
    watch.WAKE_EVENT.set()
    thread.join(timeout=15)
    assert not thread.is_alive()


def _save(config: dict) -> None:
    path = watch.CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config), encoding="utf-8")
    os.replace(tmp, path)
    watch.WAKE_EVENT.set()


def test_watcher_waits_for_the_wizard_then_sorts(config, downloads):
    config["onboarding_completed"] = False
    _save(config)
    existing = _make(downloads / "existing.pdf", age=3600)

    thread, stop = _start_watcher(wait_for_setup=True)
    try:
        assert _wait_for(watch.SETUP_PENDING.is_set, timeout=5)
        arrived = _make(downloads / "arrived_during_wizard.pdf")
        watch.WAKE_EVENT.set()
        time.sleep(1.5)
        assert existing.exists(), "nothing may be sorted before the wizard is finished"
        assert arrived.exists(), "new downloads wait for the wizard too"

        config["onboarding_completed"] = True
        time.sleep(0.05)  # make sure the file's modified time changes
        _save(config)

        docs = downloads / "Documents" / "PDF"
        assert _wait_for(lambda: (docs / "existing.pdf").exists() and (docs / "arrived_during_wizard.pdf").exists())
        assert not watch.SETUP_PENDING.is_set()
    finally:
        _stop_watcher(thread, stop)


def test_watcher_keeps_existing_files_when_user_said_not_now(config, downloads):
    config["onboarding_completed"] = False
    _save(config)
    existing = _make(downloads / "existing.pdf", age=3600)

    thread, stop = _start_watcher(wait_for_setup=True)
    try:
        assert _wait_for(watch.SETUP_PENDING.is_set, timeout=5)
        config["onboarding_completed"] = True
        config["skip_files_before"] = time.time()
        _save(config)

        assert _wait_for(lambda: not watch.SETUP_PENDING.is_set())
        later = _make(downloads / "later.pdf")
        watch.WAKE_EVENT.set()
        assert _wait_for(lambda: (downloads / "Documents" / "PDF" / "later.pdf").exists())
        assert not later.exists()
        assert existing.exists(), "files that were already there stay put"
    finally:
        _stop_watcher(thread, stop)


def test_watcher_without_wizard_sorts_at_startup(config, downloads):
    """The command-line watcher (no tray, no wizard) behaves as before."""
    config["onboarding_completed"] = False
    _save(config)
    existing = _make(downloads / "existing.pdf", age=3600)

    thread, stop = _start_watcher(wait_for_setup=False)
    try:
        assert _wait_for(lambda: not existing.exists())
        assert not watch.SETUP_PENDING.is_set()
    finally:
        _stop_watcher(thread, stop)


# ---------------------------------------------------------------------------
# 5. Auto-Unpack ZIPs switch
# ---------------------------------------------------------------------------

def test_auto_unzip_lookup_matches_the_sorter():
    import onboarding

    assert onboarding._auto_unzip_enabled({}) is False, "zips are kept as they are unless the user opts in"
    assert onboarding._auto_unzip_enabled({"auto_unzip": False}) is False
    assert onboarding._auto_unzip_enabled({"auto_unzip": True, "watch": {"auto_unzip": False}}) is False


def test_auto_unpack_switch_changes_what_the_sorter_reads(qapp, monkeypatch):
    """The switch lives on the Settings page."""
    import onboarding

    saved: list[dict] = []
    monkeypatch.setattr(onboarding.suite_data, "save_sorter_config", lambda cfg, *a, **k: saved.append(json.loads(json.dumps(cfg))))
    cfg = sorter.get_default_config()
    widget = onboarding.SettingsWidget(config=cfg)
    try:
        widget._on_unpack_toggled(False)
        assert saved, "the switch must save the settings"
        last = saved[-1]
        assert last["watch"]["auto_unzip"] is False
        assert last["auto_unzip"] is False
        assert "auto_unpack_zips" not in last
    finally:
        widget.close()


# ---------------------------------------------------------------------------
# 1b. The wizard's "Organize existing files" choice
# ---------------------------------------------------------------------------

def _finish_wizard(qapp, monkeypatch, sort_now: bool, sorter_status: str | None,
                   unzip: bool | None = None) -> tuple[list[dict], list[dict]]:
    """Finish the wizard; sorter_status None means no background sorter is running."""
    import onboarding

    saved: list[dict] = []
    sorted_calls: list[dict] = []
    monkeypatch.setattr(onboarding.suite_data, "save_sorter_config", lambda cfg, *a, **k: saved.append(json.loads(json.dumps(cfg))))
    monkeypatch.setattr(onboarding.suite_data, "set_startup_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(onboarding.suite_data, "set_context_menu_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(onboarding.suite_data, "is_sorter_running", lambda: sorter_status is not None)
    monkeypatch.setattr(onboarding.sorter, "sort_existing", lambda cfg, **kw: sorted_calls.append(kw) or 0)
    if sorter_status is not None:
        sorter.write_runtime_state(sorter_status, "test")

    dlg = onboarding.WelcomeWizardDialog(config=sorter.get_default_config())
    try:
        dlg._chk_sort_now.setChecked(sort_now)
        if unzip is not None:
            dlg._chk_unzip.setChecked(unzip)
        dlg.accept()
    finally:
        dlg.close()
    return saved, sorted_calls


def test_wizard_not_now_records_cutoff_and_sorts_nothing(qapp, monkeypatch):
    before = time.time()
    saved, sorted_calls = _finish_wizard(qapp, monkeypatch, sort_now=False, sorter_status=None)
    assert saved[-1]["onboarding_completed"] is True
    assert saved[-1]["skip_files_before"] >= before
    assert sorted_calls == []


@pytest.mark.parametrize("sorter_status", [None, "running"])
def test_wizard_sort_now_sorts_as_one_batch_unless_sorter_is_waiting(qapp, monkeypatch, sorter_status):
    """No background sorter, or one already running (tour re-run later): the wizard sorts."""
    saved, sorted_calls = _finish_wizard(qapp, monkeypatch, sort_now=True, sorter_status=sorter_status)
    assert "skip_files_before" not in saved[-1]
    assert len(sorted_calls) == 1
    assert sorted_calls[0].get("batch_id"), "the initial sort must be undoable as one batch"


def test_wizard_sort_now_leaves_it_to_the_waiting_sorter(qapp, monkeypatch):
    saved, sorted_calls = _finish_wizard(qapp, monkeypatch, sort_now=True, sorter_status="waiting_for_setup")
    assert saved[-1]["onboarding_completed"] is True
    assert sorted_calls == [], "the waiting background sorter does the initial sort"


# ---------------------------------------------------------------------------
# 1c. The wizard asks whether to unzip downloads (off unless chosen)
# ---------------------------------------------------------------------------

def test_shipped_default_keeps_zips_as_they_are():
    cfg = sorter.get_default_config()
    assert cfg["auto_unzip"] is False
    assert cfg["watch"]["auto_unzip"] is False


def test_wizard_unzip_choice_starts_off(qapp):
    import onboarding

    dlg = onboarding.WelcomeWizardDialog(config=sorter.get_default_config())
    try:
        assert dlg._chk_unzip.isChecked() is False
    finally:
        dlg.close()


@pytest.mark.parametrize("choice", [True, False])
def test_wizard_saves_the_unzip_choice(qapp, monkeypatch, choice):
    saved, _ = _finish_wizard(qapp, monkeypatch, sort_now=False, sorter_status=None, unzip=choice)
    assert saved[-1]["watch"]["auto_unzip"] is choice
    assert saved[-1]["auto_unzip"] is choice
