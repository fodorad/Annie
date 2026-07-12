"""Filesystem browsing helpers for the folder-picker dialog (service).

Pure, side-effect-free directory navigation used by the Dataset tab's folder
picker. Kept out of the UI layer so it can be unit-tested without NiceGUI: the
picker dialog (``annie/pages/folder_picker.py``) is a thin view over these.
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_start_dir(start: str | Path | None) -> Path:
    """Return a sensible existing directory to open the picker at.

    Falls back gracefully: the given path if it is a directory, else its nearest
    existing ancestor, else the user's home directory.

    Args:
        start: A path hint (e.g. the current text-field value), or ``None``.

    Returns:
        An existing directory to start browsing from.
    """
    if start:
        path = Path(start).expanduser()
        if path.is_dir():
            return path
        for ancestor in path.parents:
            if ancestor.is_dir():
                return ancestor
    return Path.home()


def parent_of(path: str | Path) -> Path | None:
    """Return the parent directory, or ``None`` if already at the filesystem root.

    Args:
        path: The current directory.

    Returns:
        The parent :class:`~pathlib.Path`, or ``None`` at the root.
    """
    resolved = Path(path)
    parent = resolved.parent
    return None if parent == resolved else parent


def scan_entries(
    path: str | Path,
    *,
    want_files: bool,
    suffixes: tuple[str, ...] | None = None,
    show_hidden: bool = False,
) -> tuple[list[Path], list[Path]]:
    """List a directory's subfolders (and optionally files) in a single pass.

    Uses :func:`os.scandir`, whose entries carry the file type read straight from the
    directory itself, so classifying each child as a folder or file costs no extra
    ``stat`` syscall on the common case. This matters on large or slow (external /
    networked) drives, where the old one-``stat``-per-entry approach could freeze the
    picker for many seconds. Unreadable directories yield empty lists rather than
    raising, so a permission error never breaks the picker.

    Args:
        path: The directory to list.
        want_files: Also collect files (skipped entirely when ``False``).
        suffixes: Lower-case dotted suffixes to keep among files, or ``None`` for all.
        show_hidden: Include dot-prefixed entries when ``True``.

    Returns:
        A ``(subdirs, files)`` pair, each sorted by lower-cased name; ``files`` is
        empty when ``want_files`` is ``False``.
    """
    subdirs: list[Path] = []
    files: list[Path] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                if not show_hidden and entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir():
                        subdirs.append(Path(entry.path))
                    elif want_files and entry.is_file():
                        child = Path(entry.path)
                        if suffixes is None or child.suffix.lower() in suffixes:
                            files.append(child)
                except OSError:
                    continue  # a single unreadable entry must not abort the listing
    except (PermissionError, OSError):
        return [], []
    subdirs.sort(key=lambda p: p.name.lower())
    files.sort(key=lambda p: p.name.lower())
    return subdirs, files


def list_subdirectories(path: str | Path, *, show_hidden: bool = False) -> list[Path]:
    """List immediate subdirectories of ``path``, sorted case-insensitively.

    Hidden entries (dotfiles, including macOS AppleDouble ``._*``) are excluded by
    default. Unreadable directories yield an empty list rather than raising, so a
    permission error never breaks the picker.

    Args:
        path: The directory to list.
        show_hidden: Include dot-prefixed entries when ``True``.

    Returns:
        The child directories, sorted by lower-cased name.
    """
    return scan_entries(path, want_files=False, show_hidden=show_hidden)[0]


def list_files(
    path: str | Path, *, suffixes: tuple[str, ...] | None = None, show_hidden: bool = False
) -> list[Path]:
    """List immediate files of ``path``, sorted case-insensitively.

    Hidden entries are excluded by default; an optional suffix filter restricts the
    result (e.g. ``(".csv",)``). Unreadable directories yield an empty list.

    Args:
        path: The directory to list.
        suffixes: Lower-case dotted suffixes to keep, or ``None`` for all files.
        show_hidden: Include dot-prefixed entries when ``True``.

    Returns:
        The child files, sorted by lower-cased name.
    """
    return scan_entries(path, want_files=True, suffixes=suffixes, show_hidden=show_hidden)[1]
