"""The learner hears "no": taking back a file that a learned rule (or the learner's own
guess) filed counts against it, and a learned rule that keeps being wrong is offered up
to be switched off. Rules the user wrote, and file types, are never judged this way.
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
    """These tests are about corrections, not about waiting for downloads to finish."""


@pytest.fixture
def downloads(tmp_path) -> Path:
    root = tmp_path / "Downloads"
    root.mkdir()
    return root.resolve()


def _rule(name: str, origin: str) -> dict:
    return {"name": name, "folder": "Banking", "match": ["statement"], "extensions": [],
            "enabled": True, "origin": origin}


@pytest.fixture
def config(downloads, tmp_path, monkeypatch) -> dict:
    """Settings saved to a file of their own, with one learned rule: "statement" goes to Banking."""
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0,
                    "auto_unzip": False, "auto_unpack_3d_zips": False}
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    cfg["quick_switcher_enabled"] = False
    cfg["hold"]["enabled"] = False
    cfg["learning"] = {"enabled": True, "suggest": True}
    cfg["keyword_rules"] = [_rule("Banking (learned)", "learned")] + list(cfg.get("keyword_rules", []))
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


def _file_it(downloads: Path, config: dict, name: str) -> dict:
    """Have the sorter file ``name``; returns that move's history record."""
    dest = sorter.move_file(_old_file(downloads / name, name), config)
    assert dest is not None
    record = sort_undo.find_active_move(dest)
    assert record is not None
    return record


def _saved_rule(name: str = "Banking (learned)") -> dict:
    saved = config_manager.load_sorter_config()
    return next(r for r in saved["keyword_rules"] if r.get("name") == name)


# ---------------------------------------------------------------------------
# a learned rule that keeps being wrong
# ---------------------------------------------------------------------------

def test_the_history_says_a_learned_rule_filed_it(downloads, config):
    record = _file_it(downloads, config, "Personal Statement.pdf")
    assert record["why"] == "keyword" and record["rule"] == "Banking (learned)"
    assert Path(record["dest"]).parent == downloads / "Banking"


def test_two_files_taken_back_offer_to_switch_the_rule_off(downloads, config):
    first = _file_it(downloads, config, "Personal Statement.pdf")
    assert refile.put_back(first, downloads).ok
    model = learning.load_model()
    assert model.misses(learning.rule_miss_key("Banking (learned)")) == 1
    assert not model.extra.get("pending")          # once could be a slip

    second = _file_it(downloads, config, "Statement of Purpose.pdf")
    assert refile.put_back(second, downloads).ok
    pending = learning.load_model().extra["pending"]
    (suggestion,) = pending.values()
    assert suggestion["kind"] == "disable_rule" and suggestion["rule"] == "Banking (learned)"
    assert learning.describe_suggestion(suggestion) == (
        "Stop filing into Banking?", "You took back 2 files this learned rule filed")

    switched = learning.accept_suggestion(suggestion["id"])
    assert switched["switched_off"] is True
    assert _saved_rule()["enabled"] is False        # still listed, just off
    assert [r["kind"] for r in learning.read_log()] == ["taken_back", "taken_back", "rule_switched_off"]


def test_saying_no_means_it_is_not_asked_again(downloads, config):
    for name in ("Personal Statement.pdf", "Statement of Purpose.pdf"):
        refile.put_back(_file_it(downloads, config, name), downloads)
    (suggestion_id,) = learning.load_model().extra["pending"]
    assert learning.decline_suggestion(suggestion_id)

    refile.put_back(_file_it(downloads, config, "Artist Statement.pdf"), downloads)
    assert not learning.load_model().extra.get("pending")
    assert _saved_rule()["enabled"] is True


def test_a_move_is_only_counted_once(downloads, config):
    record = _file_it(downloads, config, "Personal Statement.pdf")
    assert learning.observe_correction(record, config, downloads) is not None
    assert learning.observe_correction(record, config, downloads) is None
    assert learning.load_model().misses(learning.rule_miss_key("Banking (learned)")) == 1


