"""Version of the ``download_sorter`` package.

The project-wide source of truth is the top-level ``about.py`` (packaging metadata,
installer and build script read it). This module carries the same number for the
package itself; ``verify_release.py`` fails the release if the two ever differ.
"""

from __future__ import annotations

__version__ = "3.5.1"
