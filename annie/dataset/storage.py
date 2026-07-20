"""SQLite-backed review-status store (Browse tab curation state).

This holds *curation* state per video — a good/bad verdict, an optional note, an
"add to annotator" flag, and the protagonist-track correction — plus, on export,
the protagonist ``_manual`` CSV. The store is a real file (by default a per-session
``~/.annie/sessions/annie_<timestamp>.db``; pin one with ``ANNIE_DB_PATH``) so it
survives restarts, and is keyed by :attr:`annie.models.VideoEntry.key`.

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

Decision = Literal["accept", "drop"]
"""A Segment-review decision on a clip. ``None`` means the clip is not yet reviewed."""

#: DDL for the single ``review`` table, created on first connection (idempotent).
#: ``row_key`` is free-form text, so segment rows key on ``{video_id}_{segment_id}``
#: without a schema change; ``decision`` carries the Segment-review accept/drop.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS review (
    row_key            TEXT PRIMARY KEY,
    video_id           TEXT NOT NULL,
    annotation_suffix  TEXT,
    verdict            TEXT,
    note               TEXT NOT NULL DEFAULT '',
    annotate           INTEGER NOT NULL DEFAULT 0,
    active_track       INTEGER,
    decision           TEXT,
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
        active_track: The session's protagonist-track override, or ``None`` if the
            reviewer has not corrected this video (the heuristic value then stands).
        decision: The Segment-review accept/drop for a clip row, or ``None`` if the
            clip is not (or not a) Segment-review sample.
        updated_at: ISO-8601 UTC timestamp of the last change.
    """

    row_key: str
    video_id: str
    annotation_suffix: str | None
    verdict: Verdict | None
    note: str
    annotate: bool
    active_track: int | None
    decision: Decision | None
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
        if "active_track" not in columns:
            conn.execute("ALTER TABLE review ADD COLUMN active_track INTEGER")
        if "decision" not in columns:
            conn.execute("ALTER TABLE review ADD COLUMN decision TEXT")

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
        active_track: int | None = None,
        decision: Decision | None = None,
    ) -> ReviewRecord:
        """Insert or update a review row, preserving fields left as ``None``.

        Passing ``verdict=None`` / ``note=None`` / ``annotate=None`` /
        ``active_track=None`` / ``decision=None`` keeps any existing value, so the
        good/bad toggle, the note, the annotator flag, the protagonist correction,
        and the Segment-review accept/drop update independently.

        Args:
            row_key: Stable row identity (primary key).
            video_id: The video id, stored for export/grouping.
            annotation_suffix: The annotation suffix, or ``None``.
            verdict: New verdict, or ``None`` to leave unchanged.
            note: New note, or ``None`` to leave unchanged.
            annotate: New annotator flag, or ``None`` to leave unchanged.
            active_track: New protagonist-track override, or ``None`` to leave
                unchanged.
            decision: New Segment-review decision, or ``None`` to leave unchanged.

        Returns:
            The resulting :class:`ReviewRecord`.
        """
        existing = self.get(row_key)
        new_verdict = (existing.verdict if existing else "good") if verdict is None else verdict
        new_note = (existing.note if existing else "") if note is None else (note or "")
        new_annotate = (
            (existing.annotate if existing else False) if annotate is None else bool(annotate)
        )
        new_active = (
            (existing.active_track if existing else None) if active_track is None else active_track
        )
        new_decision = (existing.decision if existing else None) if decision is None else decision
        record = ReviewRecord(
            row_key=row_key,
            video_id=video_id,
            annotation_suffix=annotation_suffix,
            verdict=new_verdict,
            note=new_note,
            annotate=new_annotate,
            active_track=new_active,
            decision=new_decision,
            updated_at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO review
                    (row_key, video_id, annotation_suffix, verdict, note, annotate,
                     active_track, decision, updated_at)
                VALUES (:row_key, :video_id, :annotation_suffix, :verdict, :note,
                        :annotate, :active_track, :decision, :updated_at)
                ON CONFLICT(row_key) DO UPDATE SET
                    video_id          = excluded.video_id,
                    annotation_suffix = excluded.annotation_suffix,
                    verdict           = excluded.verdict,
                    note              = excluded.note,
                    annotate          = excluded.annotate,
                    active_track      = excluded.active_track,
                    decision          = excluded.decision,
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
        active = existing.active_track if existing else None
        decision = existing.decision if existing else None
        record = ReviewRecord(
            row_key, video_id, annotation_suffix, verdict, note, annotate, active, decision, _now()
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO review
                    (row_key, video_id, annotation_suffix, verdict, note, annotate,
                     active_track, decision, updated_at)
                VALUES (:row_key, :video_id, :annotation_suffix, :verdict, :note,
                        :annotate, :active_track, :decision, :updated_at)
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

    def set_annotate_many(self, videos: Iterable[tuple[str, str]], value: bool) -> int:
        """Set the "add to annotator" flag for many videos in one transaction.

        Backs Browse's "Add all to Annotator" action: calling :meth:`set_annotate`
        per video would open a connection and commit for each one, which is slow on
        a large filtered selection. Only the ``annotate`` column is written, so any
        stored verdict, note, or protagonist correction survives; videos with no row
        yet get one with the defaults (liked, no note).

        Args:
            videos: ``(row_key, video_id)`` pairs to update.
            value: Whether the videos are queued for the Annotator tab.

        Returns:
            The number of videos written.
        """
        now = _now()
        rows = [
            {"row_key": key, "video_id": video_id, "annotate": bool(value), "updated_at": now}
            for key, video_id in videos
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO review
                    (row_key, video_id, annotation_suffix, verdict, note, annotate,
                     active_track, updated_at)
                VALUES (:row_key, :video_id, NULL, 'good', '', :annotate, NULL, :updated_at)
                ON CONFLICT(row_key) DO UPDATE SET
                    annotate   = excluded.annotate,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
        return len(rows)

    def set_active_track(
        self, row_key: str, video_id: str, annotation_suffix: str | None, track_id: int
    ) -> ReviewRecord:
        """Persist the reviewer's protagonist-track correction for a video.

        The choice lives only in this session's database; the ``_manual`` CSV is
        written separately by the Annotator's "Export corrected CSV" action.

        Args:
            row_key: Stable row identity.
            video_id: The video id.
            annotation_suffix: The annotation suffix, or ``None``.
            track_id: The chosen active track index.

        Returns:
            The updated :class:`ReviewRecord`.
        """
        return self.upsert(row_key, video_id, annotation_suffix, active_track=track_id)

    def active_tracks(self) -> dict[str, int]:
        """Return every stored protagonist override as ``row_key -> track_id``.

        Returns:
            A mapping of row key to corrected track index for the rows a reviewer
            has changed this session (rows without an override are omitted).
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT row_key, active_track FROM review WHERE active_track IS NOT NULL"
            ).fetchall()
        return {row["row_key"]: int(row["active_track"]) for row in rows}

    def annotator_keys(self) -> set[str]:
        """Return the row keys flagged for the Annotator tab.

        Returns:
            The set of ``row_key`` values whose ``annotate`` flag is set.
        """
        with self._connect() as conn:
            rows = conn.execute("SELECT row_key FROM review WHERE annotate = 1").fetchall()
        return {row["row_key"] for row in rows}

    def set_decision(self, row_key: str, video_id: str, decision: Decision) -> ReviewRecord:
        """Persist a Segment-review accept/drop decision for a clip.

        The clip is keyed by its composite ``{video_id}_{segment_id}`` ``row_key``,
        so re-opening the source resumes a half-finished pass from the database.

        Args:
            row_key: The clip identity (``{video_id}_{segment_id}``).
            video_id: The parent video id, stored for export/grouping.
            decision: ``"accept"`` or ``"drop"``.

        Returns:
            The updated :class:`ReviewRecord`.
        """
        return self.upsert(row_key, video_id, None, decision=decision)

    def clear_decision(self, row_key: str) -> None:
        """Return a clip to the *undecided* state, dropping any accept/drop.

        This is the "Undecided" escape hatch in Segment review: a misclick or a changed
        mind must be able to put a clip back into the undecided pool that the progress
        bar counts and "jump to next undecided" walks. It cannot go through
        :meth:`upsert`, where ``decision=None`` means "leave unchanged" — this writes the
        ``NULL`` explicitly. A clip that was never decided is left untouched.

        Args:
            row_key: The clip identity (``{video_id}_{segment_id}``).
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE review SET decision = NULL, updated_at = ? WHERE row_key = ?",
                (_now(), row_key),
            )

    def decisions(self) -> dict[str, Decision]:
        """Return every stored Segment-review decision as ``row_key -> decision``.

        Returns:
            A mapping of clip key to ``"accept"``/``"drop"`` for the clips a
            reviewer has decided (undecided clips are omitted).
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT row_key, decision FROM review WHERE decision IS NOT NULL"
            ).fetchall()
        return {row["row_key"]: row["decision"] for row in rows}

    def list_by_decision(self, decision: Decision) -> list[ReviewRecord]:
        """Return all records with the given Segment-review decision.

        Args:
            decision: ``"accept"`` or ``"drop"``.

        Returns:
            Matching review records ordered by row key (the accepted or dropped set).
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM review WHERE decision = ? ORDER BY row_key", (decision,)
            ).fetchall()
        return [_record_from_row(row) for row in rows]

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
            "active_track",
            "decision",
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
            raw_active = raw.get("active_track")
            try:
                active_track = int(str(raw_active)) if raw_active not in (None, "") else None
            except (TypeError, ValueError):
                active_track = None
            raw_decision = str(raw.get("decision") or "")
            decision: Decision | None = (
                "accept" if raw_decision == "accept" else "drop" if raw_decision == "drop" else None
            )
            self.upsert(
                str(raw["row_key"]),
                str(raw["video_id"]),
                (str(raw["annotation_suffix"]) if raw.get("annotation_suffix") else None),
                verdict=verdict,
                note=str(raw.get("note") or ""),
                annotate=_truthy(raw.get("annotate")),
                active_track=active_track,
                decision=decision,
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
        active_track=row["active_track"],
        decision=row["decision"],
        updated_at=row["updated_at"],
    )