def test_moving_it_elsewhere_from_home_counts_too(downloads, config):
    record = _file_it(downloads, config, "Personal Statement.pdf")
    outcome = refile.move_to_folder(record, downloads / "School", config, downloads)
    assert outcome.ok
    assert learning.load_model().misses(learning.rule_miss_key("Banking (learned)")) == 1
    # ...and the folder you chose is learned as usual.
    assert learning.load_model().folders["school"]["examples"] == 1


def test_dragging_it_out_in_explorer_counts_too(downloads, config):
    record = _file_it(downloads, config, "Personal Statement.pdf")
    src = Path(record["dest"])
    dest = downloads / "School" / src.name
    dest.parent.mkdir()
    os.replace(src, dest)
    handler = watch.DownloadsHandler(downloads, config)
    handler.on_moved(FileMovedEvent(str(src), str(dest)))

    assert learning.load_model().misses(learning.rule_miss_key("Banking (learned)")) == 1
    assert dest.exists()                            # your move is never undone


# ---------------------------------------------------------------------------
# what is never judged
# ---------------------------------------------------------------------------

def test_a_rule_you_wrote_is_never_judged(downloads, config):
    config["keyword_rules"][0] = _rule("Banking", "user")
    for name in ("Personal Statement.pdf", "Statement of Purpose.pdf"):
        record = _file_it(downloads, config, name)
        assert record["rule"] == "Banking"
        assert learning.observe_correction(record, config, downloads) is None
    assert not (Path(sorter.LOG_DIR) / learning.LEARNING_FILE_NAME).exists()


def test_a_file_filed_by_its_type_is_never_judged(downloads, config):
    record = _file_it(downloads, config, "holiday.jpg")
    assert record["why"] == "extension"
    assert refile.put_back(record, downloads).ok
    assert not (Path(sorter.LOG_DIR) / learning.LEARNING_FILE_NAME).exists()


def test_learning_switched_off_notes_nothing(downloads, config):
    record = _file_it(downloads, config, "Personal Statement.pdf")
    config["learning"] = {"enabled": False}
    assert learning.observe_correction(record, config, downloads) is None


# ---------------------------------------------------------------------------
# a file the learner filed on its own guess
# ---------------------------------------------------------------------------

def test_taking_back_a_guess_unlearns_it_and_blocks_those_words(downloads, config):
    model = learning.Model()
    for _ in range(3):
        model.learn(["w:chase", "w:statement", "x:.pdf", "s:yourbank.com"], "Banking")
    learning.save_model(model)
    record = {"id": "abc", "why": "learned", "rule": "Learned (Banking)", "site": "yourbank.com",
              "src": str(downloads / "Chase Statement.pdf"),
              "dest": str(downloads / "Banking" / "Chase Statement.pdf")}

    noted = learning.observe_correction(record, config, downloads)

    assert noted["kind"] == "taken_back" and "suggestion" not in noted
    model = learning.load_model()
    banking = model.folders["banking"]
    assert banking["examples"] == 2 and banking["features"]["w:chase"] == 2
    feats = ["w:chase", "w:statement", "x:.pdf", "s:yourbank.com"]
    assert learning.blocked_features(model, feats, "Banking") == ["w:chase", "w:statement", "s:yourbank.com"]
    assert learning.blocked_features(model, feats, "Taxes") == []
    assert learning.blocked_features(model, ["w:invoice", "x:.pdf"], "Banking") == []


def test_unlearn_never_goes_below_nothing():
    model = learning.Model()
    model.learn(["w:a"], "Banking")
    model.unlearn(["w:a", "w:b"], "Banking")
    model.unlearn(["w:a"], "Banking")
    model.unlearn(["w:a"], "Never Seen")
    assert model.folders["banking"] == {"label": "Banking", "examples": 0, "features": {}}
