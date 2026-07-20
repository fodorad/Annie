"""CSV configuration dialogs (UI).

Two modals, both opened after the user has picked a CSV file:

* :func:`configure_csv` (Dataset tab) — describe how the file joins to the dataset:
  its **role** (label tags vs the protagonist track), the **key column** that matches
  the video id (auto-suggested), and the **value columns** to surface. It returns a
  fully-formed :class:`~annie.sources.DataSource`.
* :func:`select_id_column` (Browse tab) — pick the column holding the video ids to
  restrict the Browse list to.

Both return ``None`` if cancelled.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import ui

from annie.core import theme
from annie.dataset.manipulate import detect_type
from annie.dataset.sources import CsvRole, DataSource, SegmentationBand, SourceKind
from annie.parsers.csvmeta import (
    distinct_column_values,
    read_header,
    read_rows,
    suggest_key_column,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Column value types offered in the CSV configuration dialog.
_TYPES = ("str", "int", "float")


def _guess_column(header: list[str], needle: str) -> str | None:
    """Return the first header column whose name contains ``needle`` (case-insensitive)."""
    return next((col for col in header if needle in col.lower()), None)


class _BandsEditor:
    """A small dynamic editor for the segmentation start/end bands.

    Each band is a name plus a start and end column; the user may add or remove
    rows. One row is seeded from any ``*start*``/``*end*`` columns in the header.
    """

    def __init__(self, header: list[str]) -> None:
        self._header = header
        self._rows: list[tuple[ui.input, ui.select, ui.select]] = []
        with ui.column().classes("w-full gap-1") as container:
            ui.label("Segmentation bands (name · start · end)").classes("text-sm").style(
                f"color:{theme.NEUTRAL}"
            )
            self._list = ui.column().classes("w-full gap-1")
            ui.button("Add band", icon="add", on_click=self.add_row).props("flat dense")
        self.container = container
        start = _guess_column(header, "start")
        end = _guess_column(header, "end")
        self.add_row(name="GT", start=start, end=end)

    def add_row(self, *, name: str = "", start: str | None = None, end: str | None = None) -> None:
        """Append one band row, pre-filling name/start/end when given."""
        with self._list, ui.row().classes("w-full items-center gap-2 no-wrap") as row:
            name_in = ui.input(placeholder="name", value=name).props("dense").classes("w-24")
            start_sel = (
                ui.select(self._header, value=start, label="start")
                .props("dense")
                .classes("flex-grow")
            )
            end_sel = (
                ui.select(self._header, value=end, label="end").props("dense").classes("flex-grow")
            )
            entry = (name_in, start_sel, end_sel)
            self._rows.append(entry)

            def _remove() -> None:
                self._rows.remove(entry)
                row.delete()

            ui.button(icon="close", on_click=_remove).props("flat dense round")

    def collect(self) -> tuple[SegmentationBand, ...]:
        """Return the fully-specified bands (rows missing start or end are skipped)."""
        bands: list[SegmentationBand] = []
        for i, (name_in, start_sel, end_sel) in enumerate(self._rows):
            if not start_sel.value or not end_sel.value:
                continue
            label = name_in.value.strip() or f"band {i + 1}"
            bands.append(SegmentationBand(label, start_sel.value, end_sel.value))
        return tuple(bands)


def _bands_editor(header: list[str]) -> _BandsEditor:
    """Build the segmentation bands editor for ``header``."""
    return _BandsEditor(header)


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
            {
                CsvRole.LABELS: "Labels (tags & filters)",
                CsvRole.PROTAGONIST: "Protagonist",
                CsvRole.SEGMENTATION: "Segmentation (accept/drop clips)",
            },
            value=CsvRole.LABELS,
            label="Use as",
        ).classes("w-full")

        key = ui.select(header, value=suggested, label="Key column (joins to video id)").classes(
            "w-full"
        )

        segment_col = (
            ui.select(
                header,
                value=_guess_column(header, "segment"),
                label="Row id column (makes each clip's decision distinct)",
            )
            .classes("w-full")
            .tooltip(
                "Several rows share one video id, so this is what tells them apart: each "
                "clip's accept/drop is saved under {video_id}_{row id}. Pick the column "
                "that is unique within a video, e.g. segment_id."
            )
        )
        bands = _bands_editor(header)

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
            is_protagonist = role.value == CsvRole.PROTAGONIST
            is_segmentation = role.value == CsvRole.SEGMENTATION
            track_col.set_visibility(is_protagonist)
            # value columns double as label tags for both labels and segmentation.
            checkbox_box.set_visibility(not is_protagonist)
            segment_col.set_visibility(is_segmentation)
            bands.container.set_visibility(is_segmentation)

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
            elif role.value == CsvRole.SEGMENTATION:
                if not segment_col.value:
                    ui.notify("Pick the row id column.", color=theme.WARNING)
                    return
                chosen_bands = bands.collect()
                if not chosen_bands:
                    ui.notify("Add at least one start/end band.", color=theme.WARNING)
                    return
                selected = tuple(col for col, box in checks.items() if box.value)
                source = DataSource(
                    SourceKind.CSV,
                    csv_path,
                    role=CsvRole.SEGMENTATION,
                    key_column=key.value,
                    segment_column=segment_col.value,
                    bands=chosen_bands,
                    value_columns=selected,
                    column_types={col: types[col].value for col in selected},
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


async def select_id_column(
    path: str | Path, video_stems: Iterable[str]
) -> tuple[str, list[str]] | None:
    """Open the id-column modal for a CSV that supplies a Browse id filter.

    The user picks which column holds the video ids; the column is auto-suggested by
    matching its values against the known video stems, and the dialog reports how many
    of the ids actually exist in the dataset before the filter is applied.

    Args:
        path: The chosen CSV file.
        video_stems: Known video stems, used to auto-suggest the id column and to
            report the overlap.

    Returns:
        The ``(column, ids)`` pair — the ids distinct and in file order — or ``None``
        if cancelled or if the CSV has no header.
    """
    csv_path = Path(path)
    header = read_header(csv_path)
    if not header:
        ui.notify("That CSV has no header row.", color=theme.DANGER)
        return None

    rows = read_rows(csv_path)
    stems = set(video_stems)
    suggested = suggest_key_column(csv_path, stems) or header[0]

    with ui.dialog() as dialog, ui.card().classes("w-[30rem] max-w-full gap-2"):
        ui.label(f"Filter by ids — {csv_path.name}").classes("text-lg font-medium")
        ui.label("Browse will list only the videos whose id appears in this column.").classes(
            "text-sm"
        ).style(f"color:{theme.NEUTRAL}")

        column = ui.select(header, value=suggested, label="Id column").classes("w-full")
        summary = ui.label().classes("text-sm")

        def _ids() -> list[str]:
            return distinct_column_values(rows, column.value) if column.value else []

        def _summarise() -> None:
            ids = _ids()
            matched = sum(1 for value in ids if value in stems)
            summary.set_text(f"{len(ids)} unique ids · {matched} match a video in the dataset")
            summary.style(f"color:{theme.WARNING if not ids else theme.NEUTRAL}")

        column.on_value_change(lambda _: _summarise())
        _summarise()

        def _confirm() -> None:
            ids = _ids()
            if not ids:
                ui.notify("That column has no ids in it.", color=theme.WARNING)
                return
            dialog.submit((column.value, ids))

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat")
            ui.button("Apply filter", icon="filter_alt", on_click=_confirm).props("unelevated")

    return await dialog
