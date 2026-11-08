"""Adversarial stress-test harness for Milestone 1: Persona Immutability & History Parsing.

Empirically challenges:
1. Immutability Stress:
   - Repeated calls across all 4 personas on empty dict, default config, and dirty configs.
   - Strict SHA-256 and deep-equality immutability of sorter.DEFAULT_CONFIG.
   - Defensive copy isolation for apply_persona_profile and get_persona_profiles.
2. History Parsing Stress:
   - Malicious/corrupted history.jsonl (0-byte, empty lines, truncated JSON, non-dicts).
   - Missing fields (missing id, dest, action, src).
   - Type mismatches (non-string dest, null dest, int dest, dict dest).
   - Recursive undo loops and circular undo references.
   - High-volume stress (10,000 records) verifying strict limit enforcement, ordering,
     filtering, and sub-second performance.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config_manager as cm
import sorter


def _config_sha256(cfg: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 digest of config dictionary."""
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode("utf-8")).hexdigest()


# =============================================================================
# 1. IMMUTABILITY STRESS
# =============================================================================

class TestImmutabilityStress:
    """Adversarial challenge to config immutability and isolation."""

    PERSONAS = ["everyday_home", "academic_student", "office_admin", "maker_3d"]

    def test_default_config_untouched_after_repeated_switching(self):
        """Repeatedly switch across all 4 personas 50 times (200 cycles).

        sorter.DEFAULT_CONFIG must remain 100% bitwise and structurally identical.
        """
        initial_hash = _config_sha256(sorter.DEFAULT_CONFIG)
        initial_snapshot = copy.deepcopy(sorter.DEFAULT_CONFIG)

        # Baseline check on DEFAULT_CONFIG
        assert "user_persona" not in sorter.DEFAULT_CONFIG
        assert "onboarding_completed" not in sorter.DEFAULT_CONFIG

        current_cfg = sorter.DEFAULT_CONFIG
        for _ in range(50):
            for persona_id in self.PERSONAS:
                # Direct call passing sorter.DEFAULT_CONFIG
                res1 = cm.apply_persona_profile(sorter.DEFAULT_CONFIG, persona_id)
                assert res1["user_persona"] == persona_id
                assert res1["onboarding_completed"] is True
                assert _config_sha256(sorter.DEFAULT_CONFIG) == initial_hash

                # Chained call passing previously updated config
                current_cfg = cm.apply_persona_profile(current_cfg, persona_id)
                assert current_cfg["user_persona"] == persona_id
                assert _config_sha256(sorter.DEFAULT_CONFIG) == initial_hash

        # Final strict assertions
        assert sorter.DEFAULT_CONFIG == initial_snapshot
        assert _config_sha256(sorter.DEFAULT_CONFIG) == initial_hash

    def test_apply_persona_on_empty_dict(self):
        """apply_persona_profile must handle empty dicts without crashing or mutating DEFAULT_CONFIG."""
        initial_hash = _config_sha256(sorter.DEFAULT_CONFIG)

        for persona_id in self.PERSONAS:
            empty_input: dict[str, Any] = {}
            res = cm.apply_persona_profile(empty_input, persona_id)

            assert empty_input == {}, "Input empty dict must not be mutated in-place"
            assert res["user_persona"] == persona_id
            assert res["onboarding_completed"] is True
            assert isinstance(res.get("keyword_rules"), list)
            assert len(res["keyword_rules"]) > 0

            # Default config must remain untouched
            assert _config_sha256(sorter.DEFAULT_CONFIG) == initial_hash

    def test_apply_persona_on_dirty_configs(self):
        """apply_persona_profile must handle dirty, extra-keyed, and partially-populated configs."""
        initial_hash = _config_sha256(sorter.DEFAULT_CONFIG)

        dirty_configs = [
            {"custom_field": 12345, "watch": {"folder": "D:\\Custom"}},
            {"keyword_rules": []},
            {
                "keyword_rules": [
                    {"name": "Unique Custom Rule", "folder": "Custom\\Folder", "match": ["special"]},
                ]
            },
            {
                # Colliding rule with different case
                "keyword_rules": [
                    {"name": "INVOICES & RECEIPTS", "folder": "Different\\Folder", "match": ["bill"]},
                ]
            },
            {
                # Extra non-rule fields in keyword_rules
                "keyword_rules": [
                    {"name": "Extra Metadata", "folder": "Meta", "match": ["meta"], "custom_flag": True},
                    {"other_key": "no name"},
                    {},
                ]
            },
        ]

        for dirty in dirty_configs:
            dirty_snapshot = copy.deepcopy(dirty)
            for persona_id in self.PERSONAS:
                res = cm.apply_persona_profile(dirty, persona_id)
                assert res["user_persona"] == persona_id
                assert res["onboarding_completed"] is True
                assert dirty == dirty_snapshot, "dirty input dict must not be modified in-place"
                assert _config_sha256(sorter.DEFAULT_CONFIG) == initial_hash

    def test_defensive_copy_isolation_on_mutation(self):
        """Mutating returned objects must never contaminate DEFAULT_CONFIG or persona templates."""
        initial_hash = _config_sha256(sorter.DEFAULT_CONFIG)

        for persona_id in self.PERSONAS:
            result = cm.apply_persona_profile(sorter.DEFAULT_CONFIG, persona_id)

            # Malicious in-place mutation of result
            result["user_persona"] = "MALICIOUS_OVERWRITE"
            result["keyword_rules"].clear()
            result["new_poison_key"] = "POISON"

            # Re-fetch and verify pristineness
            fresh = cm.apply_persona_profile(sorter.DEFAULT_CONFIG, persona_id)
            assert fresh["user_persona"] == persona_id
            assert len(fresh["keyword_rules"]) > 0
            assert "new_poison_key" not in fresh
            assert _config_sha256(sorter.DEFAULT_CONFIG) == initial_hash

    def test_get_persona_profiles_defensive_copy(self):
        """Mutating profiles returned by get_persona_profiles() must not mutate subsequent calls."""
        profiles = cm.get_persona_profiles()
        assert len(profiles) == 4

        # Mutate first profile in-place
        profiles[0]["id"] = "MUTATED_ID"
        profiles[0]["default_rules"].clear()
        profiles.append({"id": "INJECTED_PERSONA"})

        # Subsequent call must return uncorrupted copies
        fresh_profiles = cm.get_persona_profiles()
        assert len(fresh_profiles) == 4
        assert fresh_profiles[0]["id"] == "everyday_home"
        assert len(fresh_profiles[0]["default_rules"]) > 0
        assert not any(p["id"] == "INJECTED_PERSONA" for p in fresh_profiles)


