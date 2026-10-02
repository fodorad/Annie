"""Event-annotation domain: interval events on tracks, and their export.

The Event-annotation task lets a reviewer mark **events** — labelled ``[start, end]`` frame
intervals — on named **tracks** (a track *is* its category) while watching one video. Unlike
the CSV-driven tasks, nothing is loaded from a source file: events are authored from scratch
and persisted to the review database (see :class:`annie.dataset.storage.EventRecord`). This
module holds the pure pieces around that store:

* frame ↔ second conversion, since the database is the source of truth in **frames**
  (fps-independent) while humans and exports think in seconds;
* interval clamping, so a typed or dragged boundary can never leave the video;
* the two export shapes. **JSON** is nested — one object per video, tracks holding their
  events with attributes inline — for downstream code. **CSV** is flat — one row per event,
  attributes flattened into ``attr_<key>`` columns — for opening in a spreadsheet. That
  split is deliberate: nested attributes do not live cleanly in a CSV cell.

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

    from annie.dataset.storage import EventRecord


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


def _event_dict(event: EventRecord, fps: float) -> dict[str, object]:
    """Render one event as an export dict, with seconds derived from ``fps``."""
    return {
        "event_id": event.event_id,
        "start_frame": event.start_frame,
        "end_frame": event.end_frame,
        "start_sec": round(frames_to_seconds(event.start_frame, fps), 3),
        "end_sec": round(frames_to_seconds(event.end_frame, fps), 3),
        "label": event.label,
        "note": event.note,
        "color": event.color,
        "attributes": dict(event.attributes),
    }


def build_export_tree(
    events: Iterable[EventRecord], fps_by_video: Mapping[str, float]
) -> dict[str, dict[str, object]]:
    """Group events into the nested ``video_id -> {fps, tracks}`` export structure.

    Shared by the JSON export and, indirectly, the CSV export's row order. Videos and their
    tracks appear in first-seen order; events keep the order they arrive in (the store hands
    them back sorted by track then start frame).

    Args:
        events: The events to export.
        fps_by_video: Frames per second per ``video_id``, for the derived seconds. A video
            missing here is exported with ``fps = 0`` and zero seconds (frames still stand).

    Returns:
        A mapping of ``video_id`` to ``{"fps": float, "tracks": {track: [event dict, ...]}}``.
    """
    # Built with concretely-typed locals, then widened to object-valued dicts on return, so
    # the nested structure type-checks without a per-access cast.
    fps_of: dict[str, float] = {}
    tracks_of: dict[str, dict[str, list[dict[str, object]]]] = {}
    for event in events:
        fps = float(fps_by_video.get(event.video_id, 0.0))
        fps_of.setdefault(event.video_id, fps)
        video_tracks = tracks_of.setdefault(event.video_id, {})
        video_tracks.setdefault(event.track, []).append(_event_dict(event, fps))
    return {
        video_id: {"fps": fps_of[video_id], "tracks": tracks_of[video_id]} for video_id in tracks_of
    }


def events_to_json_text(events: Iterable[EventRecord], fps_by_video: Mapping[str, float]) -> str:
    """Render events as nested JSON text (one object per video).

    Suits downstream code: attributes stay nested, seconds are derived per video's fps. A
    per-video export is this same shape with a single top-level key; a session export has
    many.

    Args:
        events: The events to render.
        fps_by_video: Frames per second per ``video_id``.

    Returns:
        The JSON document as a string.
    """
    tree = build_export_tree(events, fps_by_video)
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


def events_to_csv_text(events: Iterable[EventRecord], fps_by_video: Mapping[str, float]) -> str:
    """Render events as flat CSV text (one row per event) for a spreadsheet.

    Fixed columns come first, then one ``attr_<key>`` column per attribute key seen across
    *all* events (empty where an event lacks that key), so colleagues can open and sort the
    file in Excel. Attribute keys are sorted for a stable header.

    Args:
        events: The events to render.
        fps_by_video: Frames per second per ``video_id`` (for the derived seconds).

    Returns:
        The CSV document as a string.
    """
    events = list(events)
    attr_keys = sorted({key for event in events for key in event.attributes})
    fixed = [
        "video_id",
        "track",
        "start_frame",
        "end_frame",
        "start_sec",
        "end_sec",
        "label",
        "note",
        "color",
    ]
    fields = [*fixed, *(f"attr_{key}" for key in attr_keys)]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for event in events:
        fps = float(fps_by_video.get(event.video_id, 0.0))
        row: dict[str, object] = {
            "video_id": event.video_id,
            "track": event.track,
            "start_frame": event.start_frame,
            "end_frame": event.end_frame,
            "start_sec": round(frames_to_seconds(event.start_frame, fps), 3),
            "end_sec": round(frames_to_seconds(event.end_frame, fps), 3),
            "label": event.label,
            "note": event.note,
            "color": event.color or "",
        }
        for key in attr_keys:
            row[f"attr_{key}"] = event.attributes.get(key, "")
        writer.writerow(row)
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
