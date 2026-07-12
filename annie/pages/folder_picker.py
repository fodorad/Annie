"""Server-side folder/file pickers for the Dataset tab.

Annie runs locally, so the browser and the filesystem are the same machine: the
user can navigate the real directory tree and pick a folder (or a file) instead
of typing its path. Thin NiceGUI views over the pure helpers in
:mod:`annie.fsbrowse`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import background_tasks, run, ui

from annie.core import theme
from annie.pages.fsbrowse import parent_of, resolve_start_dir, scan_entries
from annie.pages.utils import _alive

if TYPE_CHECKING:
    from collections.abc import Callable

#: Session memory of where the next picker should open: the parent of the directory
#: (or file) most recently *selected*, so the first pick lands at home and later ones
#: land beside the siblings of what you chose before (video ▸ vdet ▸ track live
#: together). Only a selection updates this — plain browsing does not.
_MEMORY: dict[str, Path | None] = {"default_dir": None}


def _remember(selected: Path) -> None:
    """Record the parent of a just-selected path as the next picker's default."""
    _MEMORY["default_dir"] = selected.parent


def _initial_dir(start: str | Path | None) -> Path:
    """Resolve where a picker opens: explicit hint ▸ last-selected parent ▸ home.

    Runs the ``stat``-ing off the event loop (see :func:`_pick`).
    """
    if start:
        return resolve_start_dir(start)
    remembered = _MEMORY["default_dir"]
    if remembered is not None and remembered.is_dir():
        return remembered
    return Path.home()


async def pick_directory(start: str | Path | None = None) -> str | None:
    """Open a modal folder picker and return the chosen directory path.

    Args:
        start: A path hint to open at. Falls back to the nearest existing ancestor
            or the home directory.

    Returns:
        The absolute path of the selected folder, or ``None`` if cancelled.
    """
    return await _pick(start, files=False)


async def pick_file(start: str | Path | None = None) -> str | None:
    """Open a modal file picker and return the chosen file path.

    Navigate folders as usual; clicking a file selects it and closes the dialog.

    Args:
        start: A path hint to open at (a file or its directory).

    Returns:
        The absolute path of the selected file, or ``None`` if cancelled.
    """
    return await _pick(start, files=True)


async def _pick(start: str | Path | None, *, files: bool) -> str | None:
    """Shared picker dialog; lists files too when ``files`` is set.

    The dialog opens immediately with a spinner; the directory listing — and even
    resolving the start directory — is read on a worker thread, so a slow or busy
    drive never blocks the dialog from appearing (see
    :func:`annie.pages.fsbrowse.scan_entries`).
    """
    state: dict[str, Path | None] = {"path": None}

    with ui.dialog() as dialog, ui.card().classes("w-[34rem] max-w-full"):
        ui.label("Select a file" if files else "Select a folder").classes("text-lg font-medium")
        path_label = (
            ui.label("Loading…").classes("text-xs break-all").style(f"color:{theme.NEUTRAL}")
        )
        listing = ui.column().classes("w-full gap-0 max-h-72 overflow-auto")

        def navigate(target: Path) -> None:
            background_tasks.create(load(target))

        async def load(target: Path | None) -> None:
            if not _alive(listing):
                return
            if select is not None:
                select.disable()  # can't submit a folder until this scan resolves a path
            listing.clear()
            with listing:
                ui.spinner(size="lg").classes("self-center my-4")
            # Resolve the start dir off-thread too — it stats the hint and its ancestors.
            if target is not None:
                path = target
            else:
                path = await run.io_bound(_initial_dir, start)
                if path is None or not _alive(listing):
                    return  # app shutting down, or the dialog was closed mid-resolve
            scanned = await run.io_bound(scan_entries, path, want_files=files)
            if scanned is None or not _alive(listing):
                return  # app shutting down, or the dialog was closed mid-scan
            state["path"] = path
            subdirs, file_children = scanned
            path_label.set_text(str(path))
            listing.clear()
            with listing:
                parent = parent_of(path)
                if parent is not None:
                    _entry_row("arrow_upward", "..", lambda: navigate(parent), muted=True)
                for child in subdirs:
                    _entry_row(
                        "folder", child.name, lambda c=child: navigate(c), color=theme.PRIMARY
                    )
                for child in file_children:
                    _entry_row("description", child.name, lambda c=child: choose_file(c))
            if select is not None and _alive(select):
                select.enable()

        def choose_dir() -> None:
            chosen = state["path"]
            if chosen is not None:
                _remember(chosen)
                dialog.submit(str(chosen))

        def choose_file(chosen: Path) -> None:
            _remember(chosen)
            dialog.submit(str(chosen))

        select: ui.button | None = None
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat")
            if not files:
                select = ui.button("Select this folder", icon="check", on_click=choose_dir).props(
                    "unelevated"
                )
                select.disable()  # enabled once the initial listing resolves

    background_tasks.create(load(None))
    return await dialog


def _entry_row(
    icon: str,
    label: str,
    on_click: Callable[[], None],
    *,
    color: str | None = None,
    muted: bool = False,
):  # noqa: ANN202
    """Render one clickable directory/file row inside the picker listing."""
    row = ui.row().classes("w-full items-center gap-2 cursor-pointer p-1 rounded hover:bg-gray-200")
    row.on("click", lambda _: on_click())
    with row:
        ui.icon(icon, color=color or theme.NEUTRAL)
        text = ui.label(label).classes("text-sm")
        if muted:
            text.style(f"color:{theme.NEUTRAL}")
    return row
