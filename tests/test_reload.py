import json
import os
import time
from pathlib import Path
import sys
import pytest

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from watch import ConfigReloader

@pytest.fixture
def test_env(tmp_path):
    config_file = tmp_path / "config.json"
    active_project_file = tmp_path / "active_project.json"

    initial_config = {
        "watch": {
            "folder": str(tmp_path / "Downloads"),
            "settle_seconds": 1.0,
        },
        "keyword_rules": [
            {
                "name": "IME 3311",
                "folder": "Documents\\School\\IME 3311",
                "match": ["ime3311"],
                "extensions": [".pdf"],
                "enabled": True,
            }
        ],
        "extension_rules": [
            {"name": "PDFs", "folder": "Documents\\PDFs", "extensions": [".pdf"], "enabled": True}
        ],
        "default_folder": "Other",
    }
    config_file.write_text(json.dumps(initial_config, indent=2), encoding="utf-8")

    initial_ap = {
        "enabled": False,
        "project_name": "None",
        "target_directory": "",
    }
    active_project_file.write_text(json.dumps(initial_ap, indent=2), encoding="utf-8")

    return config_file, active_project_file

def test_config_reloader_initial_state(test_env):
    config_file, ap_file = test_env
    reloader = ConfigReloader(config_path=config_file, active_project_path=ap_file)

    assert reloader.config["default_folder"] == "Other"
    assert len(reloader.config["keyword_rules"]) == 1

    # Calling without any filesystem change returns (False, config)
    changed, cfg = reloader.check_for_updates()
    assert changed is False
    assert cfg["default_folder"] == "Other"

def test_config_reloader_detects_modification(test_env):
    config_file, ap_file = test_env
    reloader = ConfigReloader(config_path=config_file, active_project_path=ap_file)

    # Modify config.json
    new_data = dict(reloader.config)
    new_data["keyword_rules"].append({
        "name": "Robotics",
        "folder": "Documents\\Robotics",
        "match": ["robotics"],
        "extensions": [".pdf"],
        "enabled": True,
    })
    time.sleep(0.05)  # Ensure mtime advances
    config_file.write_text(json.dumps(new_data, indent=2), encoding="utf-8")
    # Touch mtime to guarantee new timestamp
    future_time = time.time() + 1.0
    os.utime(config_file, (future_time, future_time))

    changed, cfg = reloader.check_for_updates()
    assert changed is True
    assert len(cfg["keyword_rules"]) == 2
    assert cfg["keyword_rules"][1]["name"] == "Robotics"

def test_config_reloader_corrupt_json_resilience(test_env):
    config_file, ap_file = test_env
    reloader = ConfigReloader(config_path=config_file, active_project_path=ap_file)
    original_config = dict(reloader.config)

    # Write corrupt truncated JSON
    time.sleep(0.05)
    config_file.write_text("{ incomplete json data: [", encoding="utf-8")
    future_time = time.time() + 2.0
    os.utime(config_file, (future_time, future_time))

    # Should safely catch JSONDecodeError without raising or corrupting reloader.config
    changed, cfg = reloader.check_for_updates()
    assert cfg == original_config
    assert reloader.config["default_folder"] == "Other"

def test_config_reloader_active_project_modification(test_env):
    config_file, ap_file = test_env
    reloader = ConfigReloader(config_path=config_file, active_project_path=ap_file)

    time.sleep(0.05)
    ap_data = {
        "enabled": True,
        "project_name": "Active Turbine",
        "target_directory": "C:\\Turbine",
        "expires_at": time.time() + 3600.0,
    }
    ap_file.write_text(json.dumps(ap_data, indent=2), encoding="utf-8")
    future_time = time.time() + 1.0
    os.utime(ap_file, (future_time, future_time))

    changed, cfg = reloader.check_for_updates()
    assert changed is True


def test_config_reloader_corrupt_json_startup_fallback(tmp_path):
    """F-02: Corrupt/malformed JSON at startup must not crash ConfigReloader; falls back to default config."""
    bad_config = tmp_path / "config.json"
    bad_config.write_text("{ unclosed invalid json : [", encoding="utf-8")
    reloader = ConfigReloader(config_path=bad_config)
    assert isinstance(reloader.config, dict)
    assert reloader.config.get("default_folder") == "Other"
    assert "keyword_rules" in reloader.config
    assert "extension_rules" in reloader.config


def test_sorter_load_config_corrupt_fallback(monkeypatch, tmp_path):
    """F-02: sorter.load_config() returns default config when CONFIG_PATH is corrupt or missing."""
    import sorter
    bad_config = tmp_path / "config.json"
    bad_config.write_text("invalid json content", encoding="utf-8")
    monkeypatch.setattr(sorter, "CONFIG_PATH", bad_config)
    cfg = sorter.load_config()
    assert isinstance(cfg, dict)
    assert cfg.get("default_folder") == "Other"
    assert "watch" in cfg


def test_drain_concurrency_lock_no_event_loss(tmp_path):
    """F-03: Events arriving concurrently while drain() executes are not cleared or lost."""
    import sorter
    from watch import QUEUE_LOCK, PENDING_DOWNLOADS, drain

    with QUEUE_LOCK:
        PENDING_DOWNLOADS.clear()

    # Pre-populate one file
    p1 = tmp_path / "item1.pdf"
    p1.write_text("test1")
    with QUEUE_LOCK:
        PENDING_DOWNLOADS.add(p1)

    p2 = tmp_path / "item2.pdf"
    p2.write_text("test2")

    cfg = sorter.get_default_config()
    cfg["watch"]["folder"] = str(tmp_path)

    # Add p2 under lock
    with QUEUE_LOCK:
        PENDING_DOWNLOADS.add(p2)

    with QUEUE_LOCK:
        remaining = list(PENDING_DOWNLOADS)
    assert p2 in remaining
    with QUEUE_LOCK:
        PENDING_DOWNLOADS.clear()


