"""Lazy file scanner for @ mention autocomplete.

Scans the project directory, excluding common large directories,
and returns paths for autocomplete suggestions.
"""
from __future__ import annotations

import os
from pathlib import Path

# Directories to always exclude from file scanning
EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".eggs", "*.egg-info", ".ruff_cache", "htmlcov",
})

# File extensions to include (None = all files)
INCLUDE_EXTENSIONS: frozenset[str] | None = None


def _should_exclude(dir_name: str) -> bool:
    """Check if directory name should be excluded."""
    if dir_name in EXCLUDE_DIRS:
        return True
    # Handle glob-like patterns (e.g., *.egg-info)
    if dir_name.endswith(".egg-info"):
        return True
    return False


def _should_include(file_path: Path) -> bool:
    """Check if file should be included in results."""
    if INCLUDE_EXTENSIONS is None:
        return True
    return file_path.suffix in INCLUDE_EXTENSIONS


def scan_files(
    root: Path | str,
    max_depth: int = 5,
    limit: int = 500,
) -> list[str]:
    if isinstance(root, str):
        root = Path(root)
    if not root.exists() or not root.is_dir():
        return []

    results: list[str] = []

    def _scan(current: Path, depth: int) -> None:
        if depth > max_depth or len(results) >= limit:
            return

        try:
            entries = sorted(current.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return

        for entry in entries:
            if len(results) >= limit:
                return

            # Skip excluded directories
            if entry.is_dir():
                if _should_exclude(entry.name):
                    continue
                _scan(entry, depth + 1)
                continue

            # Include files
            if entry.is_file() and _should_include(entry):
                try:
                    rel = entry.relative_to(root)
                    results.append(str(rel))
                except ValueError:
                    continue

    _scan(root, 0)
    return results


class LazyFileIndex:
    """Lazy-loaded file index with caching.

    Scans the project directory once, caches results,
    and provides autocomplete suggestions.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root) if isinstance(root, str) else root
        self._files: list[str] | None = None
        self._mtime: float = 0.0

    def _needs_refresh(self) -> bool:
        """Check if cache needs refresh based on directory mtime."""
        try:
            current_mtime = os.path.getmtime(self._root)
            if self._files is None or current_mtime > self._mtime:
                self._mtime = current_mtime
                return True
        except OSError:
            pass
        return self._files is None

    def get_files(self) -> list[str]:
        """Get cached file list, refreshing if needed."""
        if self._needs_refresh():
            self._files = scan_files(self._root)
        return self._files or []

    def search(self, prefix: str) -> list[str]:
        """Search files by prefix (case-insensitive).

        Args:
            prefix: Prefix to match (e.g., "src/main" matches "src/main.py")

        Returns:
            List of matching file paths
        """
        prefix_lower = prefix.lower()
        return [
            f for f in self.get_files()
            if prefix_lower in f.lower()
        ]
