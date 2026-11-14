"""Tests for Config persistence, v2 migration, and 4-step deterministic router."""

from __future__ import annotations

import json
from pathlib import Path

from download_sorter.config import Config, Rule, import_v2_config, load_config, save_config
from download_sorter.paths import AppPaths
from download_sorter.router import FileCandidate, evaluate, read_zone_url


def test_atomic_save_and_bak_recovery(app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.default_folder = "Misc"
    save_config(cfg, app_paths)

    cfg.default_folder = "Unsorted"
    save_config(cfg, app_paths)
    assert app_paths.config_file.exists()
    assert app_paths.backup_config_file.exists()

    # Corrupt primary config; load_config must recover cleanly from .bak
    app_paths.config_file.write_text("{corrupt json", encoding="utf-8")
    recovered = load_config(app_paths)
    assert recovered.default_folder == "Misc"


def test_deterministic_first_match_wins_and_normalized_keywords() -> None:
    cfg = Config.with_starter_defaults()
    cfg.rules.insert(
        0,
        Rule(
            id="top-override",
            name="Top Override",
            destination=r"Documents\Priority",
            keywords=["scholarship"],
            extensions=[".pdf"],
        ),
    )

    dec1 = evaluate(FileCandidate("Fidel APM Scholarship Application 2026-27.pdf"), cfg)
    assert dec1.action == "move"
    assert dec1.matched_rule == "Top Override"
    assert dec1.relative_dest == Path(r"Documents\Priority\Fidel APM Scholarship Application 2026-27.pdf")

    # Underscores and spaces normalize identically
    dec2 = evaluate(FileCandidate("FEF CIC 2025 - Career Statement - Devyn Fidel.pdf"), cfg)
    assert dec2.matched_rule == "Resumes & Career"
    assert dec2.relative_dest == Path(r"Documents\Career\FEF CIC 2025 - Career Statement - Devyn Fidel.pdf")

    dec3 = evaluate(FileCandidate("Capacitance_Lab_Report.pdf"), cfg)
    assert dec3.matched_rule == "School coursework"
    assert dec3.relative_dest == Path(r"Documents\School\Capacitance_Lab_Report.pdf")


def test_domain_match_and_type_subfolder_and_leave_fallback() -> None:
    cfg = Config.with_starter_defaults()

    # Domain rule matches MakerWorld
    mw = evaluate(FileCandidate("bracket.3mf", source_url="https://makerworld.com/en/models/123"), cfg)
    assert mw.matched_rule == "MakerWorld Downloads"
    assert mw.relative_dest == Path(r"3D Models\MakerWorld\bracket.3mf")

    # Without domain, .3mf uses type subfolder map
    local_3mf = evaluate(FileCandidate("bracket.3mf"), cfg)
    assert local_3mf.match_type == "extension"
    assert local_3mf.relative_dest == Path(r"3D Models\3MF\bracket.3mf")

    # Unknown extension respects fallback_mode
    cfg.fallback_mode = "leave"
    unknown = evaluate(FileCandidate("mystery.xyz"), cfg)
    assert unknown.action == "leave"
    assert unknown.relative_dest is None

    # Ignore incomplete downloads
    partial = evaluate(FileCandidate("big_file.crdownload"), cfg)
    assert partial.action == "ignore"


def test_import_v2_config(tmp_path: Path) -> None:
    v2_file = tmp_path / "v2.json"
    v2_file.write_text(
        json.dumps(
            {
                "watch": {"folder": r"C:\Users\devyn\Downloads", "settle_seconds": 5.0},
                "default_folder": "Other",
                "type_folders": {"enabled": False, "names": {}},
                "keyword_rules": [
                    {
                        "name": "Invoices",
                        "folder": r"Documents\Invoices",
                        "match": ["invoice"],
                        "sites": [],
                        "extensions": [".pdf"],
                        "enabled": True,
                    }
                ],
                "extension_rules": [
                    {"name": "Docs", "folder": "Documents", "extensions": [".pdf"], "enabled": True}
                ],
            }
        ),
        encoding="utf-8",
    )
    migrated = import_v2_config(v2_file)
    assert migrated.settle_seconds == 5.0
    assert migrated.use_type_subfolders is False
    assert len(migrated.rules) == 1
    assert migrated.rules[0].name == "Invoices"


def test_zone_identifier_ads_safe_when_missing(tmp_path: Path) -> None:
    sample = tmp_path / "plain.txt"
    sample.write_text("hello", encoding="utf-8")
    assert read_zone_url(sample) == ""
