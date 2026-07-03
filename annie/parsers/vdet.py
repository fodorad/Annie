"""Parser for ``.vdet`` files (raw per-video face detections).

A ``.vdet`` is computed once per video and holds *all* face detections, frame by
frame. A single frame may contain several rows (several faces), so the loader
groups rows by ``frame_id``. Detections carry no track identity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from annie.parsers.base import group_rows_by_frame, read_csv_rows

if TYPE_CHECKING:
    from pathlib import Path

    from annie.core.models import FrameAnnotation


def load_vdet(path: str | Path) -> list[FrameAnnotation]:
    """Load a ``.vdet`` file into per-frame annotations.

    Rows sharing a ``frame_id`` are grouped, so a frame with three detected faces
    yields one :class:`~annie.models.FrameAnnotation` holding three boxes. Boxes
    have ``track_id is None`` because raw detections are not yet tracked.

    Args:
        path: Path to the ``.vdet`` file.

    Returns:
        Per-frame annotations ordered by ascending frame index.
    """
    rows = read_csv_rows(path)
    return group_rows_by_frame(rows, track_id=None)
