"""main.py starts a command once. If the command fails, it is not started again."""

from __future__ import annotations

import pytest

import main

# Command line flag, and the module whose main() runs that command.
COMMANDS = [
    ("--home", "rules_manager"),
    ("--welcome", "rules_manager"),
    ("--sort", "sort_once"),
    ("--undo", "sort_undo"),
    ("--ribbon", "ribbon_popup"),
    ("--ribbon-summary", "ribbon_popup"),
    ("--consolidate", "consolidate"),
]


@pytest.mark.parametrize(("flag", "module_name"), COMMANDS)
def test_a_command_that_fails_with_type_error_is_not_started_again(monkeypatch, flag, module_name):
    # main.py used to answer a TypeError from the command by calling its main()
    # again without arguments. That ran the whole command a second time (for
    # --undo, undoing another batch) and hid the first error.
    module = pytest.importorskip(module_name)  # rules_manager needs PySide6
    calls: list = []

    def failing_main(argv=None):
        calls.append(argv)
        raise TypeError("a bug inside the command")

    monkeypatch.setattr(module, "main", failing_main)

    with pytest.raises(TypeError, match="a bug inside the command"):
        main.main([flag])

    assert len(calls) == 1, f"{flag}: main.py started the command {len(calls)} times"
