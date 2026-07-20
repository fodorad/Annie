"""Segment-review domain: clips of long videos and their accept/drop export.

A segmentation CSV (:attr:`annie.dataset.sources.CsvRole.SEGMENTATION`) has one row
per **clip** — a span of a longer video identified by ``{video_id}_{segment_id}``.
Each row may carry several competing start/end **bands** (e.g. a ground-truth span and
a WhisperX forced-alignment span) that the Annotator renders side by side so the
reviewer can accept or drop the clip. This module turns those rows into
:class:`SegmentClip` records and writes the reviewer's decisions to two files — the
accepted set and the dropped set — keyed by clip.

Pure domain: no NiceGUI, no torch. The Annotator's Segment-review task consumes it.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from annie.parsers.csvmeta import read_rows

if TYPE_CHECKING:
    from collections.abc import Mapping

    from annie.dataset.sources import DataSource
    from annie.dataset.storage import Decision


def clip_key(video_id: str, segment_id: str) -> str:
    """Return the composite clip identity ``{video_id}_{segment_id}``.

    ``segment_id`` may already carry a leading underscore (``review_band.csv`` stores
    it as ``"_15"``); the separator is not doubled in that case.

    Args:
        video_id: The parent video id.
        segment_id: The segment id, with or without a leading underscore.

    Returns:
        The clip key, e.g. ``"227426_15"``.
    """
    suffix = segment_id[1:] if segment_id.startswith("_") else segment_id
    return f"{video_id}_{suffix}"


def next_undecided_index(
    clips: list[SegmentClip], decisions: Mapping[str, Decision], start: int
) -> int | None:
    """Find the next clip after ``start`` with no accept/drop decision yet.

    The search wraps: it scans from ``start + 1`` to the end, then from the beginning up
    to and including ``start``, so pressing "jump to next undecided" cycles through the
    whole backlog regardless of where the cursor sits. Returns ``None`` when every clip is
    decided — the caller disables the jump control in that case.

    Args:
        clips: The loaded clips, in review order.
        decisions: Clip key → decision for the clips already decided.
        start: The current clip index the jump starts from.

    Returns:
        The index of the next undecided clip, or ``None`` if all are decided.
    """
    total = len(clips)
    if total == 0:
        return None
    for offset in range(1, total + 1):
        idx = (start + offset) % total
        if clips[idx].key not in decisions:
            return idx
    return None


@dataclass(slots=True, frozen=True)
class ClipBand:
    """One resolved start/end span of a clip, in seconds.

    Attributes:
        name: The band's label (e.g. ``"GT"``, ``"cut"``).
        start: Start time in seconds.
        end: End time in seconds.
    """

    name: str
    start: float
    end: float


@dataclass(slots=True)
class SegmentClip:
    """One reviewable clip of a long video.

    Attributes:
        key: The composite ``{video_id}_{segment_id}`` review key.
        video_id: The parent video id (the media all bands share).
        segment_id: The segment id as written in the CSV.
        bands: The resolved start/end spans to compare, in file order.
        tags: The remaining (non-span) value columns, shown read-only as tags.
    """

    key: str
    video_id: str
    segment_id: str
    bands: tuple[ClipBand, ...] = ()
    tags: dict[str, str] = field(default_factory=dict)


def _parse_span(row: Mapping[str, str], start_col: str, end_col: str) -> tuple[float, float] | None:
    """Return ``(start, end)`` seconds for a band, or ``None`` if either is unparseable."""
    try:
        start = float(row[start_col])
        end = float(row[end_col])
    except (KeyError, ValueError, TypeError):
        return None
    return start, end


def load_segment_clips(source: DataSource) -> list[SegmentClip]:
    """Load a segmentation CSV into clip records, one per usable row.

    A row is kept when at least one band parses; bands whose start or end cannot be
    read as a number are dropped, and a row with no valid band at all is skipped.

    Args:
        source: A :attr:`~annie.dataset.sources.CsvRole.SEGMENTATION` data source with
            its ``key_column``, ``segment_column``, and ``bands`` configured. The clip's
            video is resolved from the scanned videos folder by ``video_id``.

    Returns:
        The clips in file order.
    """
    key_col = source.key_column
    seg_col = source.segment_column
    if key_col is None or seg_col is None:
        return []
    clips: list[SegmentClip] = []
    for row in read_rows(source.path):
        video_id = row.get(key_col, "")
        segment_id = row.get(seg_col, "")
        if not video_id or not segment_id:
            continue
        bands: list[ClipBand] = []
        for band in source.bands:
            span = _parse_span(row, band.start_column, band.end_column)
            if span is not None:
                bands.append(ClipBand(band.name, span[0], span[1]))
        if not bands:
            continue
        tags = {col: row.get(col, "") for col in source.value_columns}
        clips.append(
            SegmentClip(
                key=clip_key(video_id, segment_id),
                video_id=video_id,
                segment_id=segment_id,
                bands=tuple(bands),
                tags=tags,
            )
        )
    return clips


def _write_clip_rows(clips: list[SegmentClip], path: Path) -> Path:
    """Write ``clips`` to ``path`` as CSV: key, video/segment ids, then tag columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tag_columns: list[str] = []
    for clip in clips:
        for col in clip.tags:
            if col not in tag_columns:
                tag_columns.append(col)
    fields = ["clip_key", "video_id", "segment_id", *tag_columns]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for clip in clips:
            writer.writerow(
                {
                    "clip_key": clip.key,
                    "video_id": clip.video_id,
                    "segment_id": clip.segment_id,
                    **clip.tags,
                }
            )
    return path


def export_decision_sets(
    clips: list[SegmentClip],
    decisions: Mapping[str, Decision],
    accepted_path: str | Path,
    dropped_path: str | Path,
) -> tuple[Path, Path]:
    """Write the accepted clips and dropped clips to two separate CSV files.

    Each file carries the clip key, the video/segment ids, and the clip's passthrough
    tag columns. Undecided clips (no entry in ``decisions``) appear in neither file.

    Args:
        clips: All clips loaded for the source (defines row order and tag columns).
        decisions: The ``clip_key -> "accept"/"drop"`` map from the review store.
        accepted_path: Destination for the accepted set.
        dropped_path: Destination for the dropped set.

    Returns:
        The ``(accepted_path, dropped_path)`` written.
    """
    accepted = [c for c in clips if decisions.get(c.key) == "accept"]
    dropped = [c for c in clips if decisions.get(c.key) == "drop"]
    return (
        _write_clip_rows(accepted, Path(accepted_path)),
        _write_clip_rows(dropped, Path(dropped_path)),
    )
