"""Generic table metadata parser for label / protagonist sources (domain).

Unlike the fixed 17-column detection schema (:mod:`annie.parsers.base`), a label
table has an arbitrary shape: the user picks which column joins to the video id (the
**key column**) and which columns to surface as tags/filters (the **value
columns**). This module reads headers, suggests a key column by matching values
against the known video stems, and builds a ``key -> {column: value}`` map.

A table is a CSV file or one worksheet of an Excel workbook: every reader takes an
optional ``sheet`` and hands workbooks to :mod:`annie.parsers.workbook`, so callers
never care which format they read.

When a key repeats, an explicit **order column** decides which row counts (e.g. the
newest timestamp); see :func:`order_rows` and :func:`duplicate_report`.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from annie.parsers.workbook import is_workbook, read_sheet

_SHORT_NAME_LIMIT = 40
"""Longest default tag name; long headers (e.g. whole survey questions) are cut to this."""

#: Column names commonly used as the join key, tried when no video stems match.
_KEY_HINTS: tuple[str, ...] = ("uuid", "video_id", "video", "id", "name", "filename", "stem")


def read_header(path: str | Path, sheet: str | None = None) -> list[str]:
    """Return the column names of a table (its header row).

    Args:
        path: Path to the CSV file or workbook.
        sheet: The worksheet of a workbook (``None``: the first); ignored for a CSV.

    Returns:
        The header column names in order, or an empty list if the file is empty
        or missing.
    """
    if is_workbook(path):
        return read_sheet(path, sheet)[0]
    file = Path(path)
    if not file.is_file():
        return []
    with file.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        return next(reader, [])


def read_rows(path: str | Path, sheet: str | None = None) -> list[dict[str, str]]:
    """Read a table into column-keyed dict rows (CSV: BOM- and CRLF-tolerant).

    Args:
        path: Path to the CSV file or workbook.
        sheet: The worksheet of a workbook (``None``: the first); ignored for a CSV.

    Returns:
        One dict per data row, keyed by column name. Empty if missing.
    """
    if is_workbook(path):
        return read_sheet(path, sheet)[1]
    file = Path(path)
    if not file.is_file():
        return []
    with file.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def count_rows(path: str | Path, sheet: str | None = None) -> int:
    """Count data rows in a table (excludes the header), cheaply.

    Args:
        path: Path to the CSV file or workbook.
        sheet: The worksheet of a workbook (``None``: the first); ignored for a CSV.

    Returns:
        The number of data rows, or ``0`` if missing/empty.
    """
    if is_workbook(path):
        return len(read_sheet(path, sheet)[1])
    file = Path(path)
    if not file.is_file():
        return 0
    with file.open(newline="", encoding="utf-8-sig") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def suggest_key_column(
    path: str | Path,
    video_stems: set[str] | None = None,
    *,
    sample: int = 500,
    sheet: str | None = None,
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
        sheet: The worksheet of a workbook (``None``: the first); ignored for a CSV.

    Returns:
        The suggested key-column name, or ``None`` if the file has no header.
    """
    header = read_header(path, sheet)
    if not header:
        return None

    if video_stems:
        rows = read_rows(path, sheet)[:sample]
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
    path: str | Path,
    key_column: str,
    value_columns: tuple[str, ...] | list[str],
    *,
    sheet: str | None = None,
    order_column: str | None = None,
    descending: bool = True,
) -> dict[str, dict[str, str]]:
    """Build a ``key -> {value_column: value}`` mapping from a table.

    Args:
        path: Path to the CSV file or workbook.
        key_column: The column whose value identifies the video (the join key).
        value_columns: The columns to carry into each entry.
        sheet: The worksheet of a workbook (``None``: the first); ignored for a CSV.
        order_column: Decides which row counts when a key repeats: rows are ordered by
            it (see :func:`order_rows`) and the first row per key wins.
        descending: Order direction; ``True`` puts the largest / newest value first.

    Returns:
        Mapping from the (stripped) key value to a dict of the selected column
        values, in winning-row order. Rows with an empty key are skipped. Without an
        ``order_column`` a repeated key keeps its last row (the historical rule; the
        Dataset tab requires an order column whenever a key repeats).
    """
    rows = read_rows(path, sheet)
    if order_column is not None:
        rows = order_rows(rows, order_column, descending=descending)
    mapping: dict[str, dict[str, str]] = {}
    for row in rows:
        key = (row.get(key_column) or "").strip()
        if not key or (order_column is not None and key in mapping):
            continue
        mapping[key] = {col: (row.get(col) or "").strip() for col in value_columns}
    return mapping


def _is_number(value: str) -> bool:
    """Whether ``value`` parses as a number."""
    try:
        float(value)
    except ValueError:
        return False
    return True


