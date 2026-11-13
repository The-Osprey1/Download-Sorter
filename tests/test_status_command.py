"""The --status command runs from start to end (it once failed on its first line)."""

from __future__ import annotations

import status


def test_status_main_runs_and_prints_the_leftovers_line(capsys, tmp_path, monkeypatch):
    import sorter

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {**(cfg.get("watch") or {}), "folder": str(downloads)}
    monkeypatch.setattr(status, "load_config", lambda: cfg)

    assert status.main() == 0

    out = capsys.readouterr().out
    assert "Leftovers kept:" in out
