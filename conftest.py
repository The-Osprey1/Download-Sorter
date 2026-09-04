"""Keep the complete test suite isolated from the user's live sorter state."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def isolate_sorter_state(tmp_path, monkeypatch):
    import sorter
    import watch

    state_dir = tmp_path / "sorter-state"
    paths = {
        "LOG_DIR": state_dir,
        "LOG_FILE": state_dir / "sorter.log",
        "HISTORY_FILE": state_dir / "history.jsonl",
        "PAUSE_FILE": state_dir / "paused",
        "FAILURE_FILE": state_dir / "failures.json",
        "ACTIVE_PROJECT_FILE": state_dir / "active_project.json",
        "RUNTIME_STATE_FILE": state_dir / "runtime_state.json",
        "SERVICE_DISABLED_FILE": state_dir / "service_disabled",
    }
    for name, path in paths.items():
        monkeypatch.setattr(sorter, name, path)

    sorter._failure_cache = None
    with watch.QUEUE_LOCK:
        watch.PENDING_DOWNLOADS.clear()
        watch.PENDING_DOCUMENTS.clear()
        watch.PENDING_MISPLACED.clear()
        watch.PENDING_DEADLINES.clear()
    watch.WAKE_EVENT.clear()
    for module_name in ("watch", "tray", "status", "sort_undo"):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for name, path in paths.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, path)
    yield
    sorter._failure_cache = None
    with watch.QUEUE_LOCK:
        watch.PENDING_DOWNLOADS.clear()
        watch.PENDING_DOCUMENTS.clear()
        watch.PENDING_MISPLACED.clear()
        watch.PENDING_DEADLINES.clear()
    watch.WAKE_EVENT.clear()
