"""Unit tests for debounced priority queue, config pre-compilation/watcher, and gcode_peek caching."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

import config_manager
import gcode_peek
import watch


class TestDebouncedPriorityQueue:
    def test_debounce_resets_deadline_and_deduplicates(self) -> None:
        q = watch.DebouncedPriorityQueue()
        p = Path("a.pdf")
        q.enqueue("download", p, delay=0.05)
        q.enqueue("download", p, delay=0.20)
        assert q.take_due("download", now=time.monotonic() + 0.06) == []
        due = q.take_due("download", now=time.monotonic() + 0.25)
        assert due == [p]

    def test_priority_order_across_kinds(self) -> None:
        q = watch.DebouncedPriorityQueue()
        now = time.monotonic()
        q.enqueue("misplaced", Path("m.txt"), delay=0.0)
        q.enqueue("document", Path("d.pdf"), delay=0.0)
        q.enqueue("download", Path("dl.stl"), delay=0.0)

        ordered = q.take_all_due(now=now + 1.0)
        assert [k for k, _ in ordered] == ["download", "document", "misplaced"]

    def test_exponential_backoff_and_reset(self) -> None:
        q = watch.DebouncedPriorityQueue(base_retry_delay=0.5, max_retry_delay=4.0)
        p = Path("locked.gcode")
        delays = [q.retry("download", p) for _ in range(5)]
        assert delays == [0.5, 1.0, 2.0, 4.0, 4.0]
        assert q.attempts("download", p) == 5

        q.enqueue("download", p, delay=0.1)
        assert q.attempts("download", p) == 0

    def test_next_delay_and_thread_safety(self) -> None:
        q = watch.DebouncedPriorityQueue()
        assert q.next_delay() is None
        q.enqueue("download", Path("x.zip"), delay=0.5)
        nd = q.next_delay()
        assert nd is not None and 0.0 <= nd <= 0.55

        errors: list[Exception] = []

        def worker(idx: int) -> None:
            try:
                for j in range(50):
                    q.enqueue("download", Path(f"f_{idx}_{j}.bin"), delay=0.0)
                    q.take_due("download")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


class TestConfigCompileAndWatcher:
    def test_compile_rules_skips_disabled_and_normalizes(self) -> None:
        cfg = {
            "keyword_rules": [
                {"name": "School", "folder": "Documents\\School", "keywords": ["ME3210", "Syllabus"], "enabled": True},
                {"name": "Disabled", "folder": "Dead", "keywords": ["skip"], "enabled": False},
            ],
            "extension_rules": [
                {"name": "CAD", "folder": "3D Models", "extensions": ["stl", ".STEP"], "enabled": True},
                {"name": "Off", "folder": "Off", "extensions": [".bin"], "enabled": False},
            ],
            "default_folder": "Other",
        }
        compiled = config_manager.compile_rules(cfg)
        assert len(compiled.keyword_rules) == 1
        assert compiled.keyword_rules[0].pattern is not None
        assert compiled.keyword_rules[0].pattern.search("me3210_hw1.pdf")
        assert ".stl" in compiled.extension_map
        assert ".step" in compiled.extension_map
        assert ".bin" not in compiled.extension_map
        assert "documents\\school" in compiled.category_folders
        assert "3d models" in compiled.category_folders

    def test_config_file_watcher_reloads_and_ignores_invalid_json(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config.json"
        initial = {
            "keyword_rules": [{"name": "School", "folder": "Documents\\School", "keywords": ["syllabus"]}],
            "extension_rules": [{"name": "PDF", "folder": "Documents\\PDFs", "extensions": [".pdf"]}],
        }
        cfg_path.write_text(json.dumps(initial), encoding="utf-8")

        reloads: list[tuple[dict, config_manager.CompiledRules]] = []
        watcher = config_manager.ConfigFileWatcher(cfg_path, on_reload=lambda c, r: reloads.append((c, r)))
        assert ".pdf" in watcher.compiled_rules.extension_map

        # Invalid JSON should be ignored without crashing or dropping active rules
        cfg_path.write_text("{broken json", encoding="utf-8")
        assert watcher.reload_now(force=True) is False
        assert ".pdf" in watcher.compiled_rules.extension_map

        # Valid update reloads and re-compiles
        updated = {
            "keyword_rules": [{"name": "CNC", "folder": "Code\\Haas", "keywords": ["op1"]}],
            "extension_rules": [{"name": "STL", "folder": "3D Models", "extensions": [".stl"]}],
        }
        cfg_path.write_text(json.dumps(updated), encoding="utf-8")
        assert watcher.reload_now(force=True) is True
        assert len(reloads) == 1
        assert ".stl" in watcher.compiled_rules.extension_map
        assert ".pdf" not in watcher.compiled_rules.extension_map


class TestGcodePeekCachingAndPlaceholder:
    def test_sniff_machine_type_caches_by_mtime_ns_and_size(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        gcode_peek.clear_caches()
        nc = tmp_path / "part_op1.nc"
        nc.write_text("%\nO0100\nG90 G54\nT1 M06\nM30\n", encoding="utf-8")

        res1 = gcode_peek.sniff_machine_type(nc)
        assert res1["is_haas"] is True

        # Replace open() to prove second call hits cache without reading disk
        def fail_open(*_a, **_kw):
            raise AssertionError("open() should not be called on cache hit")

        monkeypatch.setattr("builtins.open", fail_open)
        res2 = gcode_peek.sniff_machine_type(nc)
        assert res2["is_haas"] is True

    def test_cloud_placeholder_checked_before_open(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        gcode_peek.clear_caches()
        nc = tmp_path / "cloud_only.gcode"
        nc.write_text("; dummy", encoding="utf-8")

        monkeypatch.setattr(gcode_peek, "is_cloud_placeholder", lambda _p: True)

        def fail_open(*_a, **_kw):
            raise AssertionError("open() must not be called for cloud placeholders")

        monkeypatch.setattr("builtins.open", fail_open)
        sniff_res = gcode_peek.sniff_machine_type(nc)
        assert sniff_res.get("cloud_placeholder") is True
        assert sniff_res["machine_type"] == "generic"

        parse_res = gcode_peek.parse_gcode(nc)
        assert parse_res.get("cloud_placeholder") is True
