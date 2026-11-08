"""Stage 1 of the learning sorter: watch, learn from the user's own moves, log guesses.

Also covers the fix that makes it possible: a file the user moves into a folder
inside Downloads stays there, instead of being treated as a misplaced download
and moved straight back out (immediately, or by the next startup sweep).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from watchdog.events import FileMovedEvent

import learning
import safe_ops
import sorter
import watch


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def downloads(tmp_path) -> Path:
    root = tmp_path / "Downloads"
    root.mkdir()
    return root.resolve()


@pytest.fixture
def config(downloads) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0,
                    "auto_unzip": False, "auto_unpack_3d_zips": False}
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    cfg["quick_switcher_enabled"] = False
    cfg["hold"]["enabled"] = False
    return cfg


@pytest.fixture
def learning_on(config) -> dict:
    config["learning"] = {"enabled": True}
    return config


@pytest.fixture
def sites(monkeypatch) -> dict[str, str]:
    """Pretend Windows recorded a download website for some files: {file name: site}."""
    known: dict[str, str] = {}

    def fake_zone(path: Path) -> str:
        site = known.get(Path(path).name)
        return f"[ZoneTransfer]\nZoneId=3\nReferrerUrl=https://www.{site}/page\n" if site else ""

    monkeypatch.setattr(learning, "_read_zone_identifier", fake_zone)
    return known


def _old_file(path: Path, text: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    past = time.time() - 60
    os.utime(path, (past, past))
    return path


def _user_move(handler: watch.DownloadsHandler, src: Path, dest: Path) -> None:
    """Move a file the way a person would in File Explorer, then deliver the watcher event."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dest)
    handler.on_moved(FileMovedEvent(str(src), str(dest)))


# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, words", [
    ("Bank_StatementsOct2026.pdf", ["bank", "statement"]),
    ("GoogleChromeSetup (2).exe", ["google", "chrome", "setup"]),
    ("Invoices-March_final_v2.pdf", ["invoice"]),
    ("IMG_4821.jpg", []),
    ("ME3210 Lab Reports.docx", ["me", "lab", "report"]),
])
def test_name_words(name, words):
    assert learning.name_words(name) == words


@pytest.mark.parametrize("text, site", [
    ("[ZoneTransfer]\nZoneId=3\nReferrerUrl=https://www.yourbank.com/statements\n"
     "HostUrl=https://cdn.files.net/x.pdf", "yourbank.com"),
    ("[ZoneTransfer]\nZoneId=3\nHostUrl=https://www.printables.com/model/1/files", "printables.com"),
    ("[ZoneTransfer]\nZoneId=3\nHostUrl=about:internet", None),
    ("[ZoneTransfer]\nZoneId=3\nReferrerUrl=file:///C:/x\nHostUrl=https://canvas.school.edu/f/1", "canvas.school.edu"),
    ("", None),
])
def test_parse_zone_identifier_keeps_only_the_site_name(text, site):
    assert learning.parse_zone_identifier(text) == site


def test_features_include_words_type_and_site(tmp_path, sites):
    f = _old_file(tmp_path / "Statement_Nov.pdf")
    sites[f.name] = "yourbank.com"
    assert learning.features(f) == ["w:statement", "x:.pdf", "s:yourbank.com"]


def test_source_site_is_none_without_a_note(tmp_path):
    assert learning.source_site(_old_file(tmp_path / "plain.pdf")) is None


# ---------------------------------------------------------------------------
# the learner
# ---------------------------------------------------------------------------

def test_model_predicts_only_folders_it_has_seen():
    model = learning.Model()
    assert model.predict(["w:statement"]) == []
    for _ in range(3):
        model.learn(["w:statement", "x:.pdf", "s:yourbank.com"], "Banking")
    for _ in range(2):
        model.learn(["w:receipt", "x:.pdf", "s:shop.com"], "Receipts")

    guesses = model.predict(["w:statement", "x:.pdf", "s:yourbank.com"])
    assert [g["folder"] for g in guesses] == ["Banking", "Receipts"]
    assert guesses[0]["confidence"] > 0.9

    other = model.predict(["w:receipt", "x:.pdf", "s:shop.com"])
    assert other[0]["folder"] == "Receipts"


