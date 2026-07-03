"""Parser for ``.track`` / ``__track{N}.csv`` files (one tracked face per file).

A track file is derived from a ``.vdet`` by a tracking heuristic and follows a
single face across frames — exactly one row per frame it appears in. The track
index is encoded in the filename after a **double** underscore, e.g.
``{video_id}__track0.csv``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from annie.parsers.base import group_rows_by_frame, read_csv_rows

if TYPE_CHECKING:
    from annie.core.models import FrameAnnotation

#: Captures the integer ``N`` from a ``...__track{N}`` filename stem.
_TRACK_ID_RE = re.compile(r"__track(\d+)$")


def track_id_from_name(path: str | Path) -> int:
    """Extract the track index ``N`` from a ``...__track{N}`` filename.

    Args:
        path: The track file path or name.

    Returns:
        The parsed track index.

    Raises:
        ValueError: If the name does not contain a ``__track{N}`` segment.
    """
    stem = Path(path).stem
    match = _TRACK_ID_RE.search(stem)
    if match is None:
        raise ValueError(f"not a '__track{{N}}' filename: {Path(path).name!r}")
    return int(match.group(1))


def load_track(path: str | Path) -> tuple[int, list[FrameAnnotation]]:
    """Load a track file into its track id and per-frame annotations.

    Every box is stamped with the track id parsed from the filename. A
    well-formed track has exactly one box per frame, but the loader does not
    enforce that — it groups defensively like the vdet loader.

    Args:
        path: Path to the ``__track{N}.csv`` file.

    Returns:
        A ``(track_id, frames)`` tuple where ``frames`` is ordered by ascending
        frame index.
    """
    track_id = track_id_from_name(path)
    rows = read_csv_rows(path)
    return track_id, group_rows_by_frame(rows, track_id=track_id)