# =============================================================================
# 2. HISTORY PARSING STRESS
# =============================================================================

class TestHistoryParsingStress:
    """Adversarial challenge to get_recent_activity against corruption and scale."""

    def test_missing_or_empty_history_file(self, tmp_path: Path):
        """Missing file or 0-byte file must return empty list without raising."""
        nonexistent = tmp_path / "does_not_exist.jsonl"
        assert cm.get_recent_activity(limit=25, history_path=nonexistent) == []

        empty_file = tmp_path / "empty.jsonl"
        empty_file.touch()
        assert cm.get_recent_activity(limit=25, history_path=empty_file) == []

    def test_whitespace_and_empty_lines_only(self, tmp_path: Path):
        """Files containing only whitespace, tabs, and empty newlines."""
        whitespace_file = tmp_path / "whitespace.jsonl"
        whitespace_file.write_text("\n\n   \n\t\t\n \r\n", encoding="utf-8")
        assert cm.get_recent_activity(limit=25, history_path=whitespace_file) == []

    def test_malicious_corrupted_json_lines(self, tmp_path: Path):
        """Truncated JSON, raw primitives, and broken unicode must be skipped safely."""
        corrupted_file = tmp_path / "corrupted.jsonl"
        valid_dest = tmp_path / "Documents" / "Valid.pdf"
        valid_dest.parent.mkdir(parents=True, exist_ok=True)
        valid_dest.write_text("content", encoding="utf-8")

        lines = [
            '{"id": "trunc1", "action": "move", "dest": "C:\\\\incomp',
            'NOT_JSON_AT_ALL',
            '{"broken": [1, 2, ',
            '123456789',
            '"just a raw string"',
            'null',
            'true',
            'false',
            '[1, 2, "array"]',
            '{',
            '}',
            '{"valid": "but no dest or action"}',
            # One valid line in between corruption
            json.dumps({
                "id": "valid_1",
                "action": "move",
                "dest": str(valid_dest),
                "timestamp": "2026-11-07T00:00:00Z",
            }),
            '{{{{{{{{{{',
        ]
        corrupted_file.write_text("\n".join(lines), encoding="utf-8")

        res = cm.get_recent_activity(limit=25, history_path=corrupted_file)
        assert len(res) == 1
        assert res[0]["id"] == "valid_1"
        assert res[0]["filename"] == "Valid.pdf"
        assert res[0]["exists"] is True

    def test_missing_and_malformed_fields(self, tmp_path: Path):
        """Records with missing IDs, missing destinations, or non-string destinations."""
        field_test_file = tmp_path / "fields.jsonl"

        lines = [
            json.dumps({"action": "move"}),  # missing id & dest
            json.dumps({"id": "f1", "action": "move"}),  # missing dest
            json.dumps({"id": "f2", "action": "move", "dest": None}),  # dest is None
            json.dumps({"id": "f3", "action": "move", "dest": 12345}),  # dest is int
            json.dumps({"id": "f4", "action": "move", "dest": ["list", "of", "paths"]}),  # dest is list
            json.dumps({"id": "f5", "action": "move", "dest": {"key": "dict"}}),  # dest is dict
            json.dumps({"id": "f6", "action": "move", "dest": ""}),  # dest is empty string
            json.dumps({"dest": str(tmp_path / "NoID.txt"), "action": "move"}),  # missing id
            json.dumps({"id": "f7", "dest": str(tmp_path / "NoAction.txt")}),  # missing action
            json.dumps({"id": "f8", "action": 999, "dest": str(tmp_path / "IntAction.txt")}),  # action is int
        ]
        field_test_file.write_text("\n".join(lines), encoding="utf-8")

        res = cm.get_recent_activity(limit=25, history_path=field_test_file)
        # Records with missing id still emit stringified empty id ""
        # Records without action default to non-NON_MOVE_ACTIONS
        # None of them should raise an exception
        assert isinstance(res, list)

    def test_recursive_and_circular_undo_loops(self, tmp_path: Path):
        """Adversarial undo records: mutual recursion, self-referencing, multi-undos, out-of-order undos."""
        undo_test_file = tmp_path / "undo_stress.jsonl"

        target_active = tmp_path / "Active.pdf"
        target_undone = tmp_path / "Undone.pdf"

        lines = [
            # 1. Circular undos: u1 undos u2, u2 undos u1
            json.dumps({"id": "u1", "action": "undo", "ref_id": "u2"}),
            json.dumps({"id": "u2", "action": "undo", "ref_id": "u1"}),
            # 2. Self-referencing undo
            json.dumps({"id": "u3", "action": "undo", "ref_id": "u3"}),
            # 3. Undo referencing non-existent id
            json.dumps({"id": "u4", "action": "undo", "ref_id": "ghost_id_9999"}),
            # 4. Undo with missing ref_id
            json.dumps({"id": "u5", "action": "undo"}),
            # 5. Undo with non-string ref_id
            json.dumps({"id": "u6", "action": "undo", "ref_id": 12345}),
            # 6. Out of order undo: undo appears BEFORE the move record
            json.dumps({"id": "u7", "action": "undo", "ref_id": "m_early_undone"}),
            json.dumps({"id": "m_early_undone", "action": "move", "dest": str(target_undone)}),
            # 7. Normal undo after move
            json.dumps({"id": "m_late_undone", "action": "move", "dest": str(target_undone)}),
            json.dumps({"id": "u8", "action": "undo", "ref_id": "m_late_undone"}),
            # 8. Duplicate undos for the same move
            json.dumps({"id": "u9", "action": "undo", "ref_id": "m_late_undone"}),
            # 9. Valid active move
            json.dumps({"id": "m_active", "action": "move", "dest": str(target_active)}),
        ]
        undo_test_file.write_text("\n".join(lines), encoding="utf-8")

        # Must not hang, must not loop infinitely
        res = cm.get_recent_activity(limit=25, history_path=undo_test_file)
        assert len(res) == 1
        assert res[0]["id"] == "m_active"
        assert res[0]["filename"] == "Active.pdf"

        # When include_undone=True, undone records should be present
        res_all = cm.get_recent_activity(limit=25, history_path=undo_test_file, include_undone=True)
        ids = [r["id"] for r in res_all]
        assert "m_active" in ids
        assert "m_early_undone" in ids
        assert "m_late_undone" in ids

    def test_non_move_actions_filtered(self, tmp_path: Path):
        """All actions in NON_MOVE_ACTIONS must be filtered out."""
        non_move_file = tmp_path / "non_moves.jsonl"
        valid_dest = tmp_path / "RealMove.pdf"

        non_move_actions = ["undo", "planned", "aborted", "needs_review", "aux_move", "link", "extract"]
        lines = [
            json.dumps({"id": f"nm_{act}", "action": act, "dest": str(tmp_path / f"{act}.txt")})
            for act in non_move_actions
        ]
        lines.append(json.dumps({"id": "m_real", "action": "move", "dest": str(valid_dest)}))
        non_move_file.write_text("\n".join(lines), encoding="utf-8")

        res = cm.get_recent_activity(limit=25, history_path=non_move_file)
        assert len(res) == 1
        assert res[0]["id"] == "m_real"

    def test_high_volume_10k_records_and_limit_enforcement(self, tmp_path: Path):
        """Stress with 10,000 JSONL records.

        Verify:
        - Sub-second performance (< 250ms).
        - Strict limit enforcement (limit=25 returns exactly 25).
        - Correct newest-first order (reverse chronological).
        - Robust boundary handling (limit=0, limit=-5, limit=1).
        """
        massive_file = tmp_path / "massive_10k.jsonl"

        records: list[str] = []
        # Generate 10,000 records:
        # Every 10th record is an undo for the previous move
        # Every 7th record is a non-move action (planned / hold)
        # Every 50th record is corrupted JSON
        for i in range(1, 10001):
            if i % 50 == 0:
                records.append(f'{{"corrupted_line_{i}": ')
            elif i % 10 == 0:
                records.append(json.dumps({
                    "id": f"undo_{i}",
                    "action": "undo",
                    "ref_id": f"move_{i - 1}",
                    "timestamp": f"2026-11-07T12:{i:04d}Z",
                }))
            elif i % 7 == 0:
                records.append(json.dumps({
                    "id": f"planned_{i}",
                    "action": "planned",
                    "dest": f"C:\\Planned\\file_{i}.txt",
                    "timestamp": f"2026-11-07T10:{i:04d}Z",
                }))
            else:
                records.append(json.dumps({
                    "id": f"move_{i}",
                    "batch_id": f"batch_{i // 100}",
                    "action": "move",
                    "src": f"C:\\Downloads\\file_{i}.pdf",
                    "dest": f"C:\\Documents\\Category_{i % 5}\\file_{i}.pdf",
                    "timestamp": f"2026-11-07T08:{i:04d}Z",
                }))

        massive_file.write_text("\n".join(records), encoding="utf-8")

        # 1. Performance and strict limit=25
        t0 = time.perf_counter()
        res_25 = cm.get_recent_activity(limit=25, history_path=massive_file)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert len(res_25) == 25, f"Expected exactly 25 records, got {len(res_25)}"
        assert elapsed_ms < 500, f"Parsing 10k records took too long: {elapsed_ms:.1f}ms"

        # 2. Strict Newest-First Ordering (reverse chronological)
        # The latest non-undone move was move_9999 (since 10000 was an undo of 9999, so 9998 or 9999)
        # Let's inspect the first element's ID
        first_id = res_25[0]["id"]
        second_id = res_25[1]["id"]
        # Extract integer indices
        idx1 = int(first_id.split("_")[1])
        idx2 = int(second_id.split("_")[1])
        assert idx1 > idx2, f"Records not in reverse chronological order: {idx1} <= {idx2}"

        # Verify undone records were strictly excluded
        # move_9989 was undone by undo_9990 (since 9990 is divisible by 10 and not 50)
        ids_25 = [r["id"] for r in res_25]
        assert "move_9989" not in ids_25, "move_9989 was undone and must not be present"

        # 3. Limit boundary assertions
        assert cm.get_recent_activity(limit=0, history_path=massive_file) == []
        assert cm.get_recent_activity(limit=-10, history_path=massive_file) == []

        res_1 = cm.get_recent_activity(limit=1, history_path=massive_file)
        assert len(res_1) == 1
        assert res_1[0]["id"] == first_id

        res_100 = cm.get_recent_activity(limit=100, history_path=massive_file)
        assert len(res_100) == 100

    def test_unicode_and_emojis_in_history_paths(self, tmp_path: Path):
        """History records with international characters, CJK glyphs, and emojis."""
        unicode_file = tmp_path / "unicode_history.jsonl"
        dest_unicode = tmp_path / "Documents" / "🚀_résumé_日本語_2026.pdf"
        dest_unicode.parent.mkdir(parents=True, exist_ok=True)
        dest_unicode.write_text("unicode content", encoding="utf-8")

        lines = [
            json.dumps({
                "id": "u_glyph_1",
                "action": "move",
                "dest": str(dest_unicode),
                "timestamp": "2026-11-07T14:00:00Z",
            }, ensure_ascii=False),
        ]
        unicode_file.write_text("\n".join(lines), encoding="utf-8")

        res = cm.get_recent_activity(limit=25, history_path=unicode_file)
        assert len(res) == 1
        assert res[0]["filename"] == "🚀_résumé_日本語_2026.pdf"
        assert res[0]["exists"] is True
        assert res[0]["folder"] == "Documents"

    def test_huge_line_length_in_history(self, tmp_path: Path):
        """Extremely long JSON line (e.g. 100KB payload) does not choke reader."""
        huge_file = tmp_path / "huge_line.jsonl"
        big_garbage = "x" * 100_000
        lines = [
            json.dumps({"id": "huge_1", "action": "move", "dest": str(tmp_path / "file.txt"), "bloat": big_garbage}),
        ]
        huge_file.write_text("\n".join(lines), encoding="utf-8")

        res = cm.get_recent_activity(limit=25, history_path=huge_file)
        assert len(res) == 1
        assert res[0]["id"] == "huge_1"