def test_folder_spellings_count_as_one_folder():
    model = learning.Model()
    model.learn(["w:a"], "Banking")
    model.learn(["w:a"], "banking")
    assert list(model.folders) == ["banking"]
    assert model.folders["banking"]["examples"] == 2


# ---------------------------------------------------------------------------
# telling the user's moves from the program's
# ---------------------------------------------------------------------------

def test_user_move_between_folders_is_recognized(downloads, config):
    src = _old_file(downloads / "Documents" / "PDFs" / "statement.pdf")
    dest = downloads / "Banking" / "statement.pdf"
    dest.parent.mkdir()
    os.replace(src, dest)
    assert learning.is_user_move(src, dest, downloads, config)


def test_program_moves_are_not_user_moves(downloads, config):
    src = _old_file(downloads / "statement.pdf")
    final = safe_ops.move(src, downloads / "Documents" / "PDFs" / "statement.pdf", settle_seconds=0.0)
    assert not learning.is_user_move(src, final, downloads, config)


def test_renames_partial_files_and_outside_moves_are_ignored(downloads, tmp_path, config):
    a = _old_file(downloads / "report.pdf")
    renamed = downloads / "report-final.pdf"
    os.replace(a, renamed)
    assert not learning.is_user_move(a, renamed, downloads, config)  # same folder

    part = _old_file(downloads / "Banking" / "movie.mp4.crdownload")
    assert not learning.is_user_move(downloads / "x.crdownload", part, downloads, config)

    outside = _old_file(tmp_path / "elsewhere" / "x.pdf")
    assert not learning.is_user_move(downloads / "x.pdf", outside, downloads, config)


# ---------------------------------------------------------------------------
# the bug fix: user moves are respected
# ---------------------------------------------------------------------------

def test_file_moved_into_own_folder_is_not_moved_back(downloads, config):
    handler = watch.DownloadsHandler(downloads, config)
    src = _old_file(downloads / "Documents" / "PDFs" / "Bank statement.pdf")
    dest = downloads / "Banking" / "Bank statement.pdf"
    _user_move(handler, src, dest)

    # Not queued as a "misplaced download", and a sweep or reclassify leaves it be.
    assert dest not in watch.PENDING_MISPLACED
    assert sorter.reclassify_misplaced_file(dest, config) is None
    sorter.sort_existing_misplaced(config, batch_id="t")
    assert dest.exists()


def test_file_moved_into_another_category_survives_the_startup_sweep(downloads, config):
    handler = watch.DownloadsHandler(downloads, config)
    src = _old_file(downloads / "Documents" / "PDFs" / "car-photos-list.pdf")
    dest = downloads / "Images" / "car-photos-list.pdf"
    _user_move(handler, src, dest)

    sorter.sort_existing_misplaced(config, batch_id="t")
    assert dest.exists(), "the startup sweep moved the user's file back out"


def test_user_placed_file_stays_protected_after_edits(downloads, config):
    handler = watch.DownloadsHandler(downloads, config)
    src = _old_file(downloads / "Documents" / "PDFs" / "notes.pdf")
    dest = downloads / "Banking" / "notes.pdf"
    _user_move(handler, src, dest)
    dest.write_text("edited later", encoding="utf-8")
    assert sorter.is_restored_by_undo(dest)


def test_new_downloads_into_a_subfolder_are_still_rescued(downloads, config):
    """A browser saving straight into an old subfolder is a creation, not a user move."""
    handler = watch.DownloadsHandler(downloads, config)
    f = _old_file(downloads / "OldFolder" / "report.pdf")
    handler.on_created(type("E", (), {"is_directory": False, "src_path": str(f)})())
    assert f in watch.PENDING_MISPLACED


# ---------------------------------------------------------------------------
# learning switched off / on
# ---------------------------------------------------------------------------

def test_learning_is_off_by_default_and_stores_nothing(downloads, config):
    assert not learning.is_enabled(config)
    handler = watch.DownloadsHandler(downloads, config)
    src = _old_file(downloads / "Documents" / "PDFs" / "statement.pdf")
    _user_move(handler, src, downloads / "Banking" / "statement.pdf")
    assert not (Path(sorter.LOG_DIR) / learning.LEARNING_FILE_NAME).exists()
    assert not (Path(sorter.LOG_DIR) / learning.LOG_FILE_NAME).exists()


