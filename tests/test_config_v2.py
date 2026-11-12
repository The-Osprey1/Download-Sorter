"""Schema v2 config migration and per-feature switches."""

from __future__ import annotations

import copy
import json

import config_manager
import sorter


def _v1_config():
    return {
        "watch": {"folder": "C:/x/Downloads", "settle_seconds": 1.5},
        "keyword_rules": [],
        "extension_rules": [],
        "my_custom_key": {"keep": "me"},
    }


def test_defaults_fill_missing_blocks_and_keep_unknown_keys():
    cfg = sorter.apply_v2_defaults(_v1_config())
    assert cfg["schema_version"] == 2
    assert cfg["my_custom_key"] == {"keep": "me"}
    assert cfg["batch_handoff"]["enabled"] is True
    assert cfg["hold"]["enabled"] is False  # off by default since 2.5.0
    assert cfg["remodel"]["enabled"] is True
    assert cfg["mirror"]["enabled"] is False


def test_legacy_settle_default_moves_to_three_seconds_but_custom_values_stay():
    assert sorter.apply_v2_defaults(_v1_config())["watch"]["settle_seconds"] == 3.0
    custom = _v1_config()
    custom["watch"]["settle_seconds"] = 2.0
    assert sorter.apply_v2_defaults(custom)["watch"]["settle_seconds"] == 2.0


def test_user_values_in_v2_blocks_are_never_overwritten():
    cfg = _v1_config()
    cfg["schema_version"] = 2
    cfg["hold"] = {"enabled": False, "hold_days": 2}
    out = sorter.apply_v2_defaults(cfg)
    assert out["hold"]["enabled"] is False and out["hold"]["hold_days"] == 2
    assert out["hold"]["max_held"] == 15  # missing sub-key filled


def test_default_config_is_v2():
    cfg = sorter.get_default_config()
    assert cfg["schema_version"] == 2
    assert cfg["watch"]["settle_seconds"] == 3.0
    assert cfg["hold"]["hold_days"] == 7 and cfg["hold"]["max_held"] == 15
    assert cfg["batch_handoff"]["gap_seconds"] == 25
    assert cfg["batch_handoff"]["max_span_seconds"] == 120
    assert cfg["batch_handoff"]["max_files"] == 50


def test_first_v2_save_backs_up_v1_once(tmp_path):
    target = tmp_path / "config.json"
    original = _v1_config()
    target.write_text(json.dumps(original), encoding="utf-8")
    assert config_manager.save_sorter_config(original, target)
    backup = tmp_path / "config.v1.json"
    assert backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8")) == original
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 2

    # a later save must not clobber the pristine v1 copy
    edited = copy.deepcopy(original)
    edited["keyword_rules"] = [{"name": "n", "folder": "f", "match": ["x"]}]
    assert config_manager.save_sorter_config(edited, target)
    assert json.loads(backup.read_text(encoding="utf-8")) == original


def test_saving_does_not_mutate_callers_dict_and_keeps_unknown_keys(tmp_path):
    target = tmp_path / "config.json"
    cfg = _v1_config()
    snapshot = copy.deepcopy(cfg)
    config_manager.save_sorter_config(cfg, target)
    assert cfg == snapshot
    assert json.loads(target.read_text(encoding="utf-8"))["my_custom_key"] == {"keep": "me"}


def test_fresh_v2_file_gets_no_backup(tmp_path):
    target = tmp_path / "config.json"
    cfg = sorter.get_default_config()
    config_manager.save_sorter_config(cfg, target)
    config_manager.save_sorter_config(cfg, target)
    assert not (tmp_path / "config.v1.json").exists()


def test_loading_a_v1_file_returns_v2_view_without_touching_disk(tmp_path):
    target = tmp_path / "config.json"
    target.write_text(json.dumps(_v1_config()), encoding="utf-8")
    before = target.read_text(encoding="utf-8")
    cfg = config_manager.load_sorter_config(target)
    assert cfg["schema_version"] == 2 and cfg["hold"]["enabled"] is False
    assert target.read_text(encoding="utf-8") == before


def test_makerworld_is_a_managed_category_folder():
    folders = [f.lower() for f in sorter.collect_category_folders(sorter.get_default_config())]
    assert "3d models\\makerworld" in folders
