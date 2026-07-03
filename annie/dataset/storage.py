"""SQLite-backed review-status store (Browse tab curation state).

This holds *curation* state per video — a good/bad verdict, an optional note, and
an "add to annotator" flag — not fine-grained annotation content (that lives in
the main-character ``_manual`` CSV and, later, per-video JSON). The store is a real
file (default ``~/.annie/annie.db``) so it survives restarts, and is keyed by
:attr:`annie.models.VideoEntry.key`.

Every video is **liked (good) by default**: a row only exists once the user
interacts, and the UI treats "no row" as good. The store is exportable to CSV/JSON
in one call and importable via upsert, so a reviewer's curation travels with them.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

Verdict = Literal["good", "bad"]
"""A review verdict. ``None`` (no row) is treated as ``"good"`` by the UI."""

#: DDL for the single ``review`` table, created on first connection (idempotent).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS review (
    row_key            TEXT PRIMARY KEY,
    video_id           TEXT NOT NULL,
    annotation_suffix  TEXT,
    verdict            TEXT,
    note               TEXT NOT NULL DEFAULT '',
    annotate           INTEGER NOT NULL DEFAULT 0,
    updated_at         TEXT NOT NULL
);
"""


@dataclass(slots=True)
class ReviewRecord:
    """One persisted review row.

    Attributes:
        row_key: Stable per-row identity (:attr:`annie.models.VideoEntry.key`).
        video_id: The video this review belongs to.
        annotation_suffix: The annotation suffix, or ``None`` for an exact match.
        verdict: ``"good"``, ``"bad"``, or ``None`` if untouched (treated as good).
        note: Free-text reviewer note (empty string when none).
        annotate: Whether the video is queued for the Annotator tab.
        updated_at: ISO-8601 UTC timestamp of the last change.
    """

    row_key: str
    video_id: str
    annotation_suffix: str | None
    verdict: Verdict | None
    note: str
    annotate: bool
    updated_at: str