def test_learning_from_moves_and_logging_guesses(downloads, learning_on, sites):
    handler = watch.DownloadsHandler(downloads, learning_on)
    for i in range(3):
        name = f"Statement_{i}.pdf"
        sites[name] = "yourbank.com"
        src = _old_file(downloads / "Documents" / "PDFs" / name)
        _user_move(handler, src, downloads / "Banking" / name)

    log = learning.read_log()
    assert [r["kind"] for r in log] == ["your_move"] * 3
    assert log[0]["counted"] is False          # nothing known yet, so not judged
    assert log[1]["first_guess_right"] is True
    assert log[2]["in_top_three"] is True
    assert log[2]["moved_to"] == "Banking"

    model = learning.load_model()
    assert model.folders["banking"]["examples"] == 3
    assert model.folders["banking"]["features"]["s:yourbank.com"] == 3

    # A new download sorted by the rules: it logs what it would have suggested.
    sites["Statement_9.pdf"] = "yourbank.com"
    new = _old_file(downloads / "Statement_9.pdf")
    dest = sorter.move_file(new, learning_on)
    watch._handle_post_move(dest, downloads, learning_on)
    sorted_rec = learning.read_log()[-1]
    assert sorted_rec["kind"] == "sorted"
    assert sorted_rec["rules_put_it_in"] == "Documents\\PDFs"
    assert sorted_rec["would_suggest"][0]["folder"] == "Banking"

    rep = learning.report()
    assert rep["your_moves"] == 3
    assert rep["judged"] == 2 and rep["first_guess_right"] == 2
    assert rep["first_guess_rate"] == 1.0
    assert rep["folders"][0] == {"folder": "Banking", "examples": 3}
    assert rep["sites"] == ["yourbank.com"]


def test_only_the_site_name_is_stored(downloads, learning_on, sites):
    handler = watch.DownloadsHandler(downloads, learning_on)
    sites["s.pdf"] = "yourbank.com"
    src = _old_file(downloads / "Documents" / "PDFs" / "s.pdf")
    _user_move(handler, src, downloads / "Banking" / "s.pdf")
    stored = (Path(sorter.LOG_DIR) / learning.LEARNING_FILE_NAME).read_text(encoding="utf-8")
    assert "yourbank.com" in stored
    assert "https://" not in stored and "/page" not in stored


def test_forget_deletes_everything(downloads, learning_on):
    handler = watch.DownloadsHandler(downloads, learning_on)
    src = _old_file(downloads / "Documents" / "PDFs" / "s.pdf")
    _user_move(handler, src, downloads / "Banking" / "s.pdf")
    assert learning.read_log()
    learning.forget()
    assert learning.read_log() == []
    assert learning.load_model().folders == {}


def test_practice_log_is_kept_short(monkeypatch):
    monkeypatch.setattr(learning, "MAX_LOG_LINES", 10)
    monkeypatch.setattr(learning, "KEEP_LOG_LINES", 5)
    for i in range(12):
        learning._append_log({"kind": "sorted", "n": i})
    # The 11th line goes past the limit, so it's cut to the newest 5; the 12th is added after.
    assert [r["n"] for r in learning.read_log()] == [6, 7, 8, 9, 10, 11]


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------

def test_settings_switch_and_report(qapp, monkeypatch, downloads, sites):
    import onboarding

    saved: list[dict] = []
    monkeypatch.setattr(onboarding.suite_data, "save_sorter_config", lambda cfg, *a, **k: saved.append(json.loads(json.dumps(cfg))))
    widget = onboarding.SettingsWidget(config=sorter.get_default_config())
    try:
        assert widget._chk_learning.isChecked() is False
        assert "Nothing yet" in widget.learning_report_text()

        widget._chk_learning.setChecked(True)
        assert saved[-1]["learning"]["enabled"] is True

        cfg = dict(saved[-1], downloads_dir=str(downloads))
        cfg["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0}
        handler = watch.DownloadsHandler(downloads, cfg)
        for i in range(2):
            src = _old_file(downloads / "Documents" / "PDFs" / f"Statement_{i}.pdf")
            _user_move(handler, src, downloads / "Banking" / f"Statement_{i}.pdf")
        text = widget.learning_report_text()
        assert "Files you've moved: 2" in text
        assert "Banking: 2 examples" in text
    finally:
        widget.close()