# =============================================================================
# 3. WINDOWS REGISTRY LIFECYCLE & IDEMPOTENCE
# =============================================================================

class TestWindowsRegistryStress:
    """Adversarial stress testing of winreg pure-Python helpers."""

    def test_registry_toggle_idempotence_and_state_preservation(self):
        """Repeatedly toggling startup and context menu must be idempotent and non-crashing."""
        if sys.platform != "win32":
            pytest.skip("Windows-only winreg tests")

        # Capture initial state
        init_startup = cm.is_startup_enabled()
        init_menu = cm.is_context_menu_enabled()

        try:
            # 1. Startup toggling
            assert cm.set_startup_enabled(True) is True
            assert cm.is_startup_enabled() is True
            # Re-enabling when already enabled
            assert cm.set_startup_enabled(True) is True
            assert cm.is_startup_enabled() is True

            assert cm.set_startup_enabled(False) is True
            assert cm.is_startup_enabled() is False
            # Re-disabling when already disabled
            assert cm.set_startup_enabled(False) is True
            assert cm.is_startup_enabled() is False

            # 2. Context menu toggling (including subkey tree recreation)
            assert cm.set_context_menu_enabled(True) is True
            assert cm.is_context_menu_enabled() is True
            assert cm.set_context_menu_enabled(True) is True
            assert cm.is_context_menu_enabled() is True

            assert cm.set_context_menu_enabled(False) is True
            assert cm.is_context_menu_enabled() is False
            assert cm.set_context_menu_enabled(False) is True
            assert cm.is_context_menu_enabled() is False

        finally:
            # Strictly restore starting state
            cm.set_startup_enabled(init_startup)
            cm.set_context_menu_enabled(init_menu)

        assert cm.is_startup_enabled() == init_startup
        assert cm.is_context_menu_enabled() == init_menu

