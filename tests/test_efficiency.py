from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import sorter
import watch


def _record(name: str, *, action: str | None = None) -> str:
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "src": f"C:/Downloads/{name}",
        "dest": f"C:/Downloads/Other/{name}",
    }
    if action:
        data["action"] = action
    return json.dumps(data) + "\n"


def test_incremental_history_reads_appends_and_resets_after_truncate(tmp_path):
    history = tmp_path / "history.jsonl"
    history.write_text(_record("one.stl") + "not-json\n", encoding="utf-8")
    reader = sorter.IncrementalHistoryReader(history)

    assert reader.moves_today() == 1
    with history.open("a", encoding="utf-8") as handle:
        handle.write(_record("two.stl"))
        handle.write(_record("one.stl", action="undo"))
    assert reader.moves_today() == 2
    assert reader.moves_today() == 2

    history.write_text(_record("replacement.stl"), encoding="utf-8")
    assert reader.moves_today() == 1


def test_incremental_history_waits_for_complete_jsonl_record(tmp_path):
    history = tmp_path / "history.jsonl"
    line = _record("partial.stl")
    history.write_text(line[:-1], encoding="utf-8")
    reader = sorter.IncrementalHistoryReader(history)
    assert reader.moves_today() == 0
    with history.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    assert reader.moves_today() == 1


def test_documents_scan_prunes_non_relocatable_extensions(tmp_path):
    (tmp_path / "model.step").write_text("cad")
    (tmp_path / "notes.txt").write_text("notes")
    config = sorter.get_default_config()
    context = sorter.compile_routing(config)

    assert [path.name for path in sorter.iter_files(tmp_path, config, context)] == ["model.step"]


def test_download_events_debounce_without_classifying_early(tmp_path, monkeypatch):
    config = sorter.get_default_config()
    config["watch"]["folder"] = str(tmp_path)
    config["watch"]["settle_seconds"] = 1.5
    category = tmp_path / "Images"
    category.mkdir()
    target = category / "large.gcode"
    target.write_text("G1 X0")
    handler = watch.DownloadsHandler(tmp_path, config)
    monkeypatch.setattr(
        sorter,
        "evaluate_routing",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
    )

    with watch.QUEUE_LOCK:
        watch.PENDING_MISPLACED.clear()
        watch.PENDING_DEADLINES.clear()
    handler._queue(str(target))
    first_deadline = watch.PENDING_DEADLINES[("misplaced", target)]
    time.sleep(0.01)
    handler._queue(str(target))

    assert watch.PENDING_MISPLACED == {target}
    assert watch.PENDING_DEADLINES[("misplaced", target)] > first_deadline


def test_coordinator_wakes_immediately_and_has_bounded_fallback():
    watch.WAKE_EVENT.clear()
    coordinator = watch.WatchCoordinator(fallback_seconds=0.03)
    started = time.monotonic()
    coordinator.wake()
    assert coordinator.wait() is False
    assert time.monotonic() - started < 0.02
    assert coordinator.wait() is True


def test_compiled_context_avoids_active_project_disk_read(monkeypatch):
    config = sorter.get_default_config()
    context = sorter.compile_routing(config, active_project=None)
    monkeypatch.setattr(sorter, "load_active_project", lambda *_args: (_ for _ in ()).throw(AssertionError()))

    result = sorter.evaluate_routing("part.stl", config, routing_context=context)

    assert result["destination"] == "3D Models\\STL"


def test_config_deletion_keeps_last_valid_configuration(tmp_path):
    config_path = tmp_path / "config.json"
    active_path = tmp_path / "active.json"
    config = sorter.get_default_config()
    config_path.write_text(json.dumps(config), encoding="utf-8")
    reloader = watch.ConfigReloader(config_path, active_path)
    config_path.unlink()
    reloader.mark_dirty(config_path)

    changed, retained = reloader.check_for_updates()

    assert changed is False
    assert retained == config


def test_event_driven_watcher_moves_new_file_and_stops_promptly(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    config_path = tmp_path / "config.json"
    active_path = tmp_path / "state" / "active_project.json"
    config = sorter.get_default_config()
    config["watch"]["folder"] = str(downloads)
    config["watch"]["settle_seconds"] = 0.05
    config["document_sources"] = []
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(watch, "CONFIG_PATH", config_path)
    monkeypatch.setattr(watch, "ACTIVE_PROJECT_FILE", active_path)

    stop = threading.Event()
    thread = threading.Thread(target=watch.run, args=(stop,), daemon=True)
    thread.start()
    time.sleep(0.15)
    incoming = downloads / "new-report.pdf"
    incoming.write_text("report", encoding="utf-8")
    destination = downloads / "Documents" / "PDFs" / incoming.name

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not destination.exists():
        time.sleep(0.02)
    stop.set()
    watch.WAKE_EVENT.set()
    thread.join(timeout=2)

    assert destination.exists()
    assert not thread.is_alive()


def test_history_cleanup_backs_up_and_removes_only_pytest_records(tmp_path):
    from clean_test_history import clean_history

    history = tmp_path / "history.jsonl"
    real = json.loads(_record("real.stl"))
    test_record = dict(real)
    test_record["src"] = str(tmp_path / "Temp" / "pytest-of-devyn" / "case" / "fake.stl")
    history.write_text(json.dumps(real) + "\n" + json.dumps(test_record) + "\n", encoding="utf-8")

    removed, malformed, backup = clean_history(history, apply=True)

    assert (removed, malformed) == (1, 0)
    assert backup is not None and backup.exists()
    assert [json.loads(line)["src"] for line in history.read_text(encoding="utf-8").splitlines()] == [real["src"]]


def test_runtime_state_is_atomic_and_preserves_process_start_time():
    sorter.write_runtime_state("starting", "initializing")
    first = json.loads(sorter.RUNTIME_STATE_FILE.read_text(encoding="utf-8"))
    time.sleep(0.01)
    sorter.write_runtime_state("running", "heartbeat")
    second = json.loads(sorter.RUNTIME_STATE_FILE.read_text(encoding="utf-8"))

    assert second["status"] == "running"
    assert second["detail"] == "heartbeat"
    assert second["started_at"] == first["started_at"]
    assert second["updated_at"] >= first["updated_at"]
