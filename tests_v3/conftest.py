"""Isolated fixtures for Download Sorter v3 tests."""

from __future__ import annotations

from collections.abc import Callable
import gc
import os
from pathlib import Path
import sys
from typing import Any

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from download_sorter.paths import AppPaths
from tests_v3.mock_win32 import FakeWin32Desktop


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    data = tmp_path / "data"
    downloads = tmp_path / "Downloads"
    data.mkdir(parents=True, exist_ok=True)
    downloads.mkdir(parents=True, exist_ok=True)
    return AppPaths(data_dir=data, downloads_dir=downloads)


@pytest.fixture(scope="session")
def v3_qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    for w in list(app.topLevelWidgets()):
        try:
            w.close()
            w.deleteLater()
        except RuntimeError:
            pass
    app.processEvents()
    gc.collect()
    app.processEvents()
    app.quit()


@pytest.fixture(autouse=True)
def _cleanup_qt_widgets(v3_qapp):
    yield
    for w in list(v3_qapp.topLevelWidgets()):
        try:
            w.close()
            w.deleteLater()
        except RuntimeError:
            pass
    v3_qapp.processEvents()
    gc.collect()
    v3_qapp.processEvents()


@pytest.fixture(autouse=True)
def _no_live_slicer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must not depend on whether a slicer happens to be open on this machine.

    Tests that exercise the slicer hold patch ``download_sorter.service.is_slicer_running``
    themselves; ``fs_ops.is_slicer_running`` is left real so the Win32 helper is testable.
    """
    import download_sorter.service as service_module

    monkeypatch.setattr(service_module, "is_slicer_running", lambda *a, **k: False)


@pytest.fixture(autouse=True)
def _legacy_new_install_packs(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests were written when a new install started with the School, Career and
    3D printing rule packs, and lean on those rules. New installs now start with none;
    tests marked ``shipped_defaults`` check that real behaviour.
    """
    if request.node.get_closest_marker("shipped_defaults") is not None:
        return
    import download_sorter.config as config_module

    monkeypatch.setattr(config_module, "NEW_INSTALL_TEMPLATES", ("school", "career", "3d_printing"))


@pytest.fixture
def fake_desktop(monkeypatch: pytest.MonkeyPatch) -> FakeWin32Desktop:
    """Provides an isolated in-memory Win32 headless desktop harness for E2E tests."""
    desktop = FakeWin32Desktop()
    desktop.patch(monkeypatch)
    return desktop


@pytest.fixture
def create_test_file(app_paths: AppPaths) -> Callable[..., Path]:
    """Helper factory fixture to create test download files with configurable names and content."""
    def _create(
        name: str = "sample.pdf",
        content: bytes | str = b"sample content",
        folder: Path | None = None,
        mtime: float | None = None,
    ) -> Path:
        target_dir = folder or app_paths.downloads_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / name
        if isinstance(content, str):
            file_path.write_text(content, encoding="utf-8")
        else:
            file_path.write_bytes(content)
        if mtime is not None:
            os.utime(file_path, (mtime, mtime))
        return file_path

    return _create


@pytest.fixture
def service_instance(app_paths: AppPaths):
    """Provides a managed SorterService instance with guaranteed stop() teardown."""
    from download_sorter.service import SorterService

    svc = SorterService(paths=app_paths)
    yield svc
    try:
        svc.stop(timeout=1.0)
    except Exception:
        pass
