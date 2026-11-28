"""Shared text helpers for rules and folder cues."""

from __future__ import annotations

from download_sorter import rule_text as rt


def test_extensions_are_cleaned_and_deduplicated() -> None:
    assert rt.normalize_ext_tokens("pdf, *.DOCX .xlsx/.csv pdf") == [".pdf", ".docx", ".xlsx", ".csv"]


def test_websites_keep_their_full_host_for_rules() -> None:
    assert rt.normalize_domain_tokens("https://dashboard.stripe.com/pay") == ["dashboard.stripe.com"]


def test_folder_websites_use_the_main_domain() -> None:
    got = rt.registrable_domain_tokens("https://dashboard.stripe.com/x, www.bbc.co.uk, mail.google.com")
    assert got == ["stripe.com", "bbc.co.uk", "mail.google.com"]


def test_a_rule_with_no_cue_is_reported() -> None:
    assert rt.rule_has_any_cue(extensions=[], keywords=[], domains=[]) is False
