"""The starter rules each profile sets up must not catch the wrong files.

Single words like "statement", "tax" and "reading" sent a student's
Personal_Statement_UCLA.pdf to Invoices and a "Reading Festival" ticket to
School notes. The 3D-printing profile's "haas" keyword beat the Haas detector,
and switching profile left the old profile's rules behind.
"""

from __future__ import annotations

import copy

import pytest

import config_manager
import sorter


def _routed(filename: str, persona_id: str) -> str:
    cfg = config_manager.apply_persona_profile(copy.deepcopy(sorter.DEFAULT_CONFIG), persona_id)
    return sorter.evaluate_routing(filename, cfg)["destination"]


def _rule_names(cfg: dict) -> set[str]:
    return {r["name"] for r in cfg["keyword_rules"]}


@pytest.mark.parametrize("name, token", [
    ("Bank Statement March.pdf", "bank_statement"),
    ("bank-statement.pdf", "bank_statement"),
    ("BankStatement.pdf", "bank_statement"),
    ("W-2 2024.pdf", "w-2"),
    ("W2 2024.pdf", "w-2"),
    ("lecture ime_3311 notes.docx", "ime 3311"),
])
def test_multi_word_keywords_match_any_separator(name, token):
    assert sorter.matches_keyword(name, token)


@pytest.mark.parametrize("name, token", [
    ("statement.pdf", "bank_statement"),
    ("embankment statement.pdf", "bank_statement"),
    ("syntax.pdf", "tax"),
])
def test_keywords_still_match_whole_words_only(name, token):
    assert not sorter.matches_keyword(name, token)


@pytest.mark.parametrize("filename, persona_id", [
    ("Personal_Statement_UCLA.pdf", "academic_student"),
    ("Statement of Purpose.pdf", "academic_student"),
    ("Mission Statement Draft.docx", "office_admin"),
    ("Tax Policy Essay.docx", "office_admin"),
    ("Reading Festival tickets.pdf", "academic_student"),
])
def test_everyday_words_do_not_trigger_name_rules(filename, persona_id):
    dest = _routed(filename, persona_id)
    assert "Invoices" not in dest
    assert "Financial" not in dest
    assert "School" not in dest


@pytest.mark.parametrize("filename, persona_id, folder", [
    ("Bank Statement March.pdf", "office_admin", "Documents\\Financial & Tax"),
    ("tax_return_2024.pdf", "office_admin", "Documents\\Financial & Tax"),
    ("Amazon invoice 1234.pdf", "everyday_home", "Documents\\Invoices & Receipts"),
    ("ME 3210 Syllabus.pdf", "academic_student", "Documents\\School\\Notes"),
    ("Week 3 reading list.pdf", "academic_student", "Documents\\School\\Notes"),
])
def test_real_documents_still_reach_their_folders(filename, persona_id, folder):
    assert _routed(filename, persona_id) == folder


def test_maker_profile_lets_the_file_type_rules_and_detectors_work():
    assert _routed("gear.stl", "maker_3d") == "3D Models"
    assert _routed("benchy.stl", "maker_3d") == "3D Models"
    cfg = config_manager.apply_persona_profile(copy.deepcopy(sorter.DEFAULT_CONFIG), "maker_3d")
    for rule in cfg["keyword_rules"]:
        assert "haas" not in [w.lower() for w in rule.get("match", [])]


def test_profiles_contain_what_their_descriptions_promise():
    def names(persona_id):
        return _rule_names(config_manager.apply_persona_profile({}, persona_id))

    everyday = names("everyday_home")
    assert "Resumes & Career" not in everyday and "Financial & Taxes" not in everyday
    assert everyday <= names("academic_student"), "Student is Everyday plus school folders"
    assert everyday <= names("office_admin"), "Office is Everyday plus office folders"
    assert everyday <= names("maker_3d")
    assert "Financial & Taxes" not in names("academic_student")


def test_switching_profile_removes_the_old_profiles_rules_but_keeps_yours():
    cfg = config_manager.apply_persona_profile({}, "office_admin")
    cfg["keyword_rules"].append({"name": "Band", "folder": "Music\\Band", "match": ["setlist"]})

    switched = config_manager.apply_persona_profile(cfg, "everyday_home")

    assert "Financial & Taxes" not in _rule_names(switched)
    assert "Resumes & Career" not in _rule_names(switched)
    assert "Band" in _rule_names(switched)


def test_a_profile_rule_you_added_words_to_is_kept():
    cfg = config_manager.apply_persona_profile({}, "office_admin")
    career = next(r for r in cfg["keyword_rules"] if r["name"] == "Resumes & Career")
    career["match"].append("internship")

    switched = config_manager.apply_persona_profile(cfg, "everyday_home")

    assert "Resumes & Career" in _rule_names(switched)


def test_rules_saved_by_an_older_version_are_recognised():
    old_maker_rule = {
        "name": "CNC & Slicer G-Code",
        "folder": "Code\\Gcode",
        "match": ["slice", "layer", "toolpath", "contour", "gcode", "haas"],
        "extensions": [".gcode", ".bgcode", ".nc", ".tap", ".ngc"],
        "enabled": True,
    }
    switched = config_manager.apply_persona_profile({"keyword_rules": [old_maker_rule]}, "maker_3d")

    assert "CNC & Slicer G-Code" not in _rule_names(switched)
