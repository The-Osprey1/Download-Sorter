"""The learner files what no rule could place, and only that, and only when asked to.

Off by default. Once on ("Always" on a suggestion, or the switch in Settings), a download
that would have gone to the catch-all folder goes where the user moved 3 similar files.
A file a name rule, a folder of the user's or a file type places is never moved on a guess,
and taking a guessed file back stops that guess for good.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from watchdog.events import FileMovedEvent

import config_manager
import learning
import refile
import sort_undo
import sorter
import watch


@pytest.fixture(autouse=True)
def _samples_are_finished_downloading(finished_downloads):
    """These tests are about where a file goes, not about waiting for downloads to finish."""


@pytest.fixture
def downloads(tmp_path) -> Path:
    root = tmp_path / "Downloads"
    root.mkdir()
    return root.resolve()


@pytest.fixture
def config(downloads, tmp_path, monkeypatch) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0,
                    "auto_unzip": False, "auto_unpack_3d_zips": False}
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    cfg["quick_switcher_enabled"] = False
    cfg["hold"]["enabled"] = False
    cfg["my_folders"] = {"enabled": False}   # so a folder's own name doesn't collect files here
    cfg["learning"] = {"enabled": True, "suggest": True}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)
    return cfg


def _old_file(path: Path, text: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    past = time.time() - 120
    os.utime(path, (past, past))
    return path


def _teach(downloads: Path, config: dict, names: list[str], folder: str) -> None:
    """Move files into ``folder`` by hand, the way a person would in File Explorer."""
    handler = watch.DownloadsHandler(downloads, config)
    for name in names:
        src = _old_file(downloads / "Other" / name, name)
        dest = downloads / folder / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dest)
        handler.on_moved(FileMovedEvent(str(src), str(dest)))


# ".xyzdata" is no known file type, so nothing but the catch-all folder would take these.
LEDGERS = ["ledger january.xyzdata", "ledger february.xyzdata", "ledger march.xyzdata"]


@pytest.fixture
def taught(downloads, config) -> dict:
    """The user has moved three "ledger" files into Bookkeeping."""
    _teach(downloads, config, LEDGERS, "Bookkeeping")
    return config


def _on(config: dict) -> dict:
    config["learning"]["auto_file"] = True
    return config


# ---------------------------------------------------------------------------
# off unless asked for
# ---------------------------------------------------------------------------

def test_it_is_off_by_default(downloads, taught):
    assert "auto_file" not in sorter.get_default_config()["learning"]
    routing = sorter.evaluate_routing("ledger april.xyzdata", taught)
    assert routing["match_type"] == "fallback"
    assert routing["rule_trace"][-2] == {"tier": "learned", "status": "no_confident_guess"}


def test_switched_on_it_files_what_no_rule_could_place(downloads, taught):
    dest = sorter.move_file(_old_file(downloads / "ledger april.xyzdata"), _on(taught))

    assert dest == downloads / "Bookkeeping" / "ledger april.xyzdata"
    record = sort_undo.find_active_move(dest)
    assert record["why"] == "learned" and record["rule"] == "Learned (Bookkeeping)"
    assert config_manager.describe_why(record).startswith("No rule matched, so it was filed where you put")


def test_the_reason_names_the_word_that_decided_it(downloads, taught):
    routing = sorter.evaluate_routing("ledger april.xyzdata", _on(taught))
    assert routing["match_type"] == "learned"
    assert routing["matched_trigger"] == "your own moves (“ledger”)"
    assert routing["rule_trace"][-1]["because"] == "“ledger”"


def test_learning_switched_off_switches_this_off_too(downloads, taught):
    _on(taught)["learning"]["enabled"] = False
    assert sorter.evaluate_routing("ledger april.xyzdata", taught)["match_type"] == "fallback"


# ---------------------------------------------------------------------------
# only what nothing else could place
# ---------------------------------------------------------------------------

def test_a_file_type_is_never_overridden(downloads, config):
    _teach(downloads, config, ["ledger january.pdf", "ledger february.pdf", "ledger march.pdf"], "Bookkeeping")
    routing = sorter.evaluate_routing("ledger april.pdf", _on(config))
    assert routing["match_type"] == "extension"


def test_a_name_rule_wins(downloads, taught):
    _on(taught)["keyword_rules"].insert(0, {"name": "Ledgers", "folder": "Accounts", "match": ["ledger"],
                                            "extensions": [], "enabled": True})
    routing = sorter.evaluate_routing("ledger april.xyzdata", taught)
    assert (routing["match_type"], routing["destination"]) == ("keyword", "Accounts")


def test_an_unrelated_file_still_goes_to_the_catch_all(downloads, taught):
    # Only one folder is known, so the model is "100% sure" of it for any file at all.
    # The word test is what stops that from meaning anything.
    assert sorter.evaluate_routing("holiday plans.xyzdata", _on(taught))["match_type"] == "fallback"


def test_two_moves_are_not_enough(downloads, config):
    _teach(downloads, config, LEDGERS[:2], "Bookkeeping")
    assert sorter.evaluate_routing("ledger april.xyzdata", _on(config))["match_type"] == "fallback"


def test_a_word_that_goes_to_two_folders_decides_nothing(downloads, config):
    _teach(downloads, config, LEDGERS, "Bookkeeping")
    _teach(downloads, config, ["ledger old 1.xyzdata", "ledger old 2.xyzdata", "ledger old 3.xyzdata"], "Archive Box")
    assert sorter.evaluate_routing("ledger april.xyzdata", _on(config))["match_type"] == "fallback"


def test_the_folder_must_still_exist(downloads, taught):
    for item in (downloads / "Bookkeeping").iterdir():
        item.unlink()
    (downloads / "Bookkeeping").rmdir()
    assert sorter.evaluate_routing("ledger april.xyzdata", _on(taught))["match_type"] == "fallback"


def test_files_already_in_the_catch_all_folder_stay_there(downloads, taught):
    waiting = _old_file(downloads / "Other" / "ledger last year.xyzdata")
    assert sorter.reclassify_misplaced_file(waiting, _on(taught)) is None
    assert waiting.exists()


# ---------------------------------------------------------------------------
# taking one back
# ---------------------------------------------------------------------------

def test_putting_one_back_stops_that_guess(downloads, taught):
    dest = sorter.move_file(_old_file(downloads / "ledger april.xyzdata"), _on(taught))
    record = sort_undo.find_active_move(dest)

    assert refile.put_back(record, downloads).ok

    learning._MODEL_CACHE = None
    assert sorter.evaluate_routing("ledger may.xyzdata", taught)["match_type"] == "fallback"
    assert learning.load_model().folders["bookkeeping"]["examples"] == 2


def test_a_run_of_wrong_sure_guesses_is_a_brake():
    right = {"kind": "your_move", "counted": True, "first_guess_right": True, "guesses": [{"confidence": 0.95}]}
    wrong = dict(right, first_guess_right=False)
    unsure = dict(wrong, guesses=[{"confidence": 0.4}])
    assert learning.sure_guesses_were_right([]) is True
    assert learning.sure_guesses_were_right([right] * 4 + [wrong]) is True       # 4 in 5
    assert learning.sure_guesses_were_right([right] * 3 + [wrong] * 2) is False
    assert learning.sure_guesses_were_right([unsure] * 9 + [right]) is True      # unsure guesses don't count
    assert learning.sure_guesses_were_right([wrong] * 5 + [right] * 20) is True  # only the last 20


# ---------------------------------------------------------------------------
# "Always", the report and the replay
# ---------------------------------------------------------------------------

def test_an_unplaced_file_is_asked_about_and_always_switches_it_on(downloads, taught):
    dest = sorter.move_file(_old_file(downloads / "ledger april.xyzdata"), taught)
    assert dest.parent == downloads / "Other"
    # The third move by hand already asked once; that ribbon went unanswered, over a day ago.
    model = learning.load_model()
    model.extra["pending"] = {}
    learning.save_model(model)

    suggestion = learning.fallback_suggestion(dest, downloads, taught)
    assert suggestion["unplaced"] is True and suggestion["folder"] == "Bookkeeping"

    learning.accept_suggestion(suggestion["id"])
    learning.turn_on_auto_file()
    saved = config_manager.load_sorter_config()
    assert saved["learning"]["auto_file"] is True and learning.auto_file_enabled(saved)
    assert saved["keyword_rules"][0]["name"] == "Bookkeeping (learned)"


def test_the_report_counts_what_it_filed_and_what_was_taken_back(downloads, taught):
    dest = sorter.move_file(_old_file(downloads / "ledger april.xyzdata"), _on(taught))
    watch._handle_post_move(dest, downloads, taught)
    assert learning.report()["filed_for_you"] == 1

    refile.put_back(sort_undo.find_active_move(dest), downloads)
    rep = learning.report()
    assert (rep["filed_for_you"], rep["taken_back"]) == (1, 1)


def test_replay_scores_past_moves_without_touching_the_model(downloads, config):
    _teach(downloads, config, LEDGERS + ["ledger april.xyzdata", "ledger may.xyzdata"], "Bookkeeping")
    before = (Path(sorter.LOG_DIR) / learning.LEARNING_FILE_NAME).read_bytes()

    result = learning.replay()

    # The first three teach it; the fourth and fifth it would have filed itself, correctly.
    assert result == {"moves": 5, "would_file": 2, "right": 2, "wrong": []}
    assert (Path(sorter.LOG_DIR) / learning.LEARNING_FILE_NAME).read_bytes() == before


def test_settings_has_the_switch(qapp, monkeypatch):
    import onboarding

    saved: list[dict] = []
    monkeypatch.setattr(onboarding.suite_data, "save_sorter_config",
                        lambda cfg, *a, **k: saved.append(json.loads(json.dumps(cfg))))
    widget = onboarding.SettingsWidget(config=sorter.get_default_config())
    try:
        assert widget._chk_auto_file.isChecked() is False, "off until you ask for it"
        widget._chk_auto_file.setChecked(True)
        assert saved[-1]["learning"]["auto_file"] is True
    finally:
        widget.close()
        widget.deleteLater()
