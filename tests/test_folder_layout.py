"""The two-tier folder layout and the folders you make yourself.

1. Inside a main folder each kind of file gets its own folder (Documents\\PDF).
2. A folder you make, in Downloads or inside a main folder, collects files named
   after it, so every version of a project lands in one place.
3. Nothing is ever taken out of a folder you made.
4. Files already sitting in a main folder are moved into their type folder by
   the next sweep, as one batch that Undo puts back.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

import sort_undo
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


def _file(path: Path, text: str = "data", age: float = 3600.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    old = time.time() - age
    os.utime(path, (old, old))
    return path


def _goes_to(name: str, config: dict) -> str:
    return sorter.evaluate_routing(name, config, active_project={"enabled": False})["destination"]


# ---------------------------------------------------------------------------
# 1. two tiers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, folder", [
    ("report.pdf", "Documents\\PDF"),
    ("letter.docx", "Documents\\Word"),
    ("budget.xlsx", "Documents\\Spreadsheets"),
    ("benchy.stl", "3D Models\\STL"),
    ("plate.3mf", "3D Models\\3MF"),
    ("bracket.f3d", "CAD\\Fusion 360"),
    ("bracket.step", "CAD\\STEP"),
    ("photo.jpg", "Images\\JPG"),
    ("song.mp3", "Audio\\MP3"),
    ("clip.mp4", "Videos\\MP4"),
    # small or rare kinds stay in the main folder
    ("setup.exe", "Installers"),
    ("bundle.zip", "Archives"),
    ("scan.heic", "Images"),
    ("mystery.xyz", "Other"),
])
def test_each_kind_of_file_has_its_own_folder(config, name, folder):
    assert _goes_to(name, config) == folder


def test_settings_saved_before_the_option_existed_get_the_new_layout(config):
    del config["type_folders"]
    assert _goes_to("report.pdf", config) == "Documents\\PDF"


def test_type_folders_can_be_switched_off(config):
    config["type_folders"]["enabled"] = False
    assert _goes_to("report.pdf", config) == "Documents"
    assert _goes_to("benchy.stl", config) == "3D Models"


def test_a_type_folder_can_be_renamed_or_left_out(config):
    config["type_folders"]["names"] = {".pdf": "PDFs", ".stl": ""}
    assert _goes_to("report.pdf", config) == "Documents\\PDFs"
    assert _goes_to("benchy.stl", config) == "3D Models"


def test_a_rule_that_already_points_deeper_is_used_as_written(config):
    for rule in config["extension_rules"]:
        if rule["name"] == "Documents":
            rule["folder"] = "Paperwork\\Scans"
    assert _goes_to("report.pdf", config) == "Paperwork\\Scans"
    assert sorter.with_type_folder("D:\\Library", ".pdf", config) == "D:\\Library"


def test_main_folders_are_made_up_front_but_type_folders_only_when_needed(config, downloads):
    sorter.ensure_tree(config)
    assert (downloads / "Documents").is_dir()
    assert not (downloads / "Documents" / "PDF").exists()
    sorter.move_file(_file(downloads / "report.pdf"), config, check_age=False)
    assert (downloads / "Documents" / "PDF" / "report.pdf").exists()


# ---------------------------------------------------------------------------
# 2. folders you make collect files named after them
# ---------------------------------------------------------------------------

def test_a_project_folder_in_3d_models_takes_every_version(config, downloads):
    (downloads / "3D Models" / "Flanged Mace").mkdir(parents=True)

    for name in ("flanged_mace_v1.f3d", "Flanged Mace v2.step", "FlangedMace-v3.stl", "flanged-mace.3mf"):
        assert _goes_to(name, config) == "3D Models\\Flanged Mace", name
    # other files, and other kinds of file, are sorted as usual
    assert _goes_to("mace.stl", config) == "3D Models\\STL"
    assert _goes_to("flanged mace invoice.pdf", config) == "Documents\\PDF"


def test_a_project_folder_at_the_top_of_downloads_takes_any_kind(config, downloads):
    (downloads / "Flanged Mace").mkdir()
    assert _goes_to("flanged_mace_v4.f3d", config) == "Flanged Mace"
    assert _goes_to("Flanged Mace quote.pdf", config) == "Flanged Mace"
    assert _goes_to("unrelated.pdf", config) == "Documents\\PDF"


def test_versions_land_in_the_folder_and_none_is_overwritten(config, downloads):
    project = downloads / "3D Models" / "Flanged Mace"
    project.mkdir(parents=True)
    _file(project / "flanged_mace.f3d", "version 1")

    dest = sorter.move_file(_file(downloads / "flanged_mace.f3d", "version 2"), config, check_age=False)

    assert dest == project / "flanged_mace (2).f3d"
    assert (project / "flanged_mace.f3d").read_text(encoding="utf-8") == "version 1"
    assert dest.read_text(encoding="utf-8") == "version 2"


def test_the_longest_folder_name_wins(config, downloads):
    (downloads / "3D Models" / "Mace").mkdir(parents=True)
    (downloads / "3D Models" / "Flanged Mace").mkdir(parents=True)
    assert _goes_to("flanged_mace_v2.stl", config) == "3D Models\\Flanged Mace"
    assert _goes_to("spiked_mace.stl", config) == "3D Models\\Mace"


@pytest.mark.parametrize("folder", ["New folder", "Old", "Parts", "PDFs", "2024", "2024-11", "v2", "_Private", ".hidden"])
def test_folders_with_general_names_never_collect_files(config, downloads, folder):
    (downloads / "3D Models" / folder).mkdir(parents=True)
    (downloads / folder).mkdir(exist_ok=True)
    assert sorter.my_folders(config) == []


def test_a_name_rule_wins_over_a_folder_name(config, downloads):
    (downloads / "Flanged Mace").mkdir()
    config["keyword_rules"] = [{"name": "Invoices", "folder": "Invoices", "match": ["invoice"], "enabled": True}]
    assert _goes_to("flanged mace invoice.pdf", config) == "Invoices"


def test_a_folder_can_be_told_not_to_collect(config, downloads):
    (downloads / "3D Models" / "Flanged Mace").mkdir(parents=True)
    (downloads / "3D Models" / "Dragon Bust").mkdir(parents=True)
    (downloads / "3D Models" / "Dragon Bust" / ".download-sorter-keep").write_text("", encoding="utf-8")
    config["my_folders"]["skip"] = ["3D Models\\Flanged Mace"]

    assert _goes_to("flanged_mace.stl", config) == "3D Models\\STL"
    assert _goes_to("dragon_bust.stl", config) == "3D Models\\STL"

    config["my_folders"] = {"enabled": False, "skip": []}
    (downloads / "Gearbox").mkdir()
    assert _goes_to("gearbox.stl", config) == "3D Models\\STL"


def test_the_sorters_own_folders_are_not_mistaken_for_yours(config, downloads):
    config["makerworld_destination"] = "3D Models"
    config["keyword_rules"] = [{"name": "Receipts", "folder": "Documents\\Receipts", "match": ["receipt"], "enabled": True}]
    for made_by_the_sorter in ("3D Models\\STL", "Documents\\PDF", "Documents\\Receipts", "Extracted", "_Duplicates"):
        sorter.folder_path(downloads, made_by_the_sorter).mkdir(parents=True, exist_ok=True)
    # A MakerWorld designer folder holds one folder per model and no files of its own.
    (downloads / "3D Models" / "Some Designer" / "Cable Clip").mkdir(parents=True)

    assert sorter.my_folders(config) == []
    assert _goes_to("some designer profile.stl", config) == "3D Models\\STL"


# ---------------------------------------------------------------------------
# 3. nothing is taken out of a folder you made
# ---------------------------------------------------------------------------

def test_files_in_your_folders_are_never_moved(config, downloads):
    kit = downloads / "Lamp Kit"
    files = [_file(kit / "manual.pdf"), _file(kit / "photo.jpg"), _file(kit / "part.stl"),
             _file(downloads / "3D Models" / "Flanged Mace" / "notes.pdf")]
    handler = watch.DownloadsHandler(downloads, config)

    for path in files:
        handler.on_created(type("E", (), {"is_directory": False, "src_path": str(path)})())
        assert sorter.reclassify_misplaced_file(path, config) is None
    assert not watch.PENDING_MISPLACED and not watch.PENDING_DOWNLOADS
    assert sorter.sort_existing_misplaced(config, batch_id="t") == 0
    assert all(path.exists() for path in files)


def test_a_file_saved_into_the_wrong_main_folder_is_still_corrected(config, downloads):
    stray = _file(downloads / "Images" / "report.pdf")
    handler = watch.DownloadsHandler(downloads, config)
    handler.on_created(type("E", (), {"is_directory": False, "src_path": str(stray)})())
    assert stray in watch.PENDING_MISPLACED

    assert sorter.reclassify_misplaced_file(stray, config) == downloads / "Documents" / "PDF" / "report.pdf"


# ---------------------------------------------------------------------------
# 4. moving to the new layout
# ---------------------------------------------------------------------------

def test_files_in_a_main_folder_move_into_their_type_folder_and_undo_puts_them_back(config, downloads):
    report = _file(downloads / "Documents" / "report.pdf")
    model = _file(downloads / "3D Models" / "benchy.stl")
    filed = _file(downloads / "Documents" / "Word" / "letter.docx")

    assert sorter.sort_existing_misplaced(config, batch_id="tidy") == 2

    assert (downloads / "Documents" / "PDF" / "report.pdf").exists()
    assert (downloads / "3D Models" / "STL" / "benchy.stl").exists()
    assert filed.exists()
    assert sorter.sort_existing_misplaced(config, batch_id="again") == 0, "a second sweep has nothing to do"

    assert sort_undo.main(["--batch", "tidy"]) == 0
    assert report.exists() and model.exists()
    assert sorter.sort_existing_misplaced(config, batch_id="after-undo") == 0, "files Undo put back stay put"


def test_files_from_before_the_users_cutoff_are_left_alone(config, downloads):
    report = _file(downloads / "Documents" / "report.pdf")
    # Windows also records when a file was created, which a test cannot backdate,
    # so put a real gap between making the file and the cutoff.
    time.sleep(0.3)
    assert sorter.sort_existing_misplaced(config, batch_id="t", skip_before=time.time()) == 0
    assert report.exists()


def test_existing_files_named_after_your_folder_are_gathered_into_it(config, downloads):
    project = downloads / "3D Models" / "Flanged Mace"
    project.mkdir(parents=True)
    _file(downloads / "3D Models" / "flanged_mace_v1.stl")
    _file(downloads / "3D Models" / "STL" / "flanged_mace_v2.stl")
    other = _file(downloads / "3D Models" / "STL" / "benchy.stl")

    assert sorter.sort_existing_misplaced(config, batch_id="t") == 2
    assert sorted(p.name for p in project.iterdir()) == ["flanged_mace_v1.stl", "flanged_mace_v2.stl"]
    assert other.exists()


def test_an_old_3mf_is_tidied_without_being_held(config, downloads):
    import hold_manager

    config["hold"]["enabled"] = True
    old = _file(downloads / "3D Models" / "old_plate.3mf", age=30 * 86400)
    fresh = _file(downloads / "3D Models" / "todays_plate.3mf", age=60)

    sorter.sort_existing_misplaced(config, batch_id="t")

    assert (downloads / "3D Models" / "3MF" / "old_plate.3mf").exists() and not old.exists()
    assert fresh.exists(), "a file still being worked on keeps its grace period"
    assert hold_manager.is_held(fresh) and not hold_manager.is_held(old)


def test_an_active_project_does_not_empty_the_sorted_folders(config, downloads, tmp_path):
    project = tmp_path / "Turbine"
    project.mkdir()
    active = {"enabled": True, "project_name": "Turbine", "target_directory": str(project)}
    context = sorter.compile_routing(config, active)
    library = [_file(downloads / "3D Models" / "STL" / "benchy.stl"), _file(downloads / "CAD" / "STEP" / "gear.step")]

    assert sorter.sort_existing_misplaced(config, batch_id="t", routing_context=context) == 0
    assert all(path.exists() for path in library)
    assert list(project.iterdir()) == []
    # a new download still goes to the project
    assert sorter.evaluate_routing("new_part.stl", config, routing_context=context)["is_active_project"] is True


# ---------------------------------------------------------------------------
# 5. the watcher: paused means nothing moves, including at startup
# ---------------------------------------------------------------------------

def _wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_startup_sort_waits_while_sorting_is_paused(config, downloads):
    config["onboarding_completed"] = True
    path = watch.CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    waiting = _file(downloads / "report.pdf")
    in_main_folder = _file(downloads / "Documents" / "old.pdf")
    sorter.set_paused(True)

    stop = threading.Event()
    thread = threading.Thread(target=watch.run, kwargs={"stop": stop}, daemon=True)
    thread.start()
    try:
        time.sleep(1.5)
        assert waiting.exists() and in_main_folder.exists(), "nothing may move while sorting is paused"

        sorter.set_paused(False)
        watch.WAKE_EVENT.set()
        pdfs = downloads / "Documents" / "PDF"
        assert _wait_for(lambda: (pdfs / "report.pdf").exists() and (pdfs / "old.pdf").exists())
    finally:
        stop.set()
        watch.WAKE_EVENT.set()
        thread.join(timeout=15)
    assert not thread.is_alive()
