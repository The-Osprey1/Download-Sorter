"""Rule words and rename tokens work for file names in any language, not only English."""

from __future__ import annotations

from pathlib import Path

from download_sorter.config import Config, Rule
from download_sorter.dest import apply_rename_template
from download_sorter.router import FileCandidate, _keyword_matches, evaluate


def test_cyrillic_keyword_matches_cyrillic_file_name() -> None:
    assert _keyword_matches("счет_апрель.pdf", "счет") is True
    assert _keyword_matches("Договор аренды 2026.pdf", "договор") is True


def test_greek_and_arabic_keywords_match() -> None:
    assert _keyword_matches("Τιμολόγιο_2026.pdf", "τιμολόγιο") is True
    assert _keyword_matches("فاتورة_يناير.pdf", "فاتورة") is True


def test_japanese_and_chinese_keywords_match_inside_unspaced_names() -> None:
    # These languages don't put spaces between words, so a word can sit inside a longer run.
    assert _keyword_matches("請求書_2026.pdf", "請求書") is True
    assert _keyword_matches("2026年4月請求書.pdf", "請求書") is True
    assert _keyword_matches("发票2026.pdf", "发票") is True
    assert _keyword_matches("領収書.pdf", "請求書") is False


def test_accents_are_optional_for_latin_letters() -> None:
    assert _keyword_matches("Résumé_2026.pdf", "resume") is True
    assert _keyword_matches("Resume_2026.pdf", "résumé") is True
    assert _keyword_matches("Überweisung_März.pdf", "uberweisung") is True


def test_accented_keyword_does_not_match_unrelated_fragments() -> None:
    # Dropping the accented letters used to turn "résumé" into "r sum", and "über" into "ber".
    assert _keyword_matches("r_sum_of_squares.pdf", "résumé") is False
    assert _keyword_matches("ber_report.pdf", "über") is False


def test_camel_case_and_numbers_still_split_for_non_english_names() -> None:
    assert _keyword_matches("СчетАпрель.pdf", "апрель") is True
    assert _keyword_matches("Rechnung2026.pdf", "rechnung") is True


def test_english_behaviour_is_unchanged() -> None:
    assert _keyword_matches("opencv_tutorial.pdf", "cv") is False
    assert _keyword_matches("presumed_innocent.pdf", "resume") is False
    assert _keyword_matches("Bankruptcy_notice.pdf", "bank") is False
    assert _keyword_matches("lecture_notes.pdf", "lecturenotes") is True
    assert _keyword_matches("Resume2026.pdf", "resume") is True


def test_rule_with_russian_word_files_the_download() -> None:
    cfg = Config(
        rules=[Rule(id="ru", name="Счета", destination="Documents", keywords=["счет"])],
    )
    decision = evaluate(FileCandidate("счет_апрель.pdf"), cfg)
    assert decision.matched_rule == "Счета"
    assert decision.relative_dest == Path("Documents") / "счет_апрель.pdf"


def test_stem_slug_keeps_letters_from_any_language() -> None:
    assert apply_rename_template("{stem_slug}", "Отчёт за май.pdf") == "отчёт-за-май.pdf"
    assert apply_rename_template("{stem_slug}", "請求書 2026.pdf") == "請求書-2026.pdf"


def test_regex_group_values_keep_letters_from_any_language() -> None:
    renamed = apply_rename_template(
        "{client}_{stem}", "file.pdf", regex_groups={"client": "Müller GmbH"}
    )
    assert renamed == "Müller_GmbH_file.pdf"