def _now() -> str:
    """Return the current time as an ISO-8601 UTC string (seconds precision)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class ReviewStore:
    """A thin, connection-per-call wrapper over the ``review`` table.

    The store opens a fresh connection for each operation, which keeps it safe to
    call from the render worker threads and the UI thread alike without sharing a
    connection across threads.
    """

    def __init__(self, db_path: str | Path) -> None:
        """Open (creating if needed) the review database at ``db_path``.

        Args:
            db_path: Filesystem path to the SQLite file. Parent directories are
                created as needed.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns missing from a database created by an older Annie."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(review)")}
        if "annotate" not in columns:
            conn.execute("ALTER TABLE review ADD COLUMN annotate INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a row-factory connection inside a transaction, closing it after."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ── reads ──────────────────────────────────────────────────────────────────

    def get(self, row_key: str) -> ReviewRecord | None:
        """Return the review record for ``row_key``, or ``None`` if unreviewed.

        Args:
            row_key: The row identity to look up.

        Returns:
            The stored :class:`ReviewRecord`, or ``None``.
        """
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM review WHERE row_key = ?", (row_key,)).fetchone()
        return _record_from_row(row) if row is not None else None

    def all(self) -> list[ReviewRecord]:
        """Return every review record, ordered by row key.

        Returns:
            All stored review records.
        """
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM review ORDER BY row_key").fetchall()
        return [_record_from_row(row) for row in rows]

    def list_by_verdict(self, verdict: Verdict) -> list[ReviewRecord]:
        """Return all records with the given verdict (the good or bad list).

        Args:
            verdict: ``"good"`` or ``"bad"``.

        Returns:
            Matching review records ordered by row key.
        """
        with self._connect() as conn:
            if verdict == "good":
                # NULL verdict means "never explicitly set" → treated as good by convention.
                rows = conn.execute(
                    "SELECT * FROM review WHERE verdict = ? OR verdict IS NULL ORDER BY row_key",
                    (verdict,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM review WHERE verdict = ? ORDER BY row_key", (verdict,)
                ).fetchall()
        return [_record_from_row(row) for row in rows]

    # ── writes ─────────────────────────────────────────────────────────────────

    def upsert(
        self,
        row_key: str,
        video_id: str,
        annotation_suffix: str | None,
        *,
        verdict: Verdict | None = None,
        note: str | None = None,
        annotate: bool | None = None,
    ) -> ReviewRecord:
        """Insert or update a review row, preserving fields left as ``None``.

        Passing ``verdict=None`` / ``note=None`` / ``annotate=None`` keeps any
        existing value, so the good/bad toggle, the note, and the annotator flag
        update independently.

        Args:
            row_key: Stable row identity (primary key).
            video_id: The video id, stored for export/grouping.
            annotation_suffix: The annotation suffix, or ``None``.
            verdict: New verdict, or ``None`` to leave unchanged.
            note: New note, or ``None`` to leave unchanged.
            annotate: New annotator flag, or ``None`` to leave unchanged.

        Returns:
            The resulting :class:`ReviewRecord`.
        """
        existing = self.get(row_key)
        new_verdict = (existing.verdict if existing else "good") if verdict is None else verdict
        new_note = (existing.note if existing else "") if note is None else (note or "")
        new_annotate = (
            (existing.annotate if existing else False) if annotate is None else bool(annotate)
        )
        record = ReviewRecord(
            row_key=row_key,
            video_id=video_id,
            annotation_suffix=annotation_suffix,
            verdict=new_verdict,
            note=new_note,
            annotate=new_annotate,
            updated_at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO review
                    (row_key, video_id, annotation_suffix, verdict, note, annotate, updated_at)
                VALUES (:row_key, :video_id, :annotation_suffix, :verdict, :note,
                        :annotate, :updated_at)
                ON CONFLICT(row_key) DO UPDATE SET
                    video_id          = excluded.video_id,
                    annotation_suffix = excluded.annotation_suffix,
                    verdict           = excluded.verdict,
                    note              = excluded.note,
                    annotate          = excluded.annotate,
                    updated_at        = excluded.updated_at
                """,
                asdict(record),
            )
        return record

    def set_verdict(
        self, row_key: str, video_id: str, annotation_suffix: str | None, verdict: Verdict | None
    ) -> ReviewRecord:
        """Set (or clear) the good/bad verdict for a row.

        Args:
            row_key: Stable row identity.
            video_id: The video id.
            annotation_suffix: The annotation suffix, or ``None``.
            verdict: ``"good"``, ``"bad"``, or ``None`` to clear.

        Returns:
            The updated :class:`ReviewRecord`.
        """
        # Clearing requires a direct write because upsert treats None as "keep".
        existing = self.get(row_key)
        note = existing.note if existing else ""
        annotate = existing.annotate if existing else False
        record = ReviewRecord(row_key, video_id, annotation_suffix, verdict, note, annotate, _now())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO review
                    (row_key, video_id, annotation_suffix, verdict, note, annotate, updated_at)
                VALUES (:row_key, :video_id, :annotation_suffix, :verdict, :note,
                        :annotate, :updated_at)
                ON CONFLICT(row_key) DO UPDATE SET
                    verdict    = excluded.verdict,
                    updated_at = excluded.updated_at
                """,
                asdict(record),
            )
        return record

    def set_annotate(
        self, row_key: str, video_id: str, annotation_suffix: str | None, value: bool
    ) -> ReviewRecord:
        """Set the "add to annotator" flag for a video.

        Args:
            row_key: Stable row identity.
            video_id: The video id.
            annotation_suffix: The annotation suffix, or ``None``.
            value: Whether the video is queued for the Annotator tab.

        Returns:
            The updated :class:`ReviewRecord`.
        """
        return self.upsert(row_key, video_id, annotation_suffix, annotate=value)

    def annotator_keys(self) -> set[str]:
        """Return the row keys flagged for the Annotator tab.

        Returns:
            The set of ``row_key`` values whose ``annotate`` flag is set.
        """
        with self._connect() as conn:
            rows = conn.execute("SELECT row_key FROM review WHERE annotate = 1").fetchall()
        return {row["row_key"] for row in rows}

    def set_note(
        self, row_key: str, video_id: str, annotation_suffix: str | None, note: str
    ) -> ReviewRecord:
        """Set the free-text note for a row.

        Args:
            row_key: Stable row identity.
            video_id: The video id.
            annotation_suffix: The annotation suffix, or ``None``.
            note: The note text.

        Returns:
            The updated :class:`ReviewRecord`.
        """
        return self.upsert(row_key, video_id, annotation_suffix, note=note)

    # ── export / import ────────────────────────────────────────────────────────

    def export_json(self, path: str | Path) -> Path:
        """Write all review records to a JSON array file.

        Args:
            path: Destination JSON path.

        Returns:
            The path written.
        """
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps([asdict(r) for r in self.all()], indent=2), encoding="utf-8")
        return out

    def export_csv(self, path: str | Path) -> Path:
        """Write all review records to a CSV file.

        Args:
            path: Destination CSV path.

        Returns:
            The path written.
        """
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "row_key",
            "video_id",
            "annotation_suffix",
            "verdict",
            "note",
            "annotate",
            "updated_at",
        ]
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for record in self.all():
                writer.writerow(asdict(record))
        return out

    def import_records(self, records: Iterable[dict[str, object]]) -> int:
        """Upsert review records from dicts (e.g. a re-imported export).

        Args:
            records: Iterable of dicts with at least ``row_key`` and ``video_id``.

        Returns:
            The number of records imported.
        """
        count = 0
        for raw in records:
            raw_verdict = raw.get("verdict")
            verdict: Verdict | None = None
            if raw_verdict == "good":
                verdict = "good"
            elif raw_verdict == "bad":
                verdict = "bad"
            self.upsert(
                str(raw["row_key"]),
                str(raw["video_id"]),
                (str(raw["annotation_suffix"]) if raw.get("annotation_suffix") else None),
                verdict=verdict,
                note=str(raw.get("note") or ""),
                annotate=_truthy(raw.get("annotate")),
            )
            count += 1
        return count


def _truthy(value: object) -> bool:
    """Coerce a CSV/JSON cell (``"1"``, ``1``, ``True``, ``"true"``) to ``bool``."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def _record_from_row(row: sqlite3.Row) -> ReviewRecord:
    """Build a :class:`ReviewRecord` from a SQLite row."""
    return ReviewRecord(
        row_key=row["row_key"],
        video_id=row["video_id"],
        annotation_suffix=row["annotation_suffix"],
        verdict=row["verdict"],
        note=row["note"],
        annotate=bool(row["annotate"]),
        updated_at=row["updated_at"],
    )
