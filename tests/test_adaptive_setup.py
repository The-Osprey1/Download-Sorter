"""Setup from your own folders, learning on by default, and rules it asks about.

The welcome steps used to ask which of 4 profiles you are. Now they look at the
folders already in Downloads and offer to keep filing into them; when Downloads
has none, they offer simple category boxes. Learning is on by default, and after
3 similar moves it asks (with a ribbon) before adding a rule. It never moves a
file on its own guess.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from watchdog.events import FileMovedEvent

import config_manager
import folder_scan
import learning
import sorter
import watch


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
def sites(monkeypatch) -> dict[str, str]:
    known: dict[str, str] = {}

    def fake_zone(path: Path) -> str:
        site = known.get(Path(path).name)
        return f"[ZoneTransfer]\nZoneId=3\nReferrerUrl=https://www.{site}/page\n" if site else ""

    monkeypatch.setattr(learning, "_read_zone_identifier", fake_zone)
    return known


def _touch(path: Path, text: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    past = time.time() - 60
    os.utime(path, (past, past))
    return path


def _sample_downloads(root: Path) -> None:
    for i in range(4):
        _touch(root / "Invoices" / f"Invoice_{i}.pdf")
        _touch(root / "Banking" / f"Chase Statement {i}.pdf")
        _touch(root / "IME 4030" / f"IME4030_lecture_{i}.pdf")
    _touch(root / "Taxes 2025" / "1099-INT.pdf")
    _touch(root / "Music" / "song.mp3")
    _touch(root / "New folder" / "x.txt")
    _touch(root / "_Duplicates" / "copy.pdf")
    _touch(root / "Repo" / ".git" / "HEAD")
    _touch(root / "Repo" / "main.py")
    _touch(root / "Kept" / ".download-sorter-keep")
    _touch(root / "Kept" / "notes.txt")
    _touch(root / "Documents" / "Personal_Statement_UCLA.pdf")
    _touch(root / "Documents" / "statement_of_purpose.pdf")


def _folders(scan: dict) -> dict[str, dict]:
    return {f["name"]: f for f in scan["folders"]}


# ---------------------------------------------------------------------------
# Step 1: the scan
# ---------------------------------------------------------------------------

def test_scan_suggests_a_rule_for_each_folder_you_use(downloads, config):
    _sample_downloads(downloads)

    found = _folders(folder_scan.scan_downloads(config))

    assert set(found) == {"Invoices", "Banking", "IME 4030", "Taxes 2025", "Music"}, \
        "app folders, dumping grounds, code projects and kept folders are left out"
    assert found["Invoices"]["rule"]["match"] == ["invoice", "invoices"]
    assert found["Invoices"]["rule"]["extensions"] == [".pdf"]
    assert found["IME 4030"]["rule"]["match"][0] == "ime 4030", "a course code keeps its number"
    assert found["Taxes 2025"]["rule"]["match"] == ["tax", "taxes"], "a year is not a course code"
    assert found["Music"]["kind"] == "category" and found["Music"]["category"] == "Audio"
    assert all(f["checked"] for f in found.values())


def test_a_word_common_elsewhere_in_downloads_is_not_a_folder_word(downloads, config):
    _sample_downloads(downloads)

    banking = _folders(folder_scan.scan_downloads(config))["Banking"]["rule"]

    assert "statement" not in banking["match"], "personal statements in Documents also use it"
    assert "chase" in banking["match"]


def test_a_second_folder_for_a_category_gets_no_rule(downloads, config):
    _touch(downloads / "Images" / "a.jpg")
    _touch(downloads / "Pictures" / "b.jpg")

    pictures = _folders(folder_scan.scan_downloads(config))["Pictures"]

    assert "rule" not in pictures and pictures["kind"] == "custom"
    assert pictures["checked"] is False


def test_empty_downloads_is_reported_as_empty(downloads, config):
    _touch(downloads / "loose.pdf")
    scan = folder_scan.scan_downloads(config)
    assert scan["empty"] and scan["folders"] == []


def test_applying_the_ticked_folders(downloads, config):
    _sample_downloads(downloads)
    scan = folder_scan.scan_downloads(config)
    config["keyword_rules"] = config_manager.apply_persona_profile({}, "office_admin")["keyword_rules"]
    config["keyword_rules"].append({"name": "Band", "folder": "Music\\Band", "match": ["setlist"]})

    new = folder_scan.apply_scan(config, scan, ["Invoices", "Banking", "IME 4030", "Music"],
                                 categories_off=["Installers"])

    names = [r["name"] for r in new["keyword_rules"]]
    assert {"Invoices", "Banking", "IME 4030", "Band"} <= set(names)
    assert "Taxes 2025" not in names, "unticked"
    assert "Resumes & Career" not in names, "untouched starter rules from a profile are dropped"
    audio = next(r for r in new["extension_rules"] if r["name"] == "Audio")
    assert audio["folder"] == "Music"
    installers = next(r for r in new["extension_rules"] if r["name"] == "Installers")
    assert installers["enabled"] is False
    assert new["user_persona"] == "custom"

    route = lambda name: sorter.evaluate_routing(name, new)["destination"]
    assert route("Invoice_99.pdf") == "Invoices"
    assert route("Invoices March.pdf") == "Invoices"
    assert route("IME4030_homework.pdf") == "IME 4030"
    assert route("song2.mp3") == "Music"
    assert route("Personal_Statement.pdf") == "Documents"
    assert route("setup.exe") == "Other"
    assert config["keyword_rules"][-1]["name"] == "Band", "the original config is not changed"


def test_starter_folders_when_downloads_is_empty(config):
    new = folder_scan.apply_scan(config, {"folders": []}, starters=["receipts", "school"])
    names = {r["name"] for r in new["keyword_rules"]}
    assert {"Invoices & Receipts", "School"} <= names
    assert sorter.evaluate_routing("ME 3210 Syllabus.pdf", new)["destination"] == "Documents\\School"


def test_maker_files_turn_on_maker_features(downloads, config):
    for i in range(3):
        _touch(downloads / "Prints" / f"part{i}.stl")
    new = folder_scan.apply_scan(config, folder_scan.scan_downloads(config))
    assert new["show_maker_features"] is True


def test_the_learner_is_seeded_once(downloads, config):
    _sample_downloads(downloads)
    scan = folder_scan.scan_downloads(config)

    first = folder_scan.seed_learning(scan, config)
    again = folder_scan.seed_learning(scan, config)

    assert first > 0 and again == 0
    guess = learning.load_model().predict(learning._name_features("Invoice_77.pdf"))
    assert guess[0]["folder"] == "Invoices"


# ---------------------------------------------------------------------------
# Step 2: learning on by default
# ---------------------------------------------------------------------------

def test_learning_is_on_for_new_installs():
    assert learning.is_enabled(sorter.get_default_config())
    assert learning.suggestions_enabled(sorter.get_default_config())


@pytest.mark.parametrize("saved, expected", [
    ({"enabled": False}, True),                       # the old off-by-default preview
    ({"enabled": False, "suggest": True}, False),     # switched off after this version
    ({"enabled": True, "suggest": False}, True),
])
def test_older_settings_get_learning_switched_on_once(saved, expected):
    config = sorter.apply_v2_defaults({"keyword_rules": [], "extension_rules": [], "learning": dict(saved)})
    assert learning.is_enabled(config) is expected


# ---------------------------------------------------------------------------
# Words and websites in rules
# ---------------------------------------------------------------------------

def test_a_rule_can_require_the_website(downloads, config, sites):
    config["keyword_rules"] = [{"name": "Bank", "folder": "Banking", "match": ["statement"],
                                "sites": ["yourbank.com"]}]
    from_bank = _touch(downloads / "Statement_March.pdf")
    personal = _touch(downloads / "Personal_Statement.pdf")
    sites[from_bank.name] = "yourbank.com"

    assert sorter.evaluate_routing(from_bank.name, config, file_path=from_bank)["destination"] == "Banking"
    assert sorter.evaluate_routing(personal.name, config, file_path=personal)["destination"] == "Documents"


def test_a_website_only_rule(downloads, config, sites):
    config["keyword_rules"] = [{"name": "Canvas", "folder": "School", "match": [], "sites": ["instructure.com"]}]
    handout = _touch(downloads / "week3.pdf")
    sites[handout.name] = "school.instructure.com"

    result = sorter.evaluate_routing(handout.name, config, file_path=handout)

    assert result["destination"] == "School"
    assert "school.instructure.com" in result["matched_trigger"]


# ---------------------------------------------------------------------------
# Step 3: suggest a rule after 3 similar moves
# ---------------------------------------------------------------------------

def _move(handler, downloads: Path, name: str, folder: str) -> dict | None:
    src = _touch(downloads / "Documents" / name)
    dest = downloads / folder / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dest)
    seen: dict = {}
    original = learning.observe_user_move

    def spy(*args, **kwargs):
        seen["record"] = original(*args, **kwargs)
        return seen["record"]

    learning.observe_user_move = spy
    try:
        handler.on_moved(FileMovedEvent(str(src), str(dest)))
    finally:
        learning.observe_user_move = original
    return (seen.get("record") or {}).get("suggestion")


def test_three_similar_moves_lead_to_one_question(downloads, config, sites):
    handler = watch.DownloadsHandler(downloads, config)
    asked = []
    for i in range(3):
        name = f"Statement_{i}.pdf"
        sites[name] = "yourbank.com"
        asked.append(_move(handler, downloads, name, "Banking"))

    assert asked[0] is None and asked[1] is None
    suggestion = asked[2]
    assert suggestion["folder"] == "Banking"
    assert suggestion["word"] == "statement" and suggestion["site"] == "yourbank.com"
    assert learning.describe_suggestion(suggestion) == (
        "Put files like this in Banking?", "“statement” files from yourbank.com")
    assert (downloads / "Banking" / "Statement_2.pdf").exists(), "nothing was moved for the user"


def test_yes_adds_a_rule_that_wins(downloads, config, sites, tmp_path):
    handler = watch.DownloadsHandler(downloads, config)
    for i in range(3):
        name = f"Invoice-{i}.pdf"
        suggestion = _move(handler, downloads, name, "Receipts")
    config_path = tmp_path / "config.json"
    config_manager.save_sorter_config(config, config_path)

    rule = learning.accept_suggestion(suggestion["id"], config_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["keyword_rules"][0] == rule
    assert rule["name"] == "Receipts (learned)" and rule["origin"] == "learned"
    assert rule["match"] == ["invoice"] and rule["extensions"] == [".pdf"]
    assert sorter.evaluate_routing("Invoice-77.pdf", saved)["destination"] == "Receipts"
    assert learning.accept_suggestion(suggestion["id"], config_path) is None, "answered only once"
    assert learning.report()["rules_added"] == 1


def test_no_means_it_never_asks_about_that_again(downloads, config):
    handler = watch.DownloadsHandler(downloads, config)
    for i in range(3):
        suggestion = _move(handler, downloads, f"Receipt {i}.pdf", "Receipts")
    assert learning.decline_suggestion(suggestion["id"])

    later = [_move(handler, downloads, f"Receipt {i}.pdf", "Receipts") for i in range(3, 7)]

    assert later == [None, None, None, None]


def test_a_word_that_points_to_several_folders_is_not_suggested(downloads, config):
    handler = watch.DownloadsHandler(downloads, config)
    for i in range(3):
        _move(handler, downloads, f"report {i} school.pdf", "School")
    asked = [_move(handler, downloads, f"report {i} work.pdf", "Work") for i in range(3)]
    assert all(s is None or s["word"] != "report" for s in asked)


def test_nothing_is_asked_when_a_rule_already_files_it_there(downloads, config):
    config["keyword_rules"] = [{"name": "Receipts", "folder": "Receipts", "match": ["receipt"]}]
    handler = watch.DownloadsHandler(downloads, config)
    asked = [_move(handler, downloads, f"receipt {i}.pdf", "Receipts") for i in range(3)]
    assert asked == [None, None, None]


def test_suggestions_can_be_switched_off(downloads, config):
    config["learning"]["suggest"] = False
    handler = watch.DownloadsHandler(downloads, config)
    asked = [_move(handler, downloads, f"Receipt {i}.pdf", "Receipts") for i in range(3)]
    assert asked == [None, None, None]


def test_the_watcher_shows_the_question_and_a_broken_ribbon_does_not_undo_the_move(
        downloads, config, monkeypatch):
    ribbon_popup = pytest.importorskip("ribbon_popup")
    config["ribbon_enabled"] = True
    config["notifications"] = True
    shown = []
    monkeypatch.setattr(ribbon_popup, "notify_suggestion", shown.append)
    handler = watch.DownloadsHandler(downloads, config)
    for i in range(3):
        _move(handler, downloads, f"Receipt {i}.pdf", "Receipts")
    assert len(shown) == 1

    def broken(_id):
        raise RuntimeError("no display")

    monkeypatch.setattr(ribbon_popup, "notify_suggestion", broken)
    for i in range(3, 6):
        _move(handler, downloads, f"Payslip {i}.pdf", "Pay")
    assert (downloads / "Pay" / "Payslip 5.pdf").exists()
    assert not (downloads / "Documents" / "Payslip 5.pdf").exists()
