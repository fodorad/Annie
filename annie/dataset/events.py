"""Event-annotation domain: interval events on tracks, and their export.

The Event-annotation task lets a reviewer mark **events** — labelled ``[start, end]`` frame
intervals — on named **tracks** (a track *is* its category) while watching one video. Unlike
the CSV-driven tasks, nothing is loaded from a source file: events are authored from scratch
and persisted to the review database (see :class:`annie.dataset.storage.EventRecord`). This
module holds the pure pieces around that store:

* frame ↔ second conversion, since the database is the source of truth in **frames**
  (fps-independent) while humans and exports think in seconds;
* interval clamping, so a typed or dragged boundary can never leave the video;
* an event's **effective** colour and attributes: an event linked to an event category
  (:class:`annie.dataset.storage.EventType`) inherits the category's colour and default
  attributes, and its own values win;
* the export shapes. **JSON** is nested — one object per video, tracks holding their
  events with attributes inline — for downstream code. **CSV** and **XLSX** are flat — one
  row per event, attributes flattened into ``attr_<key>`` columns — for a spreadsheet
  (XLSX opens in Excel with typed numbers, a frozen header and filters). That split is
  deliberate: nested attributes do not live cleanly in a spreadsheet cell.

Pure domain: no NiceGUI, no torch. The task UI and the store consume it.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from annie.dataset.storage import EventRecord, EventType


def frames_to_seconds(frame: int, fps: float) -> float:
    """Convert a frame index to seconds at ``fps``.

    Args:
        frame: The frame index.
        fps: Frames per second (``<= 0`` is treated as ``0`` seconds, since no timebase
            is known — the caller falls back to frame numbers).

    Returns:
        The time in seconds, or ``0.0`` when ``fps`` is non-positive.
    """
    if fps <= 0:
        return 0.0
    return frame / fps


def seconds_to_frame(sec: float, fps: float) -> int:
    """Convert seconds to the nearest frame index at ``fps``.

    Rounds to the nearest frame; on constant-fps media this is exact, and on a variable-fps
    file it may be off by one frame (the documented playback limitation).

    Args:
        sec: The time in seconds.
        fps: Frames per second (``<= 0`` yields frame ``0``).

    Returns:
        The nearest non-negative frame index.
    """
    if fps <= 0:
        return 0
    return max(0, round(sec * fps))


def clamp_event(start: int, end: int, num_frames: int) -> tuple[int, int]:
    """Order and bound an event's frames to a valid interval within the video.

    Swaps a reversed pair so ``start <= end``, then clamps both to ``[0, last]`` where
    ``last = num_frames - 1``. A video with no known frame count (``num_frames <= 0``) is
    clamped only at the lower bound, so an event can still be authored before metadata loads.

    Args:
        start: The proposed start frame.
        end: The proposed end frame.
        num_frames: The video's total frame count (``<= 0`` if unknown).

    Returns:
        The ordered, bounded ``(start, end)`` pair.
    """
    lo, hi = (start, end) if start <= end else (end, start)
    lo = max(0, lo)
    hi = max(0, hi)
    if num_frames > 0:
        last = num_frames - 1
        lo = min(lo, last)
        hi = min(hi, last)
    return lo, hi


def effective_color(
    event: EventRecord,
    types: Mapping[str, EventType] | None = None,
    lane_color: str | None = None,
) -> str | None:
    """Return the colour an event is drawn and exported with.

    Its own colour wins, then its event category's, then the participant lane's.

    Args:
        event: The event.
        types: Event categories by name (``None`` when there are none).
        lane_color: The participant lane's colour, the last fallback.

    Returns:
        A hex colour, or ``None`` when nothing sets one.
    """
    if event.color:
        return event.color
    kind = (types or {}).get(event.event_type or "")
    if kind is not None and kind.color:
        return kind.color
    return lane_color


def effective_attributes(
    event: EventRecord, types: Mapping[str, EventType] | None = None
) -> dict[str, str]:
    """Return an event's attributes with its event category's defaults filled in.

    Args:
        event: The event.
        types: Event categories by name (``None`` when there are none).

    Returns:
        ``{**category defaults, **event's own}``, so the event's own values win.
    """
    kind = (types or {}).get(event.event_type or "")
    defaults = kind.attributes if kind is not None else {}
    return {**defaults, **event.attributes}


def _event_dict(
    event: EventRecord, fps: float, types: Mapping[str, EventType] | None = None
) -> dict[str, object]:
    """Render one event as an export dict, with seconds derived from ``fps``."""
    return {
        "event_id": event.event_id,
        "start_frame": event.start_frame,
        "end_frame": event.end_frame,
        "start_sec": round(frames_to_seconds(event.start_frame, fps), 3),
        "end_sec": round(frames_to_seconds(event.end_frame, fps), 3),
        "label": event.label,
        "event_type": event.event_type,
        "note": event.note,
        "color": effective_color(event, types),
        "attributes": effective_attributes(event, types),
    }


def build_export_tree(
    events: Iterable[EventRecord],
    fps_by_video: Mapping[str, float],
    meta_by_video: Mapping[str, Mapping[str, str]] | None = None,
    types: Mapping[str, EventType] | None = None,
) -> dict[str, dict[str, object]]:
    """Group events into the nested ``video_id -> {fps, tracks}`` export structure.

    Shared by the JSON export and, indirectly, the CSV export's row order. Videos and their
    tracks appear in first-seen order; events keep the order they arrive in (the store hands
    them back sorted by track then start frame).

    Args:
        events: The events to export.
        fps_by_video: Frames per second per ``video_id``, for the derived seconds. A video
            missing here is exported with ``fps = 0`` and zero seconds (frames still stand).
        meta_by_video: Optional per-video metadata (e.g. the fields a nested layout captured
            from the path), exported as a ``"meta"`` object when a video has any.
        types: Event categories by name, so linked events export their effective colour
            and attributes.

    Returns:
        A mapping of ``video_id`` to ``{"fps": float, "tracks": {...}}`` (plus ``"meta"``).
    """
    # Built with concretely-typed locals, then widened to object-valued dicts on return, so
    # the nested structure type-checks without a per-access cast.
    fps_of: dict[str, float] = {}
    tracks_of: dict[str, dict[str, list[dict[str, object]]]] = {}
    for event in events:
        fps = float(fps_by_video.get(event.video_id, 0.0))
        fps_of.setdefault(event.video_id, fps)
        video_tracks = tracks_of.setdefault(event.video_id, {})
        video_tracks.setdefault(event.track, []).append(_event_dict(event, fps, types))
    meta = meta_by_video or {}
    tree: dict[str, dict[str, object]] = {}
    for video_id in tracks_of:
        node: dict[str, object] = {"fps": fps_of[video_id], "tracks": tracks_of[video_id]}
        if meta.get(video_id):
            node["meta"] = dict(meta[video_id])
        tree[video_id] = node
    return tree


def events_to_json_text(
    events: Iterable[EventRecord],
    fps_by_video: Mapping[str, float],
    meta_by_video: Mapping[str, Mapping[str, str]] | None = None,
    types: Mapping[str, EventType] | None = None,
) -> str:
    """Render events as nested JSON text (one object per video).

    Suits downstream code: attributes stay nested, seconds are derived per video's fps. A
    per-video export is this same shape with a single top-level key; a session export has
    many.

    Args:
        events: The events to render.
        fps_by_video: Frames per second per ``video_id``.
        meta_by_video: Optional per-video metadata exported as ``"meta"``.
        types: Event categories by name (for effective colour and attributes).

    Returns:
        The JSON document as a string.
    """
    tree = build_export_tree(events, fps_by_video, meta_by_video, types)
    return json.dumps(tree, indent=2, ensure_ascii=False)


def export_events_json(
    events: Iterable[EventRecord],
    fps_by_video: Mapping[str, float],
    path: str | Path,
) -> Path:
    """Write events to a nested JSON file (see :func:`events_to_json_text`).

    Args:
        events: The events to write.
        fps_by_video: Frames per second per ``video_id``.
        path: Destination ``.json`` path (parent directories are created).

    Returns:
        The path written.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(events_to_json_text(events, fps_by_video), encoding="utf-8")
    return out


def event_table(
    events: Iterable[EventRecord],
    fps_by_video: Mapping[str, float],
    meta_by_video: Mapping[str, Mapping[str, str]] | None = None,
    types: Mapping[str, EventType] | None = None,
) -> tuple[list[str], list[dict[str, object]]]:
    """Flatten events into spreadsheet rows (one per event), shared by CSV and XLSX.

    Fixed columns come first, then one ``meta_<key>`` column per metadata key seen, then
    one ``attr_<key>`` column per attribute key seen across *all* events (empty where an
    event lacks that key), so colleagues can open and sort the file in Excel. Keys are
    sorted for a stable header. Colour and attributes are the effective ones, so the file
    is complete without the database.

    Args:
        events: The events to flatten.
        fps_by_video: Frames per second per ``video_id`` (for the derived seconds).
        meta_by_video: Optional per-video metadata.
        types: Event categories by name (for effective colour and attributes).

    Returns:
        The column names and the rows (frames as ints, seconds as floats, the rest text).
    """
    events = list(events)
    attributes = [effective_attributes(event, types) for event in events]
    attr_keys = sorted({key for attrs in attributes for key in attrs})
    fixed = [
        "video_id",
        "track",
        "start_frame",
        "end_frame",
        "start_sec",
        "end_sec",
        "label",
        "event_type",
        "note",
        "color",
    ]
    meta = meta_by_video or {}
    meta_keys = sorted({key for event in events for key in meta.get(event.video_id, {})})
    fields = [
        *fixed,
        *(f"meta_{key}" for key in meta_keys),
        *(f"attr_{key}" for key in attr_keys),
    ]
    rows: list[dict[str, object]] = []
    for event, attrs in zip(events, attributes, strict=True):
        fps = float(fps_by_video.get(event.video_id, 0.0))
        row: dict[str, object] = {
            "video_id": event.video_id,
            "track": event.track,
            "start_frame": event.start_frame,
            "end_frame": event.end_frame,
            "start_sec": round(frames_to_seconds(event.start_frame, fps), 3),
            "end_sec": round(frames_to_seconds(event.end_frame, fps), 3),
            "label": event.label,
            "event_type": event.event_type or "",
            "note": event.note,
            "color": effective_color(event, types) or "",
        }
        for key in meta_keys:
            row[f"meta_{key}"] = meta.get(event.video_id, {}).get(key, "")
        for key in attr_keys:
            row[f"attr_{key}"] = attrs.get(key, "")
        rows.append(row)
    return fields, rows


def events_to_csv_text(
    events: Iterable[EventRecord],
    fps_by_video: Mapping[str, float],
    meta_by_video: Mapping[str, Mapping[str, str]] | None = None,
    types: Mapping[str, EventType] | None = None,
) -> str:
    """Render events as flat CSV text (one row per event; columns as :func:`event_table`).

    Args:
        events: The events to render.
        fps_by_video: Frames per second per ``video_id`` (for the derived seconds).
        meta_by_video: Optional per-video metadata, as ``meta_<key>`` columns.
        types: Event categories by name (for effective colour and attributes).

    Returns:
        The CSV document as a string.
    """
    fields, rows = event_table(events, fps_by_video, meta_by_video, types)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def export_events_csv(
    events: Iterable[EventRecord],
    fps_by_video: Mapping[str, float],
    path: str | Path,
) -> Path:
    """Write events to a flat CSV file (see :func:`events_to_csv_text`).

    Args:
        events: The events to write.
        fps_by_video: Frames per second per ``video_id`` (for the derived seconds).
        path: Destination ``.csv`` path (parent directories are created).

    Returns:
        The path written.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        handle.write(events_to_csv_text(events, fps_by_video))
    return out


_XLSX_MAX_WIDTH = 60
"""Widest a column is sized to (in characters), so a long note does not push the rest away."""


def events_to_xlsx_bytes(
    events: Iterable[EventRecord],
    fps_by_video: Mapping[str, float],
    meta_by_video: Mapping[str, Mapping[str, str]] | None = None,
    types: Mapping[str, EventType] | None = None,
) -> bytes:
    """Render events as an Excel workbook (same columns as the CSV, see :func:`event_table`).

    One ``Events`` sheet: a bold, frozen header with filters, frames and seconds stored as
    numbers (so Excel sorts and sums them), and columns sized to their content.

    Args:
        events: The events to render.
        fps_by_video: Frames per second per ``video_id`` (for the derived seconds).
        meta_by_video: Optional per-video metadata, as ``meta_<key>`` columns.
        types: Event categories by name (for effective colour and attributes).

    Returns:
        The ``.xlsx`` file's bytes.
    """
    # Imported here: only an Excel export needs it, so the app starts without loading it.
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    fields, rows = event_table(events, fps_by_video, meta_by_video, types)
    book = Workbook()
    sheet = book.active
    assert sheet is not None  # a new workbook always has one sheet
    sheet.title = "Events"
    sheet.append(fields)
    for row in rows:
        sheet.append([row[field] for field in fields])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for i, field in enumerate(fields, start=1):
        width = max([len(field), *(len(str(row[field])) for row in rows)])
        sheet.column_dimensions[get_column_letter(i)].width = min(width + 2, _XLSX_MAX_WIDTH)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def export_events_xlsx(
    events: Iterable[EventRecord],
    fps_by_video: Mapping[str, float],
    path: str | Path,
) -> Path:
    """Write events to an Excel file (see :func:`events_to_xlsx_bytes`).

    Args:
        events: The events to write.
        fps_by_video: Frames per second per ``video_id`` (for the derived seconds).
        path: Destination ``.xlsx`` path (parent directories are created).

    Returns:
        The path written.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(events_to_xlsx_bytes(events, fps_by_video))
    return out
