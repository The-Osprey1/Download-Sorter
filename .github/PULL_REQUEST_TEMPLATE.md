## What changed

<!-- Plain-language summary of what this PR changes and why. Link any related issue (e.g. Fixes #123). -->

## Checks

- [ ] `python -m pytest tests_v3 -v` passes
- [ ] `python -m ruff check .` passes with 0 errors
- [ ] Moves remain reversible via `state.db` Undo, and any cleanup uses the Windows Recycle Bin (never permanent file deletion)
- [ ] Local-first preserved: no telemetry or new network calls added
- [ ] Disk / file I/O in UI paths runs off the Qt main thread (`ui/workers.py`)
