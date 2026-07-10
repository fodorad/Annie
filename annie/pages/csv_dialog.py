"""CSV source configuration dialog (UI).

After the user picks a CSV file, this modal lets them describe how it joins to the
dataset: its **role** (label tags vs the protagonist track), the **key column**
that matches the video id (auto-suggested), and the **value columns** to surface.
It returns a fully-formed :class:`~annie.sources.DataSource`, or ``None`` if
cancelled.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import ui

from annie.core import theme
from annie.dataset.manipulate import detect_type
from annie.dataset.sources import CsvRole, DataSource, SourceKind
from annie.parsers.csvmeta import read_header, read_rows, suggest_key_column

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Column value types offered in the CSV configuration dialog.
_TYPES = ("str", "int", "float")


async def configure_csv(path: str | Path, video_stems: Iterable[str]) -> DataSource | None:
    """Open the CSV configuration modal and return the resulting source.

    Args:
        path: The chosen CSV file.
        video_stems: Known video stems, used to auto-suggest the key column.

    Returns:
        A configured :class:`~annie.sources.DataSource`, or ``None`` if cancelled.
    """
    csv_path = Path(path)
    header = read_header(csv_path)
    stems = set(video_stems)
    suggested = suggest_key_column(csv_path, stems) or (header[0] if header else None)

    if not header:
        ui.notify("That CSV has no header row.", color=theme.DANGER)
        return None

    sample = read_rows(csv_path)[:500]
    detected = {col: detect_type((row.get(col) or "") for row in sample) for col in header}

    with ui.dialog() as dialog, ui.card().classes("w-[34rem] max-w-full gap-2"):
        ui.label(f"Add CSV — {csv_path.name}").classes("text-lg font-medium")

        role = ui.select(
            {CsvRole.LABELS: "Labels (tags & filters)", CsvRole.PROTAGONIST: "Protagonist"},
            value=CsvRole.LABELS,
            label="Use as",
        ).classes("w-full")

        key = ui.select(header, value=suggested, label="Key column (joins to video id)").classes(
            "w-full"
        )

        with ui.row().classes("w-full items-center justify-between mt-1"):
            ui.label("Value columns").classes("text-sm").style(f"color:{theme.NEUTRAL}")
            with ui.row().classes("gap-1"):
                ui.button("Select all", on_click=lambda: _set_all(True)).props("flat dense")
                ui.button("Clear", on_click=lambda: _set_all(False)).props("flat dense")

        checks: dict[str, ui.checkbox] = {}
        types: dict[str, ui.select] = {}
        track_col = ui.select(header, value=None, label="Track-id column").classes("w-full")

        def _set_all(value: bool) -> None:
            for box in checks.values():
                box.set_value(value)

        @ui.refreshable
        def _value_controls() -> None:
            if role.value == CsvRole.PROTAGONIST:
                track_col.set_visibility(True)
                checkbox_box.set_visibility(False)
            else:
                track_col.set_visibility(False)
                checkbox_box.set_visibility(True)

        with ui.column().classes("w-full gap-0 max-h-72 overflow-auto") as checkbox_box:
            for col in header:
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    checks[col] = ui.checkbox(col, value=False).classes("flex-grow")
                    types[col] = (
                        ui.select(list(_TYPES), value=detected[col])
                        .props("dense outlined")
                        .classes("w-28")
                    )

        role.on_value_change(lambda _: _value_controls.refresh())
        _value_controls()

        def _confirm() -> None:
            if role.value == CsvRole.PROTAGONIST:
                if not track_col.value:
                    ui.notify("Pick the track-id column.", color=theme.WARNING)
                    return
                source = DataSource(
                    SourceKind.CSV,
                    csv_path,
                    role=CsvRole.PROTAGONIST,
                    key_column=key.value,
                    value_columns=(track_col.value,),
                )
            else:
                selected = tuple(col for col, box in checks.items() if box.value)
                if not selected:
                    ui.notify("Select at least one value column.", color=theme.WARNING)
                    return
                source = DataSource(
                    SourceKind.CSV,
                    csv_path,
                    role=CsvRole.LABELS,
                    key_column=key.value,
                    value_columns=selected,
                    column_types={col: types[col].value for col in selected},
                )
            dialog.submit(source)

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat")
            ui.button("Add source", icon="add", on_click=_confirm).props("unelevated")

    return await dialog
