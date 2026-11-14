"""Explicit application paths container (no hidden module-level state)."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _local_app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Local"


@dataclass(frozen=True)
class AppPaths:
    """All storage locations for a Download Sorter v3 instance."""

    data_dir: Path
    downloads_dir: Path | None = None

    @classmethod
    def default(cls) -> AppPaths:
        return cls(data_dir=_local_app_data() / "download-sorter-v3")

    @staticmethod
    def v2_config_file() -> Path:
        return _local_app_data() / "download-sorter" / "config.json"

    @property
    def config_file(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def backup_config_file(self) -> Path:
        return self.data_dir / "config.json.bak"

    @property
    def db_file(self) -> Path:
        return self.data_dir / "state.db"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "sorter_v3.log"

    @property
    def pause_file(self) -> Path:
        return self.data_dir / "paused"

    @property
    def recent_state_file(self) -> Path:
        return self.data_dir / "recent_files.json"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
