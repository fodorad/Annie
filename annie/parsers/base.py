"""Shared CSV schema and the parser protocol.

Both ``.vdet`` and ``.track`` files use one identical 17-column schema with a
header row and CRLF line endings:

``frame_id, source, score, x, y, w, h,``
``left_eye_x, left_eye_y, right_eye_x, right_eye_y,``
``nose_x, nose_y, left_mouth_x, left_mouth_y, right_mouth_x, right_mouth_y``

The ``source`` column is informational only — matching is done by file stem, not
by this path (it points at the original capture location, which need not exist).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Protocol, runtime_checkable

from annie.core.models import LANDMARK_NAMES, BBox, FrameAnnotation

CSV_COLUMNS: tuple[str, ...] = (
    "frame_id",
    "source",
    "score",
    "x",
    "y",
    "w",
    "h",
    "left_eye_x",
    "left_eye_y",
    "right_eye_x",
    "right_eye_y",
    "nose_x",
    "nose_y",
    "left_mouth_x",
    "left_mouth_y",
    "right_mouth_x",
    "right_mouth_y",
)
"""The 17 columns of the shared detection/track CSV schema, in order."""


@runtime_checkable
class AnnotationParser(Protocol):
    """Protocol implemented by every annotation loader.

    A parser turns a path into a list of :class:`~annie.models.FrameAnnotation`,
    one entry per distinct ``frame_id`` it contains.
    """

    def __call__(self, path: str | Path) -> list[FrameAnnotation]:
        """Parse ``path`` into per-frame annotations.

        Args:
            path: Path to the annotation file.

        Returns:
            One :class:`~annie.models.FrameAnnotation` per distinct frame index.
        """
        ...


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    """Read a shared-schema CSV into a list of column-keyed dict rows.

    Tolerant of both CRLF and LF line endings (``newline=""`` lets :mod:`csv`
    handle either) and of a UTF-8 BOM. The header row is required and is used as
    the dict keys.

    Args:
        path: Path to the CSV file.

    Returns:
        One dict per data row, keyed by column name.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def parse_bbox_row(row: dict[str, str], track_id: int | None = None) -> BBox:
    """Build a :class:`~annie.models.BBox` from one CSV row.

    Args:
        row: A column-keyed dict as produced by :func:`read_csv_rows`.
        track_id: The owning track id to stamp onto the box, or ``None`` for a
            raw detection.

    Returns:
        The parsed bounding box, with its five named landmarks populated.
    """
    landmarks = {
        name: (int(round(float(row[f"{name}_x"]))), int(round(float(row[f"{name}_y"]))))
        for name in LANDMARK_NAMES
    }
    return BBox(
        x=int(round(float(row["x"]))),
        y=int(round(float(row["y"]))),
        w=int(round(float(row["w"]))),
        h=int(round(float(row["h"]))),
        score=float(row["score"]),
        landmarks=landmarks,
        track_id=track_id,
    )


def group_rows_by_frame(
    rows: list[dict[str, str]], track_id: int | None = None
) -> list[FrameAnnotation]:
    """Group flat CSV rows into per-frame annotations, sorted by frame index.

    Multiple rows sharing a ``frame_id`` (possible in a ``.vdet``) collapse into a
    single :class:`~annie.models.FrameAnnotation` carrying all their boxes.

    Args:
        rows: Column-keyed CSV rows from :func:`read_csv_rows`.
        track_id: Track id to stamp on every box, or ``None`` for raw detections.

    Returns:
        Per-frame annotations ordered by ascending ``frame_idx``.
    """
    by_frame: dict[int, FrameAnnotation] = {}
    for row in rows:
        frame_idx = int(row["frame_id"])
        annotation = by_frame.get(frame_idx)
        if annotation is None:
            annotation = FrameAnnotation(frame_idx=frame_idx)
            by_frame[frame_idx] = annotation
        annotation.boxes.append(parse_bbox_row(row, track_id=track_id))
    return [by_frame[idx] for idx in sorted(by_frame)]
