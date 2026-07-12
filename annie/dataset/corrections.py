"""Protagonist correction service.

This is the service surface the UI uses to read and fix which track is the active
"protagonist" of a video. It composes the domain-level participant resolver
(:mod:`annie.parsers.participants`) and the geometric hit-test
(:mod:`annie.color`) so the UI never reaches past the service layer.

The correction flow: the annotator clicks the true protagonist on one of the
five highlighted frames; :func:`hit_test_frame` maps the click to a ``track_id``;
:func:`set_active_track` persists it (manual ``_manual`` sibling, upsert by uuid);
the frames are re-rendered so the new active track shows green.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

from annie.core.config import settings
from annie.media.color import hit_test
from annie.parsers.participants import DEFAULT_KEY_COLUMN, DEFAULT_VALUE_COLUMN
from annie.parsers.participants import (
    export_resolved as _export_resolved,
)
from annie.parsers.participants import (
    resolve_active_track as _resolve_active_track,
)
from annie.parsers.participants import (
    set_active_track as _set_active_track,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from annie.core.models import FrameAnnotation

__all__ = [
    "export_corrected",
    "export_active_tracks",
    "hit_test_frame",
    "manual_sibling",
    "resolve_active_track",
    "set_active_track",
]


def manual_sibling(heuristic_path: str | Path) -> Path:
    """The ``_manual`` CSV path for a protagonist source, next to the heuristic file.

    ``protagonist_track_heuristic.csv`` → ``protagonist_track_manual.csv``; any other
    name gets a ``_manual`` suffix. This is where the Annotator exports its
    DB-stored corrections.

    Args:
        heuristic_path: The protagonist source CSV.

    Returns:
        The sibling manual-corrections CSV path.
    """
    path = Path(heuristic_path)
    stem = path.stem
    name = stem.replace("heuristic", "manual") if "heuristic" in stem else f"{stem}_manual"
    return path.with_name(f"{name}{path.suffix}")


def export_active_tracks(
    out_path: str | Path,
    mapping: Mapping[str, int],
    key_column: str = DEFAULT_KEY_COLUMN,
    value_column: str = DEFAULT_VALUE_COLUMN,
) -> Path:
    """Write a ``video_id -> track_id`` correction mapping as a two-column CSV.

    Args:
        out_path: Destination CSV path (parent directories are created).
        mapping: The corrections to write, keyed by video id.
        key_column: Header for the video-id column.
        value_column: Header for the track-id column.

    Returns:
        The path written.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([key_column, value_column])
        for video_id, track_id in sorted(mapping.items()):
            writer.writerow([video_id, track_id])
    return out


def _participants_file(heuristic_file: str | Path | None) -> Path:
    """Resolve the protagonist source file, falling back to configured settings."""
    file = heuristic_file if heuristic_file is not None else settings.participants_file
    if file is None:
        raise ValueError("participants_file is not configured")
    return Path(file)


def resolve_active_track(
    uuid: str,
    heuristic_file: str | Path | None = None,
    key_column: str = DEFAULT_KEY_COLUMN,
    value_column: str = DEFAULT_VALUE_COLUMN,
) -> int:
    """Resolve a video's active track id (manual ▸ source ▸ -1).

    Args:
        uuid: The video id to look up.
        heuristic_file: The protagonist source CSV. Defaults to the configured
            ``participants_file``.
        key_column: The column holding the video id.
        value_column: The column holding the track index.

    Returns:
        The active track index, or ``-1`` when none applies.
    """
    return _resolve_active_track(uuid, _participants_file(heuristic_file), key_column, value_column)


def set_active_track(
    uuid: str,
    track_id: int,
    heuristic_file: str | Path | None = None,
    key_column: str = DEFAULT_KEY_COLUMN,
    value_column: str = DEFAULT_VALUE_COLUMN,
) -> Path:
    """Persist a manual protagonist correction (upsert by video id).

    Args:
        uuid: The video id being corrected.
        track_id: The newly chosen active track index.
        heuristic_file: The protagonist source CSV (defines where the ``_manual``
            sibling lives). Defaults to the configured ``participants_file``.
        key_column: The column holding the video id.
        value_column: The column holding the track index.

    Returns:
        The path to the manual-correction file that was written.
    """
    return _set_active_track(
        uuid, track_id, _participants_file(heuristic_file), key_column, value_column
    )


def export_corrected(
    out_path: str | Path,
    heuristic_file: str | Path | None = None,
    key_column: str = DEFAULT_KEY_COLUMN,
    value_column: str = DEFAULT_VALUE_COLUMN,
) -> Path:
    """Export the resolved (manual ▸ source) protagonist mapping to a CSV.

    Args:
        out_path: Destination CSV path.
        heuristic_file: The protagonist source CSV. Defaults to the configured
            ``participants_file``.
        key_column: The column holding the video id.
        value_column: The column holding the track index.

    Returns:
        The path written.
    """
    return _export_resolved(_participants_file(heuristic_file), out_path, key_column, value_column)


def hit_test_frame(
    click_xy: tuple[int, int], annotation: FrameAnnotation, *, smallest_wins: bool = True
) -> int | None:
    """Map a click on a frame to the enclosing box's ``track_id``.

    Args:
        click_xy: The ``(x, y)`` click position in image-pixel space.
        annotation: The boxes drawn on the clicked frame.
        smallest_wins: Tie-break toward the smallest enclosing box when ``True``.

    Returns:
        The selected ``track_id``, or ``None`` if the click hit no track box.
    """
    return hit_test(click_xy, annotation, smallest_wins=smallest_wins)
