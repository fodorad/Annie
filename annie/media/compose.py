"""Compose a video's vdet and track annotations into per-frame overlays (service).

A :class:`~annie.models.VideoEntry` aggregates one video's raw detections and all
of its tracks. To draw a frame we merge the relevant boxes into a single
:class:`~annie.models.FrameAnnotation`, then hand it to
:func:`annie.color.draw_overlay`, which colours each box by the standing rules:

* vdet boxes (``track_id is None``) → flat **blue**;
* track boxes → a **stable unique colour** per track id (never blue/green);
* the **active / protagonist** track → **green**, overriding its palette colour.

Two inclusion modes are used: the Browse five-frame **strip** shows vdet + only
the active track; the full **render** shows vdet + every track.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from annie.core.models import FrameAnnotation
from annie.parsers.track import load_track
from annie.parsers.vdet import load_vdet

if TYPE_CHECKING:
    from annie.core.models import VideoEntry


def load_entry_annotations(
    entry: VideoEntry,
) -> tuple[dict[int, FrameAnnotation], dict[int, dict[int, FrameAnnotation]]]:
    """Load and index a video's vdet and track annotations by frame.

    Args:
        entry: The video whose annotation files to read.

    Returns:
        A ``(vdet_by_frame, tracks_by_id)`` pair where ``vdet_by_frame`` maps a
        frame index to its raw detections, and ``tracks_by_id`` maps each track id
        to its own ``frame_index -> FrameAnnotation`` mapping.
    """
    vdet_by_frame: dict[int, FrameAnnotation] = {}
    if entry.vdet_path is not None:
        vdet_by_frame = {fa.frame_idx: fa for fa in load_vdet(entry.vdet_path)}

    tracks_by_id: dict[int, dict[int, FrameAnnotation]] = {}
    for path in entry.track_paths:
        track_id, frames = load_track(path)
        tracks_by_id[track_id] = {fa.frame_idx: fa for fa in frames}
    return vdet_by_frame, tracks_by_id


def merge_frame(
    frame_idx: int,
    vdet_by_frame: dict[int, FrameAnnotation],
    tracks_by_id: dict[int, dict[int, FrameAnnotation]],
    include_track_ids: list[int],
) -> FrameAnnotation:
    """Combine vdet and selected track boxes for one frame.

    Args:
        frame_idx: The frame to assemble.
        vdet_by_frame: Per-frame raw detections (from :func:`load_entry_annotations`).
        tracks_by_id: Per-track per-frame annotations (from :func:`load_entry_annotations`).
        include_track_ids: Which track ids to include on this frame.

    Returns:
        A single :class:`~annie.models.FrameAnnotation` holding the vdet boxes
        followed by the selected track boxes present on that frame.
    """
    boxes = list(vdet_by_frame[frame_idx].boxes) if frame_idx in vdet_by_frame else []
    for track_id in include_track_ids:
        frames = tracks_by_id.get(track_id)
        if frames is not None and frame_idx in frames:
            boxes.extend(frames[frame_idx].boxes)
    return FrameAnnotation(frame_idx=frame_idx, boxes=boxes)


def strip_track_ids(entry: VideoEntry) -> list[int]:
    """Track ids to draw on the Browse strip: just the active one, if any.

    Args:
        entry: The video being shown.

    Returns:
        ``[active_track_id]`` when a protagonist is assigned, else ``[]``.
    """
    return [entry.active_track_id] if entry.has_active_track else []
