"""Generic CSV metadata parser for label / main-character sources (domain).

Unlike the fixed 17-column detection schema (:mod:`annie.parsers.base`), a label
CSV has an arbitrary shape: the user picks which column joins to the video id (the
**key column**) and which columns to surface as tags/filters (the **value
columns**). This module reads headers, suggests a key column by matching values
against the known video stems, and builds a ``video_id -> {column: value}`` map.

It is pure and dependency-light (stdlib :mod:`csv` only), so it is unit-tested
without any media backend.
"""

from __future__ import annotations

import csv
from pathlib import Path

#: Column names commonly used as the join key, tried when no video stems match.
_KEY_HINTS: tuple[str, ...] = ("uuid", "video_id", "video", "id", "name", "filename", "stem")


def read_header(path: str | Path) -> list[str]:
    """Return the column names of a CSV (its header row).

    Args:
        path: Path to the CSV file.

    Returns:
        The header column names in order, or an empty list if the file is empty
        or missing.
    """
    file = Path(path)
    if not file.is_file():
        return []
    with file.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        return next(reader, [])


def read_rows(path: str | Path) -> list[dict[str, str]]:
    """Read a CSV into column-keyed dict rows (BOM- and CRLF-tolerant).

    Args:
        path: Path to the CSV file.

    Returns:
        One dict per data row, keyed by column name. Empty if missing.
    """
    file = Path(path)
    if not file.is_file():
        return []
    with file.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def count_rows(path: str | Path) -> int:
    """Count data rows in a CSV (excludes the header), cheaply.

    Args:
        path: Path to the CSV file.

    Returns:
        The number of data rows, or ``0`` if missing/empty.
    """
    file = Path(path)
    if not file.is_file():
        return 0
    with file.open(newline="", encoding="utf-8-sig") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def suggest_key_column(
    path: str | Path, video_stems: set[str] | None = None, *, sample: int = 500
) -> str | None:
    """Guess which column joins the CSV to videos.

    Scores each column by the fraction of its sampled values found in
    ``video_stems``; the best non-zero column wins. With no stems (or no match),
    falls back to the first header that looks like an id (see ``_KEY_HINTS``),
    else the first column.

    Args:
        path: Path to the CSV file.
        video_stems: Known video stems to match against, or ``None``.
        sample: Maximum number of rows to inspect.

    Returns:
        The suggested key-column name, or ``None`` if the file has no header.
    """
    header = read_header(path)
    if not header:
        return None

    if video_stems:
        rows = read_rows(path)[:sample]
        best_col, best_score = None, 0.0
        for col in header:
            values = [(r.get(col) or "").strip() for r in rows]
            nonempty = [v for v in values if v]
            if not nonempty:
                continue
            score = sum(1 for v in nonempty if v in video_stems) / len(nonempty)
            if score > best_score:
                best_col, best_score = col, score
        if best_col is not None and best_score > 0:
            return best_col

    lowered = {h.lower(): h for h in header}
    for hint in _KEY_HINTS:
        if hint in lowered:
            return lowered[hint]
    return header[0]


def load_value_map(
    path: str | Path, key_column: str, value_columns: tuple[str, ...] | list[str]
) -> dict[str, dict[str, str]]:
    """Build a ``key -> {value_column: value}`` mapping from a CSV.

    Args:
        path: Path to the CSV file.
        key_column: The column whose value identifies the video (the join key).
        value_columns: The columns to carry into each entry.

    Returns:
        Mapping from the (stripped) key value to a dict of the selected column
        values. Rows with an empty key are skipped; later rows win on a key clash.
    """
    mapping: dict[str, dict[str, str]] = {}
    for row in read_rows(path):
        key = (row.get(key_column) or "").strip()
        if not key:
            continue
        mapping[key] = {col: (row.get(col) or "").strip() for col in value_columns}
    return mapping


def distinct_values(value_map: dict[str, dict[str, str]], column: str) -> list[str]:
    """Return the sorted distinct non-empty values of ``column`` across a map.

    Args:
        value_map: A map from :func:`load_value_map`.
        column: The value column to collect.

    Returns:
        Sorted distinct values, empties excluded.
    """
    seen = {row[column] for row in value_map.values() if row.get(column)}
    return sorted(seen)
