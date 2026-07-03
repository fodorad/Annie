"""Annotation parsers for Annie.

Each parser consumes one on-disk annotation format and returns domain models
(:mod:`annie.models`). All current formats share a single 17-column CSV schema;
see :mod:`annie.parsers.base`.
"""

from annie.parsers.base import (
    CSV_COLUMNS,
    AnnotationParser,
    parse_bbox_row,
    read_csv_rows,
)
from annie.parsers.participants import (
    load_participants,
    manual_path_for,
    resolve_active_track,
    resolved_mapping,
    set_active_track,
)
from annie.parsers.track import load_track, track_id_from_name
from annie.parsers.vdet import load_vdet

__all__ = [
    "CSV_COLUMNS",
    "AnnotationParser",
    "parse_bbox_row",
    "read_csv_rows",
    "load_vdet",
    "load_track",
    "track_id_from_name",
    "load_participants",
    "manual_path_for",
    "resolved_mapping",
    "resolve_active_track",
    "set_active_track",
]
