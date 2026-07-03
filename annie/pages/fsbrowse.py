"""Filesystem browsing helpers for the folder-picker dialog (service).

Pure, side-effect-free directory navigation used by the Dataset tab's folder
picker. Kept out of the UI layer so it can be unit-tested without NiceGUI: the
picker dialog (``annie/pages/folder_picker.py``) is a thin view over these.
"""

from __future__ import annotations

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
    base = Path(path)
    try:
        entries = [
            child
            for child in base.iterdir()
            if child.is_dir() and (show_hidden or not child.name.startswith("."))
        ]
    except (PermissionError, OSError):
        return []
    return sorted(entries, key=lambda child: child.name.lower())


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
    base = Path(path)
    try:
        entries = [
            child
            for child in base.iterdir()
            if child.is_file()
            and (show_hidden or not child.name.startswith("."))
            and (suffixes is None or child.suffix.lower() in suffixes)
        ]
    except (PermissionError, OSError):
        return []
    return sorted(entries, key=lambda child: child.name.lower())
