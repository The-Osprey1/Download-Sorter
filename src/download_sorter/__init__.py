"""Download Sorter v3.0 — deterministic, single-process top-level file sorter."""

from __future__ import annotations

from download_sorter.about import __version__
from download_sorter.config import Config, Rule, STARTER_TEMPLATES, import_v2_config, load_config, save_config
from download_sorter.db import StateDB
from download_sorter.paths import AppPaths
from download_sorter.quick_switcher import (
    QuickSwitcherMonitor,
    get_recent_file,
    inject_file_into_dialog,
    is_file_dialog,
    record_recent_file,
)
from download_sorter.router import FileCandidate, RouteDecision, RouteStep, evaluate

__all__ = [
    "__version__",
    "AppPaths",
    "Config",
    "FileCandidate",
    "QuickSwitcherMonitor",
    "RouteDecision",
    "RouteStep",
    "Rule",
    "STARTER_TEMPLATES",
    "StateDB",
    "evaluate",
    "get_recent_file",
    "import_v2_config",
    "inject_file_into_dialog",
    "is_file_dialog",
    "load_config",
    "record_recent_file",
    "save_config",
]
