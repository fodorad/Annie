"""Server-side folder/file pickers for the Dataset tab.

Annie runs locally, so the browser and the filesystem are the same machine: the
user can navigate the real directory tree and pick a folder (or a file) instead
of typing its path. Thin NiceGUI views over the pure helpers in
:mod:`annie.fsbrowse`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from annie.core import theme
from annie.pages.fsbrowse import list_files, list_subdirectories, parent_of, resolve_start_dir

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


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
    """Shared picker dialog; lists files too when ``files`` is set."""
    state: dict[str, Path] = {"path": resolve_start_dir(start)}

    with ui.dialog() as dialog, ui.card().classes("w-[34rem] max-w-full"):
        ui.label("Select a file" if files else "Select a folder").classes("text-lg font-medium")
        path_label = ui.label().classes("text-xs break-all").style(f"color:{theme.NEUTRAL}")
        listing = ui.column().classes("w-full gap-0 max-h-72 overflow-auto")

        def navigate(target: Path) -> None:
            state["path"] = target
            refresh()

        def refresh() -> None:
            path_label.set_text(str(state["path"]))
            listing.clear()
            with listing:
                parent = parent_of(state["path"])
                if parent is not None:
                    _entry_row("arrow_upward", "..", lambda: navigate(parent), muted=True)
                for child in list_subdirectories(state["path"]):
                    _entry_row(
                        "folder", child.name, lambda c=child: navigate(c), color=theme.PRIMARY
                    )
                if files:
                    for child in list_files(state["path"]):
                        _entry_row(
                            "description",
                            child.name,
                            lambda c=child: dialog.submit(str(c)),
                        )

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat")
            if not files:
                ui.button(
                    "Select this folder",
                    icon="check",
                    on_click=lambda: dialog.submit(str(state["path"])),
                ).props("unelevated")

        refresh()

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
