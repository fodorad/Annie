"""Table configuration dialogs: CSV files and Excel workbooks (UI).

Opened after the user has picked a file:

* :func:`configure_table` (Dataset tab) — describe how a CSV or workbook joins to the
  dataset. A CSV picks a **role** (label tags, protagonist track, segmentation); a
  workbook lists every sheet, and each included sheet becomes its own label source.
  For label tables a :class:`_JoinEditor` chooses the **ID column** and what it
  **matches** (the whole video id or a nested-layout path field such as
  ``{subject}``), optionally by substring, resolves repeated IDs with an explicit
  **order column**, and names each feature column's tag. A live summary shows how
  many videos would get a row.
* :func:`select_id_column` (Browse tab) — pick the column holding the video ids to
  restrict the Browse list to.

All return ``None`` if cancelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import run, ui

from annie.core import theme
from annie.dataset.manipulate import detect_type
from annie.dataset.scanning import join_summary
from annie.dataset.sources import CsvRole, DataSource, SegmentationBand, SourceKind
from annie.parsers.csvmeta import (
    JoinSuggestion,
    distinct_column_values,
    duplicate_report,
    order_rows,
    read_header,
    read_rows,
    short_name,
    suggest_join,
    suggest_key_column,
)
from annie.parsers.workbook import is_workbook, read_sheet, sheet_names

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

#: Column value types offered in the CSV configuration dialog.
_TYPES = ("str", "int", "float")

VIDEO_ID_TARGET = "video id"
"""Match target meaning "the whole video id" (path fields are identifiers, never this)."""

_ORDER_HINTS = ("timestamp", "time", "date", "created", "updated")
"""Column-name fragments that suggest a column orders rows in time."""

#: A table's match targets: target name to ``{video id: value the ID must match}``.
Targets = dict[str, dict[str, str]]


def tag_clashes(sources: list[DataSource], taken: set[str]) -> list[str]:
    """Return tag names that would collide, in the order they appear.

    A tag must be unique across the new sources and everything already shown in
    Browse (other tables' tags and nested-layout path fields), or one would silently
    overwrite the other.

    Args:
        sources: The label sources about to be added.
        taken: Tag names already in use.

    Returns:
        Each clashing name once; empty when all are unique.
    """
    seen = set(taken)
    clashes: list[str] = []
    for source in sources:
        for column in source.value_columns:
            name = source.tag_name(column)
            if name in seen and name not in clashes:
                clashes.append(name)
            seen.add(name)
    return clashes


class _JoinEditor:
    """Join, duplicate and column controls for one label table (a CSV or a sheet).

    Builds its widgets into the current UI context. :meth:`collect` turns the choices
    into a :class:`~annie.dataset.sources.DataSource`, or explains what is missing.
    """

    def __init__(self, header: list[str], rows: list[dict[str, str]], targets: Targets) -> None:
        self._header = header
        self._rows = rows
        self._targets = targets
        self._has_duplicates = False
        offered = {name: set(values.values()) - {""} for name, values in targets.items()}
        best = suggest_join(rows, header, offered)
        sample = rows[:500]
        detected = {col: detect_type((row.get(col) or "") for row in sample) for col in header}
        order_guess = next(
            (col for col in header if any(hint in col.lower() for hint in _ORDER_HINTS)), None
        )

        with ui.row().classes("w-full gap-2 no-wrap"):
            self._key = ui.select(
                header, value=best.column if best else header[0], label="ID column"
            ).classes("flex-grow min-w-0")
            self._match = (
                ui.select(
                    list(targets), value=best.target if best else VIDEO_ID_TARGET, label="Matches"
                )
                .classes("w-44")
                .tooltip("What the IDs are compared with: the whole video id, or one path field")
            )
        self._substring = ui.checkbox(
            "ID is part of the name (substring match)", value=False
        ).tooltip(
            "Link a row to every video whose name contains the ID. If one name contains "
            "several IDs, the longest wins."
        )
        self._summary = ui.label().classes("text-sm")
        with ui.column().classes("w-full gap-1") as self._duplicates:
            self._duplicate_note = ui.label().classes("text-sm").style(f"color:{theme.WARNING}")
            with ui.row().classes("w-full gap-2 no-wrap items-center"):
                self._order = ui.select(header, value=order_guess, label="Order by").classes(
                    "flex-grow min-w-0"
                )
                self._descending = ui.toggle(
                    {True: "Newest / largest first", False: "Oldest / smallest first"}, value=True
                ).props("dense no-caps")

        with ui.row().classes("w-full items-center justify-between mt-1"):
            ui.label("Feature columns · tag name · type").classes("text-sm").style(
                f"color:{theme.NEUTRAL}"
            )
            with ui.row().classes("gap-1"):
                ui.button("Select all", on_click=lambda: self._set_all(True)).props("flat dense")
                ui.button("Clear", on_click=lambda: self._set_all(False)).props("flat dense")
        self._checks: dict[str, ui.checkbox] = {}
        self._names: dict[str, ui.input] = {}
        self._types: dict[str, ui.select] = {}
        with ui.column().classes("w-full gap-0 max-h-72 overflow-auto"):
            for col in header:
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    self._checks[col] = (
                        ui.checkbox(short_name(col, 60), value=False)
                        .classes("w-64 min-w-0")
                        .tooltip(col)
                    )
                    self._names[col] = (
                        ui.input(value=short_name(col)).props("dense outlined").classes("flex-grow")
                    )
                    self._types[col] = (
                        ui.select(list(_TYPES), value=detected[col])
                        .props("dense outlined")
                        .classes("w-24")
                    )

        for element in (self._key, self._match, self._substring, self._order, self._descending):
            element.on_value_change(lambda _: self.refresh())
        self.refresh()

    def _set_all(self, value: bool) -> None:
        for box in self._checks.values():
            box.set_value(value)

    def refresh(self) -> None:
        """Recompute the match summary and the duplicate controls from the choices."""
        key = self._key.value
        if not key:
            return
        order = self._order.value
        descending = bool(self._descending.value)
        duplicated = duplicate_report(self._rows, key).duplicated
        self._has_duplicates = bool(duplicated)
        self._duplicates.set_visibility(self._has_duplicates)
        rows = self._rows
        if self._has_duplicates:
            note = f"{len(duplicated)} IDs appear more than once — choose which row counts."
            if order:
                ties = duplicate_report(self._rows, key, order, descending=descending).ties
                if ties:
                    note += f" {len(ties)} still tie on '{order}'; file order decides those."
                rows = order_rows(self._rows, order, descending=descending)
            self._duplicate_note.set_text(note)
        keys = [(row.get(key) or "").strip() for row in rows]
        summary = join_summary(
            keys, self._targets.get(self._match.value, {}), substring=bool(self._substring.value)
        )
        text = (
            f"{summary.videos_matched} of {summary.videos_total} videos matched · "
            f"{summary.keys_used} of {summary.keys_total} IDs used"
        )
        color = theme.DANGER if summary.videos_matched == 0 else theme.NEUTRAL
        if summary.ambiguous:
            text += f" · {summary.ambiguous} ambiguous (longest ID wins)"
            color = theme.WARNING
        self._summary.set_text(text)
        self._summary.style(f"color:{color}")

    def collect(self, path: Path, sheet: str | None) -> DataSource | str:
        """Build the label source, or return a message saying what is missing.

        Args:
            path: The table file.
            sheet: The worksheet, for a workbook.

        Returns:
            The :class:`~annie.dataset.sources.DataSource`, or an error message.
        """
        selected = tuple(col for col, box in self._checks.items() if box.value)
        if not selected:
            return "select at least one feature column."
        if self._has_duplicates and not self._order.value:
            return "IDs repeat, so choose an 'Order by' column to decide which row counts."
        names = {col: self._names[col].value.strip() for col in selected}
        match = self._match.value
        return DataSource(
            SourceKind.CSV,
            path,
            role=CsvRole.LABELS,
            key_column=self._key.value,
            value_columns=selected,
            column_types={col: self._types[col].value for col in selected},
            sheet=sheet,
            match_field=None if match == VIDEO_ID_TARGET else match,
            match_substring=bool(self._substring.value),
            column_names={col: name for col, name in names.items() if name and name != col},
            order_column=self._order.value if self._has_duplicates else None,
            order_descending=bool(self._descending.value),
        )


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


async def configure_table(
    path: str | Path, targets: Targets, taken: set[str]
) -> list[DataSource] | None:
    """Configure a CSV or an Excel workbook as one or more sources.

    Args:
        path: The chosen file.
        targets: What IDs can match: :data:`VIDEO_ID_TARGET` plus each path field, each
            mapping video id to the value an ID must equal (or contain).
        taken: Tag names already in use (see :func:`tag_clashes`).

    Returns:
        The sources to add (one per included sheet for a workbook), or ``None`` if
        cancelled.
    """
    if is_workbook(path):
        return await _configure_workbook(Path(path), targets, taken)
    source = await configure_csv(path, targets, taken)
    return [source] if source is not None else None


def _clash_message(sources: list[DataSource], taken: set[str]) -> str | None:
    """A notification for clashing tag names, or ``None`` when all are unique."""
    clashes = tag_clashes(sources, taken)
    if not clashes:
        return None
    return f"Tag name already used: {', '.join(clashes)} — rename it in the tag name box."


async def reading[T](path: Path, read: Callable[[], T]) -> T | None:
    """Run ``read`` off the event loop while a small dialog says the file is being read.

    Large tables and many-sheet workbooks take seconds to parse; the spinner shows the
    click was taken, and the UI stays responsive meanwhile.

    Args:
        path: The file being read (named in the dialog).
        read: The blocking read to run on a worker thread.

    Returns:
        Whatever ``read`` returned; ``None`` if the app is shutting down.
    """
    with ui.dialog().props("persistent") as dialog, ui.card().classes("items-center gap-3"):
        ui.spinner(size="lg", color=theme.PRIMARY)
        ui.label(f"Reading {path.name}…").classes("text-sm")
    dialog.open()
    try:
        return await run.io_bound(read)
    finally:
        dialog.close()
        dialog.delete()


@dataclass(frozen=True, slots=True)
class _SheetPreview:
    """One worksheet as the workbook dialog shows it: its table and the best join hint."""

    name: str
    header: list[str]
    rows: list[dict[str, str]]
    hint: JoinSuggestion | None


def _read_workbook(path: Path, offered: dict[str, set[str]]) -> list[_SheetPreview]:
    """Read every sheet of ``path`` and suggest its join (blocking; run off the loop)."""
    previews = []
    for name in sheet_names(path):
        header, rows = read_sheet(path, name)
        hint = suggest_join(rows, header, offered) if header else None
        previews.append(_SheetPreview(name, header, rows, hint))
    return previews


async def _configure_workbook(
    path: Path, targets: Targets, taken: set[str]
) -> list[DataSource] | None:
    """Workbook dialog: one expandable section per sheet, each joined separately."""
    offered = {name: set(values.values()) - {""} for name, values in targets.items()}
    sheets = await reading(path, lambda: _read_workbook(path, offered))
    if not sheets:
        ui.notify("That workbook could not be read.", color=theme.DANGER)
        return None
    editors: dict[str, _JoinEditor] = {}
    includes: dict[str, ui.checkbox] = {}

    def _build(sheet: str, body: ui.column, header: list[str], rows: list[dict[str, str]]) -> None:
        """Build a sheet's controls the first time it is opened (sheets can be wide)."""
        if sheet in editors:
            return
        with body:
            if not header or not rows:
                ui.label("This sheet has no table to read.").style(f"color:{theme.NEUTRAL}")
                return
            includes[sheet] = ui.checkbox("Include this sheet", value=True)
            editors[sheet] = _JoinEditor(header, rows, targets)

    with ui.dialog() as dialog, ui.card().classes("w-[52rem] max-w-full gap-2"):
        ui.label(f"Add workbook — {path.name}").classes("text-lg font-medium")
        ui.label(
            "Open a sheet to include it, then choose its ID column and the feature columns "
            "to show as tags. Each included sheet becomes its own label source."
        ).classes("text-sm").style(f"color:{theme.NEUTRAL}")
        with ui.column().classes("w-full gap-1 max-h-[65vh] overflow-auto"):
            for preview in sheets:
                sheet, header, rows, hint = (
                    preview.name,
                    preview.header,
                    preview.rows,
                    preview.hint,
                )
                caption = f"{len(rows)} rows · {len(header)} columns"
                if hint is not None:
                    column = short_name(hint.column, 30)
                    caption += f" · '{column}' matches {hint.matched} {hint.target} values"
                with ui.expansion(sheet, caption=caption).classes("w-full") as section:
                    body = ui.column().classes("w-full gap-2")
                section.on_value_change(
                    lambda e, sh=sheet, b=body, h=header, r=rows: (
                        _build(sh, b, h, r) if e.value else None
                    )
                )

        def _confirm() -> None:
            chosen = [sheet for sheet, box in includes.items() if box.value]
            if not chosen:
                ui.notify("Open a sheet and include it first.", color=theme.WARNING)
                return
            sources: list[DataSource] = []
            for sheet in chosen:
                result = editors[sheet].collect(path, sheet)
                if isinstance(result, str):
                    ui.notify(f"{sheet}: {result}", color=theme.WARNING)
                    return
                sources.append(result)
            clash = _clash_message(sources, taken)
            if clash:
                ui.notify(clash, color=theme.WARNING)
                return
            dialog.submit(sources)

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat")
            ui.button("Add sheets", icon="add", on_click=_confirm).props("unelevated")

    return await dialog


def _read_csv(path: Path, stems: set[str]) -> tuple[list[str], list[dict[str, str]], str]:
    """Read a CSV's header and rows and suggest its key column (blocking; run off the loop).

    Returns:
        ``(header, rows, suggested_key)``; an empty header means the file has none.
    """
    header = read_header(path)
    if not header:
        return [], [], ""
    return header, read_rows(path), suggest_key_column(path, stems) or header[0]


async def configure_csv(path: str | Path, targets: Targets, taken: set[str]) -> DataSource | None:
    """Open the CSV configuration modal and return the resulting source.

    Args:
        path: The chosen CSV file.
        targets: Match targets for label tables (see :func:`configure_table`).
        taken: Tag names already in use.

    Returns:
        A configured :class:`~annie.sources.DataSource`, or ``None`` if cancelled.
    """
    csv_path = Path(path)
    stems = set(targets.get(VIDEO_ID_TARGET, {}))
    table = await reading(csv_path, lambda: _read_csv(csv_path, stems))
    header, rows, suggested = table if table is not None else ([], [], "")
    if not header:
        ui.notify("That CSV has no header row.", color=theme.DANGER)
        return None
    sample = rows[:500]
    detected = {col: detect_type((row.get(col) or "") for row in sample) for col in header}

    with ui.dialog() as dialog, ui.card().classes("w-[44rem] max-w-full gap-2"):
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

        with ui.column().classes("w-full gap-2") as labels_box:
            editor = _JoinEditor(header, rows, targets)

        with ui.column().classes("w-full gap-2") as other_box:
            key = ui.select(
                header, value=suggested, label="Key column (joins to video id)"
            ).classes("w-full")
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
            track_col = ui.select(header, value=None, label="Track-id column").classes("w-full")
            checks: dict[str, ui.checkbox] = {}
            types: dict[str, ui.select] = {}
            with ui.column().classes("w-full gap-0 max-h-72 overflow-auto") as checkbox_box:
                ui.label("Value columns").classes("text-sm").style(f"color:{theme.NEUTRAL}")
                for col in header:
                    with ui.row().classes("w-full items-center gap-2 no-wrap"):
                        checks[col] = ui.checkbox(col, value=False).classes("flex-grow")
                        types[col] = (
                            ui.select(list(_TYPES), value=detected[col])
                            .props("dense outlined")
                            .classes("w-28")
                        )

        def _show_role() -> None:
            is_labels = role.value == CsvRole.LABELS
            labels_box.set_visibility(is_labels)
            other_box.set_visibility(not is_labels)
            track_col.set_visibility(role.value == CsvRole.PROTAGONIST)
            # Segmentation value columns double as label tags.
            checkbox_box.set_visibility(role.value == CsvRole.SEGMENTATION)
            segment_col.set_visibility(role.value == CsvRole.SEGMENTATION)
            bands.container.set_visibility(role.value == CsvRole.SEGMENTATION)

        role.on_value_change(lambda _: _show_role())
        _show_role()

        def _confirm() -> None:
            if role.value == CsvRole.PROTAGONIST:
                if not track_col.value:
                    ui.notify("Pick the track-id column.", color=theme.WARNING)
                    return
                dialog.submit(
                    DataSource(
                        SourceKind.CSV,
                        csv_path,
                        role=CsvRole.PROTAGONIST,
                        key_column=key.value,
                        value_columns=(track_col.value,),
                    )
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
                dialog.submit(
                    DataSource(
                        SourceKind.CSV,
                        csv_path,
                        role=CsvRole.SEGMENTATION,
                        key_column=key.value,
                        segment_column=segment_col.value,
                        bands=chosen_bands,
                        value_columns=selected,
                        column_types={col: types[col].value for col in selected},
                    )
                )
            else:
                result = editor.collect(csv_path, None)
                if isinstance(result, str):
                    ui.notify(result[0].upper() + result[1:], color=theme.WARNING)
                    return
                clash = _clash_message([result], taken)
                if clash:
                    ui.notify(clash, color=theme.WARNING)
                    return
                dialog.submit(result)

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
    stems = set(video_stems)
    table = await reading(csv_path, lambda: _read_csv(csv_path, stems))
    header, rows, suggested = table if table is not None else ([], [], "")
    if not header:
        ui.notify("That CSV has no header row.", color=theme.DANGER)
        return None

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