def order_rows(
    rows: list[dict[str, str]], column: str, *, descending: bool
) -> list[dict[str, str]]:
    """Return ``rows`` sorted by ``column``, stably, with empty values always last.

    The column compares **numerically** when every non-empty value is a number
    (``9 < 10``), otherwise as text — which also orders ISO dates and timestamps
    chronologically, the form :mod:`annie.parsers.workbook` gives Excel dates. Rows with
    equal values keep their file order, so the result is always deterministic.

    Args:
        rows: Dict rows as returned by :func:`read_rows`.
        column: The column to order by.
        descending: ``True`` for largest / newest first.

    Returns:
        A new, ordered list (the input is not modified).
    """
    filled = [row for row in rows if (row.get(column) or "").strip()]
    empty = [row for row in rows if not (row.get(column) or "").strip()]
    if all(_is_number(row[column].strip()) for row in filled):
        filled.sort(key=lambda row: float(row[column]), reverse=descending)
    else:
        filled.sort(key=lambda row: row[column].strip(), reverse=descending)
    return filled + empty


@dataclass(slots=True)
class DuplicateReport:
    """How a table's key column repeats, for the Dataset tab's warning.

    Attributes:
        duplicated: Keys that appear on more than one row, in first-seen order.
        ties: Duplicated keys whose two leading rows (after ordering) share the same
            order value, so only file order separates them. Every duplicated key is a
            tie when no order column is given.
    """

    duplicated: list[str] = field(default_factory=list)
    ties: list[str] = field(default_factory=list)


def duplicate_report(
    rows: list[dict[str, str]],
    key_column: str,
    order_column: str | None = None,
    *,
    descending: bool = True,
) -> DuplicateReport:
    """Report the repeated keys of a table and which of them the order can't separate.

    Args:
        rows: Dict rows as returned by :func:`read_rows`.
        key_column: The join-key column.
        order_column: The column chosen to resolve repeats, or ``None``.
        descending: The chosen order direction.

    Returns:
        A :class:`DuplicateReport`.
    """
    ordered = order_rows(rows, order_column, descending=descending) if order_column else rows
    groups: dict[str, list[dict[str, str]]] = {}
    for row in ordered:
        key = (row.get(key_column) or "").strip()
        if key:
            groups.setdefault(key, []).append(row)
    first_seen = {}
    for row in rows:
        first_seen.setdefault((row.get(key_column) or "").strip(), None)
    report = DuplicateReport()
    for key in first_seen:
        group = groups.get(key, [])
        if len(group) < 2:
            continue
        report.duplicated.append(key)
        if order_column is None:
            report.ties.append(key)
        elif (group[0].get(order_column) or "").strip() == (
            group[1].get(order_column) or ""
        ).strip():
            report.ties.append(key)
    return report


@dataclass(slots=True, frozen=True)
class JoinSuggestion:
    """A proposed way to join a table to the videos.

    Attributes:
        column: The table column holding the ids.
        target: What the ids match: a target name passed to :func:`suggest_join`.
        matched: How many distinct ids of that column the target contains.
    """

    column: str
    target: str
    matched: int


def suggest_join(
    rows: list[dict[str, str]],
    header: list[str],
    targets: dict[str, set[str]],
    *,
    sample: int = 500,
) -> JoinSuggestion | None:
    """Propose the (id column, target) pair whose values overlap the most.

    A target is anything ids can match: all video ids, or the values of one nested-
    layout path field such as ``{subject}``. Ties go to the earlier column, then the
    earlier target, so the first target (the video id) wins when both fit equally.

    Args:
        rows: Dict rows as returned by :func:`read_rows`.
        header: The table's column names, in order.
        targets: Target name to the set of values it offers.
        sample: Maximum number of rows to inspect.

    Returns:
        The best :class:`JoinSuggestion`, or ``None`` when no column matches anything.
    """
    best: JoinSuggestion | None = None
    head = rows[:sample]
    for column in header:
        values = {(row.get(column) or "").strip() for row in head} - {""}
        for target, offered in targets.items():
            matched = len(values & offered)
            if matched and (best is None or matched > best.matched):
                best = JoinSuggestion(column, target, matched)
    return best


def short_name(header: str, limit: int = _SHORT_NAME_LIMIT) -> str:
    """Prefill for a tag name: the header on one line, cut at a word within ``limit``.

    Args:
        header: The column header (may be a long, multi-line question).
        limit: Maximum length of the result.

    Returns:
        The header with whitespace collapsed; when longer than ``limit``, cut back to
        the last whole word and marked with ``…``.
    """
    text = " ".join(header.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return f"{cut}…"


def distinct_column_values(rows: list[dict[str, str]], column: str) -> list[str]:
    """Return the distinct non-empty values of one column, in first-seen order.

    Used by the Browse id-list filter: a CSV supplies the set of video ids to keep,
    and the order is preserved so the preview shows them as the file lists them.

    Args:
        rows: Dict rows as returned by :func:`read_rows`.
        column: The column to collect.

    Returns:
        The distinct stripped values of ``column``, empties excluded.
    """
    seen: dict[str, None] = {}
    for row in rows:
        value = (row.get(column) or "").strip()
        if value:
            seen.setdefault(value, None)
    return list(seen)


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
