"""Parser and resolver for the main-character participant file.

A main-character file maps each video to the track index that is its active /
monitored main character. Classically this is a two-column CSV — ``uuid,track_id``
— but Annie is dataset-agnostic: the **key column** (the video id) and the
**value column** (the track id) are chosen when the source is added, defaulting to
``uuid`` / ``track_id``. A ``track_id`` of ``-1`` means "no active track".

Annie keeps human judgement separate from heuristic output. The user selects the
source file; Annie never writes to it. Manual corrections go to its ``_manual``
sibling (``..._heuristic.csv`` → ``..._heuristic_manual.csv``), an upsert keyed by
the video id, written with the same two columns. Resolution order is
**manual ▸ source ▸ -1**.
"""

from __future__ import annotations

import csv
from pathlib import Path

from annie.core.models import NO_ACTIVE_TRACK

__all__ = [
    "NO_ACTIVE_TRACK",
    "export_resolved",
    "load_participants",
    "manual_path_for",
    "resolved_mapping",
    "resolve_active_track",
    "set_active_track",
]

DEFAULT_KEY_COLUMN = "uuid"
"""Default key/value columns when a source does not specify them."""
DEFAULT_VALUE_COLUMN = "track_id"
"""Default main-character track-id column."""


def manual_path_for(source_file: str | Path) -> Path:
    """Return the manual-correction sibling path for a source file.

    ``/x/participant_face_track_heuristic.csv`` →
    ``/x/participant_face_track_heuristic_manual.csv``.

    Args:
        source_file: Path to the pristine main-character source CSV.

    Returns:
        The path where manual corrections are written.
    """
    path = Path(source_file)
    return path.with_name(f"{path.stem}_manual{path.suffix}")


def load_participants(
    path: str | Path,
    key_column: str = DEFAULT_KEY_COLUMN,
    value_column: str = DEFAULT_VALUE_COLUMN,
) -> dict[str, int]:
    """Load a main-character CSV into a ``video_id -> track_id`` mapping.

    A missing file resolves to an empty mapping (treated as "no corrections /
    no source yet") rather than raising, so resolution degrades cleanly. Rows
    whose track-id value is blank or non-integer are skipped.

    Args:
        path: Path to the main-character CSV.
        key_column: The column holding the video id.
        value_column: The column holding the track index.

    Returns:
        Mapping from video id to its active track index (may be ``-1``). Empty if
        the file does not exist.
    """
    file = Path(path)
    if not file.is_file():
        return {}
    mapping: dict[str, int] = {}
    with file.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            key = (row.get(key_column) or "").strip()
            raw = (row.get(value_column) or "").strip()
            if not key or not raw:
                continue
            try:
                mapping[key] = int(float(raw))
            except ValueError:
                continue
    return mapping


def resolved_mapping(
    source_file: str | Path,
    key_column: str = DEFAULT_KEY_COLUMN,
    value_column: str = DEFAULT_VALUE_COLUMN,
) -> dict[str, int]:
    """Return the merged ``video_id -> track_id`` mapping (manual overrides source).

    Args:
        source_file: Path to the main-character source CSV.
        key_column: The column holding the video id.
        value_column: The column holding the track index.

    Returns:
        The source mapping with manual corrections layered on top.
    """
    merged = load_participants(source_file, key_column, value_column)
    merged.update(load_participants(manual_path_for(source_file), key_column, value_column))
    return merged


def resolve_active_track(
    uuid: str,
    source_file: str | Path,
    key_column: str = DEFAULT_KEY_COLUMN,
    value_column: str = DEFAULT_VALUE_COLUMN,
) -> int:
    """Resolve the active track id for a video using manual ▸ source ▸ -1.

    Args:
        uuid: The video id to look up.
        source_file: Path to the main-character source CSV.
        key_column: The column holding the video id.
        value_column: The column holding the track index.

    Returns:
        The resolved active track index, or ``-1`` when none applies.
    """
    manual = load_participants(manual_path_for(source_file), key_column, value_column)
    if uuid in manual:
        return manual[uuid]
    return load_participants(source_file, key_column, value_column).get(uuid, NO_ACTIVE_TRACK)


def _write_mapping(path: Path, mapping: dict[str, int], key_column: str, value_column: str) -> Path:
    """Write a ``video_id -> track_id`` mapping as a two-column CSV, sorted by key."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow((key_column, value_column))
        for key in sorted(mapping):
            writer.writerow((key, mapping[key]))
    return path


def set_active_track(
    uuid: str,
    track_id: int,
    source_file: str | Path,
    key_column: str = DEFAULT_KEY_COLUMN,
    value_column: str = DEFAULT_VALUE_COLUMN,
) -> Path:
    """Upsert a manual main-character correction for ``uuid``.

    Writes to the ``_manual`` sibling of the source file only (the source file is
    never touched). The write is an upsert keyed by the video id — re-correcting a
    video overwrites its previous manual row rather than appending.

    Args:
        uuid: The video id being corrected.
        track_id: The newly chosen active track index.
        source_file: Path to the main-character source CSV (defines where the
            ``_manual`` sibling lives).
        key_column: The column holding the video id.
        value_column: The column holding the track index.

    Returns:
        The path to the manual-correction file that was written.
    """
    manual_path = manual_path_for(source_file)
    mapping = load_participants(manual_path, key_column, value_column)
    mapping[uuid] = int(track_id)
    return _write_mapping(manual_path, mapping, key_column, value_column)


def export_resolved(
    source_file: str | Path,
    out_path: str | Path,
    key_column: str = DEFAULT_KEY_COLUMN,
    value_column: str = DEFAULT_VALUE_COLUMN,
) -> Path:
    """Write the resolved (manual ▸ source) mapping to a standalone CSV.

    This is the export of the corrected main-character datasource: the heuristic
    rows with every manual correction applied, in one self-contained file.

    Args:
        source_file: Path to the main-character source CSV.
        out_path: Destination CSV path.
        key_column: The column holding the video id.
        value_column: The column holding the track index.

    Returns:
        The path written.
    """
    mapping = resolved_mapping(source_file, key_column, value_column)
    return _write_mapping(Path(out_path), mapping, key_column, value_column)
