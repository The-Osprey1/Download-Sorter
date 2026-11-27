"""Tests for the mistake-proof logic behind the one-screen Folders designer."""

from __future__ import annotations

from pathlib import Path

import pytest

from download_sorter import folder_designer as fd
from download_sorter.config import Config, Rule
from download_sorter.router import FileCandidate, evaluate


def _dest(cfg: Config, name: str, url: str = "") -> str:
    dec = evaluate(FileCandidate(filename=name, source_url=url), cfg, include_trace=False)
    if dec.relative_dest is None:
        return ""
    return str(Path(dec.relative_dest).parent).replace("/", "\\")


def _cfg() -> Config:
    return Config()


def test_add_folder_then_give_it_words_routes_files() -> None:
    cfg = _cfg()
    ch = fd.add_folder(cfg, "School")
    assert ch.selected_path == "School"
    assert fd.is_manual(ch.config, "School")
    ch2 = fd.set_cues(ch.config, "School", fd.FolderCues(keywords=["homework"]))
    assert _dest(ch2.config, "homework_week3.pdf") == "School"
    assert not fd.is_manual(ch2.config, "School")
    # Clearing every cue turns it back into a manual folder without losing it.
    ch3 = fd.set_cues(ch2.config, "School", fd.FolderCues())
    assert fd.find_node(fd.working_architecture(ch3.config), "School") is not None
    assert fd.is_manual(ch3.config, "School")
    assert _dest(ch3.config, "homework_week3.pdf") != "School"


def test_bad_names_are_refused_with_plain_messages() -> None:
    cfg = fd.add_folder(_cfg(), "School").config
    arch = fd.working_architecture(cfg)
    assert fd.check_folder_name(arch, "  ")[1] == "Give the folder a name."
    assert "Windows keeps" in fd.check_folder_name(arch, "con")[1]
    assert "already has" in fd.check_folder_name(arch, "school")[1]
    name, problem = fd.check_folder_name(arch, 'Tax: 2026?')
    assert (name, problem) == ("Tax 2026", "")
    with pytest.raises(fd.DesignerError):
        fd.add_folder(cfg, "SCHOOL")


def test_types_live_in_one_place() -> None:
    cfg = fd.add_folder(_cfg(), "Papers").config
    ch = fd.set_cues(cfg, "Papers", fd.FolderCues(extensions=[".pdf"]))
    assert "moved those types here" in ch.message
    assert _dest(ch.config, "anything.pdf") == "Papers"
    others = [
        fd.path_of(a, n)
        for a in [fd.working_architecture(ch.config)]
        for n in a.nodes
        if ".pdf" in n.extensions
    ]
    assert others == ["Papers"]


def test_rename_and_move_keep_user_rules_pointing_at_the_folder() -> None:
    cfg = fd.add_folder(_cfg(), "Work").config
    cfg.rules.insert(0, Rule(id="u1", name="Invoices", destination="Work", keywords=["invoice"], regex_pattern=r"inv\d+"))
    ch = fd.rename_folder(cfg, "Work", "Jobs")
    assert ch.disk_moves == [("Work", "Jobs")]
    assert any(r.id == "u1" and r.destination == "Jobs" for r in ch.config.rules)
    cfg2 = fd.add_folder(ch.config, "Projects").config
    assert "Projects" in fd.valid_move_targets(cfg2, "Jobs")
    ch2 = fd.move_folder(cfg2, "Jobs", "Projects")
    assert ch2.selected_path == "Projects\\Jobs"
    assert any(r.id == "u1" and r.destination == "Projects\\Jobs" for r in ch2.config.rules)
    assert "Projects\\Jobs" in fd.advanced_rules_for(ch2.config, "Projects\\Jobs")[0].destination


def test_move_refuses_loops_and_name_clashes() -> None:
    cfg = fd.add_folder(_cfg(), "A").config
    cfg = fd.add_folder(cfg, "B", "A").config
    cfg = fd.add_folder(cfg, "B").config
    assert "inside itself" in fd.why_cannot_move(cfg, "A", "A\\B")
    assert "already has" in fd.why_cannot_move(cfg, "A\\B", "")
    assert "A" not in fd.valid_move_targets(cfg, "A")
    with pytest.raises(fd.DesignerError):
        fd.move_folder(cfg, "A", "A\\B")


def test_other_rules_show_as_extra_lines_and_repeats_are_dropped() -> None:
    cfg = fd.add_folder(_cfg(), "Taxes").config
    cfg.rules.insert(0, Rule(id="u2", name="Tax", destination="Taxes", keywords=["w2", "1099"]))
    # A separate rule is shown on its own line, not mixed into the sentence.
    assert fd.folder_cues(cfg, "Taxes").is_empty
    assert [r.id for r in fd.extra_rules_for(cfg, "Taxes")] == ["u2"]
    assert not fd.is_manual(cfg, "Taxes")
    removed = fd.remove_rule(cfg, "u2")
    assert fd.is_manual(removed.config, "Taxes")

    # A rule that only repeats the sentence is dropped when the sentence is edited.
    cfg2 = fd.set_cues(cfg, "Taxes", fd.FolderCues(keywords=["w2", "1099"])).config
    assert fd.extra_rules_for(cfg2, "Taxes") == []
    ch = fd.set_cues(cfg2, "Taxes", fd.FolderCues(keywords=["w2"]))
    assert not any(r.id == "u2" for r in ch.config.rules)
    assert _dest(ch.config, "my_1099.pdf") != "Taxes"
    assert _dest(ch.config, "my_w2.pdf") == "Taxes"


def test_teach_learns_words_without_changing_the_folder_sentence() -> None:
    cfg = fd.add_folder(_cfg(), "Receipts").config
    ch = fd.teach(cfg, "Receipts", "amazon_receipt_0412.pdf")
    assert _dest(ch.config, "amazon_receipt_0999.pdf") == "Receipts"
    assert fd.folder_cues(ch.config, "Receipts").is_empty
    assert len(fd.extra_rules_for(ch.config, "Receipts")) == 1
    assert "Learned" in ch.message


def test_merge_and_remove() -> None:
    cfg = fd.add_folder(_cfg(), "Old").config
    cfg = fd.set_cues(cfg, "Old", fd.FolderCues(keywords=["oldword"])).config
    cfg = fd.add_folder(cfg, "New").config
    ch = fd.merge_folder(cfg, "Old", "New")
    assert ch.disk_moves == [("Old", "New")]
    assert fd.find_node(fd.working_architecture(ch.config), "Old") is None
    assert _dest(ch.config, "oldword.txt") == "New"

    summary = fd.removal_summary(ch.config, "New")
    assert summary["name"] == "New"
    removed = fd.remove_folder(ch.config, "New")
    assert removed.disk_removals == [("New", None)]
    assert fd.find_node(fd.working_architecture(removed.config), "New") is None
    assert _dest(removed.config, "oldword.txt") != "New"


def test_describe_and_type_names() -> None:
    docs_exts = list(dict(fd.TYPE_CHOICES)["Documents"])
    names, extra = fd.type_names_for([*docs_exts, ".stl"])
    assert names == ["Documents"] and extra == [".stl"]
    text = fd.describe_cues(fd.FolderCues(extensions=[".pdf"], keywords=["tax"]))
    assert ".pdf" in text and "tax" in text
    assert fd.describe_cues(fd.FolderCues()) == "Only files you put here yourself"
