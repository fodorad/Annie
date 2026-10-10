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
import io
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

Verdict = Literal["good", "bad"]
"""A review verdict. ``None`` (no row) is treated as ``"good"`` by the UI."""

Decision = Literal["accept", "drop"]
"""A Segment-review decision on a clip. ``None`` means the clip is not yet reviewed."""

#: DDL for the persisted tables, created on first connection (all idempotent).
#:
#: ``review`` is one row per video (curation, protagonist, segment decision); ``row_key``
#: is free-form text, so segment rows key on ``{video_id}_{segment_id}`` without a schema
#: change, and ``decision`` carries the Segment-review accept/drop.
#:
#: ``event``, ``event_track`` and ``event_category`` back the Event-annotation task, which is
#: a different shape: one video carries **many** interval events across **many** tracks. An
#: event is a ``[start_frame, end_frame]`` span on a named track (``event.track``), with a
#: free-text label/note and a JSON ``attributes`` object for arbitrary key/values — hence a
#: dedicated table rather than more columns on ``review``. ``event`` and the legacy per-video
#: ``event_track`` are keyed by :attr:`annie.core.models.VideoEntry.key` (``row_key``).
#:
#: ``event_category`` holds the **participant categories** (e.g. Mother / Baby) an event's
#: track names refer to. It is **DB-scoped, not per-video** (no ``row_key``), so the one set
#: is shared by every video of this dataset — and because each config carries its own DB, the
#: set is automatically per-dataset (a robot/human dataset's DB holds robot/human instead).
#: ``ordinal`` gives the lane's top-to-bottom order; ``color`` optionally overrides the
#: palette. It supersedes the per-video ``event_track`` as the source of timeline lanes;
#: ``event_track`` stays in the schema for back-compat but the UI no longer depends on it.
#: Opening a pre-events database simply creates these tables empty — no column migration is
#: needed for whole new tables.
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

CREATE TABLE IF NOT EXISTS event (
    event_id     TEXT PRIMARY KEY,
    video_id     TEXT NOT NULL,
    row_key      TEXT NOT NULL,
    track        TEXT NOT NULL,
    start_frame  INTEGER NOT NULL,
    end_frame    INTEGER NOT NULL,
    label        TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    attributes   TEXT NOT NULL DEFAULT '{}',
    color        TEXT,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS event_by_video ON event(row_key);

CREATE TABLE IF NOT EXISTS event_track (
    row_key     TEXT NOT NULL,
    track       TEXT NOT NULL,
    ordinal     INTEGER NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (row_key, track)
);

CREATE TABLE IF NOT EXISTS event_category (
    name        TEXT PRIMARY KEY,
    ordinal     INTEGER NOT NULL,
    color       TEXT,
    updated_at  TEXT NOT NULL
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


@dataclass(slots=True)
class EventRecord:
    """One persisted Event-annotation event: a labelled interval on a track.

    Attributes:
        event_id: Stable UUID identity, so an event survives edits to its own fields.
        video_id: The video this event belongs to.
        row_key: The :attr:`annie.core.models.VideoEntry.key` of the queue row, tying the
            event to the same identity curation uses.
        track: The track (category) name the event sits on.
        start_frame: Inclusive start frame of the interval.
        end_frame: Inclusive end frame of the interval (``>= start_frame``).
        label: Free-text label shown on the event box (empty when unnamed).
        note: Free-text note (empty when none).
        attributes: Arbitrary string key/value pairs; stored as a JSON object.
        color: Per-event colour override (hex string), or ``None`` to inherit the track's
            colour on the timeline.
        updated_at: ISO-8601 UTC timestamp of the last change.
    """

    event_id: str
    video_id: str
    row_key: str
    track: str
    start_frame: int
    end_frame: int
    label: str
    note: str
    attributes: dict[str, str] = field(default_factory=dict)
    color: str | None = None
    updated_at: str = ""


@dataclass(slots=True)
class EventCategory:
    """One participant category (a timeline lane) in the Event-annotation task.

    DB-scoped, not per-video: the categories in a review DB are shared by every video of that
    dataset. An event belongs to a category when its ``track`` equals the category ``name``.

    Attributes:
        name: The category / participant label (e.g. ``"Mother"``); unique within the DB.
        ordinal: Vertical lane order, ``0`` at the top.
        color: Optional hex colour override for the lane; ``None`` uses the palette by ordinal.
        updated_at: ISO-8601 UTC timestamp of the last change.
    """

    name: str
    ordinal: int
    color: str | None = None
    updated_at: str = ""


def _new_event_id() -> str:
    """Return a fresh UUID4 hex string for a new event."""
    return uuid.uuid4().hex


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
        # The event table already exists (CREATE IF NOT EXISTS ran); a database from the first
        # cut of the feature lacks the later per-event colour column.
        event_columns = {row["name"] for row in conn.execute("PRAGMA table_info(event)")}
        if "color" not in event_columns:
            conn.execute("ALTER TABLE event ADD COLUMN color TEXT")

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        """Yield a row-factory connection inside a transaction, closing it after."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ── whole-database operations ──────────────────────────────────────────────

    def has_data(self) -> bool:
        """Return whether any review, event, or participant category has been stored.

        Used to decide whether switching to another database would leave progress
        behind (see :meth:`copy_to`).
        """
        with self._connect() as conn:
            return any(
                conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None  # noqa: S608
                for table in ("review", "event", "event_category")
            )

    def copy_to(self, db_path: str | Path) -> ReviewStore:
        """Copy this whole database to ``db_path`` and return a store opened on the copy.

        Uses SQLite's online backup API, so the copy is consistent even while this store
        is in use. An existing file at ``db_path`` is overwritten; this database is left
        untouched.

        Args:
            db_path: Destination SQLite file. Parent directories are created as needed.

        Returns:
            A :class:`ReviewStore` backed by the copy.
        """
        target = Path(db_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(self.db_path)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        return ReviewStore(target)

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

    # ── event annotation ─────────────────────────────────────────────────────────

    def add_event(
        self,
        video_id: str,
        row_key: str,
        track: str,
        start_frame: int,
        end_frame: int,
        *,
        label: str = "",
        note: str = "",
        attributes: dict[str, str] | None = None,
        color: str | None = None,
    ) -> EventRecord:
        """Insert a new event on a track and return it (with its fresh id).

        The event's track is also registered in ``event_track`` if absent, so a lane
        created implicitly by dropping an event onto it persists like an explicit one.

        Args:
            video_id: The video the event belongs to.
            row_key: The queue row identity the event ties to.
            track: The track (category) name.
            start_frame: Inclusive start frame.
            end_frame: Inclusive end frame (``>= start_frame``).
            label: Optional event label.
            note: Optional event note.
            attributes: Optional arbitrary string key/value pairs.
            color: Optional per-event colour override (hex), or ``None`` to inherit the track.

        Returns:
            The stored :class:`EventRecord`.
        """
        record = EventRecord(
            event_id=_new_event_id(),
            video_id=video_id,
            row_key=row_key,
            track=track,
            start_frame=start_frame,
            end_frame=end_frame,
            label=label,
            note=note,
            attributes=dict(attributes or {}),
            color=color,
            updated_at=_now(),
        )
        with self._connect() as conn:
            self._ensure_track(conn, row_key, track)
            conn.execute(
                """
                INSERT INTO event
                    (event_id, video_id, row_key, track, start_frame, end_frame,
                     label, note, attributes, color, updated_at)
                VALUES (:event_id, :video_id, :row_key, :track, :start_frame, :end_frame,
                        :label, :note, :attributes, :color, :updated_at)
                """,
                _event_params(record),
            )
        return record

    def update_event(self, event_id: str, **fields: object) -> EventRecord | None:
        """Update named columns of an event, returning the updated record.

        Only the columns named in ``fields`` are written (plus ``updated_at``); any
        others keep their stored value. ``attributes`` is accepted as a dict and stored
        as JSON. Passing an unknown column raises, so a typo fails loudly rather than
        silently doing nothing.

        Args:
            event_id: The event to update.
            **fields: Column/value pairs among ``track``, ``start_frame``, ``end_frame``,
                ``label``, ``note``, ``attributes``, ``color``.

        Returns:
            The updated :class:`EventRecord`, or ``None`` if no such event exists.
        """
        allowed = {"track", "start_frame", "end_frame", "label", "note", "attributes", "color"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown event field(s): {sorted(unknown)}")
        if not fields:
            return self.get_event(event_id)
        assignments = {**fields}
        if "attributes" in assignments:
            assignments["attributes"] = json.dumps(assignments["attributes"])
        assignments["updated_at"] = _now()
        columns = ", ".join(f"{name} = :{name}" for name in assignments)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE event SET {columns} WHERE event_id = :event_id",  # noqa: S608 - keys are whitelisted
                {**assignments, "event_id": event_id},
            )
        return self.get_event(event_id)

    def delete_event(self, event_id: str) -> None:
        """Delete one event by id (a no-op if it does not exist).

        Args:
            event_id: The event to delete.
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM event WHERE event_id = ?", (event_id,))

    def get_event(self, event_id: str) -> EventRecord | None:
        """Return one event by id, or ``None`` if it does not exist.

        Args:
            event_id: The event to fetch.

        Returns:
            The stored :class:`EventRecord`, or ``None``.
        """
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM event WHERE event_id = ?", (event_id,)).fetchone()
        return _event_from_row(row) if row is not None else None

    def events_for(self, row_key: str) -> list[EventRecord]:
        """Return a video's events, ordered by track then start frame.

        Args:
            row_key: The queue row identity to fetch events for.

        Returns:
            The events on that video, grouped by track and sorted within each track.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM event WHERE row_key = ? ORDER BY track, start_frame, event_id",
                (row_key,),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def all_events(self) -> list[EventRecord]:
        """Return every stored event, ordered by video then track then start frame.

        Backs the session-wide export (every annotated video in one file).

        Returns:
            All events in the store.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM event ORDER BY row_key, track, start_frame, event_id"
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    @staticmethod
    def _ensure_track(conn: sqlite3.Connection, row_key: str, track: str) -> None:
        """Register a track for a video if it is not already present (append at the end)."""
        existing = conn.execute(
            "SELECT 1 FROM event_track WHERE row_key = ? AND track = ?", (row_key, track)
        ).fetchone()
        if existing is not None:
            return
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM event_track WHERE row_key = ?", (row_key,)
        ).fetchone()
        conn.execute(
            "INSERT INTO event_track (row_key, track, ordinal, updated_at) VALUES (?, ?, ?, ?)",
            (row_key, track, count, _now()),
        )

    def add_track(self, row_key: str, track: str) -> None:
        """Create an empty track (lane) for a video, appended after existing ones.

        Idempotent: re-adding an existing track leaves its ordinal untouched.

        Args:
            row_key: The queue row identity.
            track: The track (category) name.
        """
        with self._connect() as conn:
            self._ensure_track(conn, row_key, track)

    def rename_track(self, row_key: str, old: str, new: str) -> None:
        """Rename a track and move its events onto the new name, keeping the ordinal.

        A no-op when ``old`` and ``new`` are equal. If ``new`` already exists, the two
        lanes merge: ``old``'s events move onto ``new`` and the now-empty ``old`` lane
        is removed.

        Args:
            row_key: The queue row identity.
            old: The current track name.
            new: The new track name.
        """
        if old == new:
            return
        with self._connect() as conn:
            merging = conn.execute(
                "SELECT 1 FROM event_track WHERE row_key = ? AND track = ?", (row_key, new)
            ).fetchone()
            conn.execute(
                "UPDATE event SET track = ?, updated_at = ? WHERE row_key = ? AND track = ?",
                (new, _now(), row_key, old),
            )
            if merging is not None:
                conn.execute(
                    "DELETE FROM event_track WHERE row_key = ? AND track = ?", (row_key, old)
                )
            else:
                conn.execute(
                    "UPDATE event_track SET track = ?, updated_at = ? "
                    "WHERE row_key = ? AND track = ?",
                    (new, _now(), row_key, old),
                )

    def delete_track(self, row_key: str, track: str) -> None:
        """Delete a track and all of its events for a video.

        Args:
            row_key: The queue row identity.
            track: The track (category) name to remove.
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM event WHERE row_key = ? AND track = ?", (row_key, track))
            conn.execute(
                "DELETE FROM event_track WHERE row_key = ? AND track = ?", (row_key, track)
            )

    def tracks_for(self, row_key: str) -> list[str]:
        """Return a video's track names in their vertical order.

        Args:
            row_key: The queue row identity.

        Returns:
            The track names ordered by ``ordinal`` (their top-to-bottom lane order).
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT track FROM event_track WHERE row_key = ? ORDER BY ordinal, track",
                (row_key,),
            ).fetchall()
        return [row["track"] for row in rows]

    def event_row_keys(self) -> set[str]:
        """Return the row keys that have at least one event or track.

        Lets the tab know which queued videos already carry annotation work.

        Returns:
            The set of row keys present in either event table.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT row_key FROM event UNION SELECT row_key FROM event_track"
            ).fetchall()
        return {row["row_key"] for row in rows}

    # ── event categories (participant lanes, DB-scoped) ──────────────────────────

    def categories(self) -> list[EventCategory]:
        """Return the dataset's participant categories, in lane order.

        Returns:
            The categories ordered by ``ordinal`` then ``name`` (top to bottom).
        """
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM event_category ORDER BY ordinal, name").fetchall()
        return [_category_from_row(row) for row in rows]

    def add_category(self, name: str, color: str | None = None) -> EventCategory:
        """Add a category at the end of the lane order (idempotent on the name).

        Re-adding an existing name is a no-op that returns the existing category, so a
        double-click never creates a duplicate.

        Args:
            name: The category / participant label (must be non-empty; caller validates).
            color: Optional hex colour override for the lane.

        Returns:
            The stored :class:`EventCategory` (existing one if the name was already present).
        """
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM event_category WHERE name = ?", (name,)
            ).fetchone()
            if existing is not None:
                return _category_from_row(existing)
            (count,) = conn.execute("SELECT COUNT(*) FROM event_category").fetchone()
            record = EventCategory(name=name, ordinal=count, color=color, updated_at=_now())
            conn.execute(
                "INSERT INTO event_category (name, ordinal, color, updated_at) VALUES (?, ?, ?, ?)",
                (record.name, record.ordinal, record.color, record.updated_at),
            )
        return record

    def rename_category(self, old: str, new: str) -> None:
        """Rename a category and move every event on it onto the new name.

        A no-op when ``old == new``. If ``new`` already exists, the two lanes merge: ``old``'s
        events move onto ``new`` and the now-empty ``old`` category is removed. This is the
        safe way to fix a label everywhere at once — events follow, so nothing is orphaned.

        Args:
            old: The current category name.
            new: The new name.
        """
        if old == new:
            return
        with self._connect() as conn:
            merging = conn.execute("SELECT 1 FROM event_category WHERE name = ?", (new,)).fetchone()
            conn.execute(
                "UPDATE event SET track = ?, updated_at = ? WHERE track = ?",
                (new, _now(), old),
            )
            if merging is not None:
                conn.execute("DELETE FROM event_category WHERE name = ?", (old,))
            else:
                conn.execute(
                    "UPDATE event_category SET name = ?, updated_at = ? WHERE name = ?",
                    (new, _now(), old),
                )

    def delete_category(self, name: str) -> None:
        """Delete a category and all events on it, then close the ordinal gap.

        The caller is responsible for confirming when the category still holds events (the UI
        guards this); at the store level the delete always cascades to keep the data
        consistent — an event can never reference a category that no longer exists.

        Args:
            name: The category to remove.
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM event WHERE track = ?", (name,))
            conn.execute("DELETE FROM event_category WHERE name = ?", (name,))
            # Re-pack ordinals so they stay 0..n-1 with no gaps.
            remaining = conn.execute(
                "SELECT name FROM event_category ORDER BY ordinal, name"
            ).fetchall()
            for i, row in enumerate(remaining):
                conn.execute(
                    "UPDATE event_category SET ordinal = ? WHERE name = ?", (i, row["name"])
                )

    def category_event_count(self, name: str) -> int:
        """Return how many events currently sit on a category (for the delete guard).

        Args:
            name: The category name.

        Returns:
            The number of events whose ``track`` equals ``name``.
        """
        with self._connect() as conn:
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM event WHERE track = ?", (name,)
            ).fetchone()
        return int(count)

    def reorder_categories(self, names: list[str]) -> None:
        """Set the lane order to ``names`` (first = top). Unlisted categories keep going after.

        Args:
            names: The category names in the desired top-to-bottom order. Names not present in
                the DB are ignored; categories omitted from the list are appended after the
                listed ones in their previous relative order.
        """
        with self._connect() as conn:
            current = [
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM event_category ORDER BY ordinal, name"
                ).fetchall()
            ]
            ordered = [n for n in names if n in current]
            ordered += [n for n in current if n not in ordered]
            for i, name in enumerate(ordered):
                conn.execute(
                    "UPDATE event_category SET ordinal = ?, updated_at = ? WHERE name = ?",
                    (i, _now(), name),
                )

    def set_category_color(self, name: str, color: str | None) -> None:
        """Set (or clear) a category's lane colour override.

        Args:
            name: The category name.
            color: A hex colour, or ``None`` to fall back to the palette by ordinal.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE event_category SET color = ?, updated_at = ? WHERE name = ?",
                (color, _now(), name),
            )

    def seed_categories_from_events(self) -> int:
        """Seed the category table from distinct event track names, if it is empty.

        Back-compat for a DB annotated under the old single-lane model: its events sit on the
        ``"events"`` track (or whatever names were used), but there are no category rows yet.
        This creates one category per distinct existing track name so those events keep a lane
        the user can then rename to a participant. Does nothing once any category exists.

        Returns:
            The number of categories created (``0`` if the table was already populated or
            there are no events).
        """
        with self._connect() as conn:
            (existing,) = conn.execute("SELECT COUNT(*) FROM event_category").fetchone()
            if existing:
                return 0
            names = [
                row["track"]
                for row in conn.execute(
                    "SELECT DISTINCT track FROM event ORDER BY track"
                ).fetchall()
            ]
            for i, name in enumerate(names):
                conn.execute(
                    "INSERT INTO event_category (name, ordinal, color, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (name, i, None, _now()),
                )
        return len(names)

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

    def export_json_text(self) -> str:
        """Render all review records as a JSON array string."""
        return json.dumps([asdict(r) for r in self.all()], indent=2)

    def export_csv_text(self) -> str:
        """Render all review records as CSV text."""
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
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        for record in self.all():
            writer.writerow(asdict(record))
        return buffer.getvalue()

    def export_json(self, path: str | Path) -> Path:
        """Write all review records to a JSON array file.

        Args:
            path: Destination JSON path.

        Returns:
            The path written.
        """
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.export_json_text(), encoding="utf-8")
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
        with out.open("w", newline="", encoding="utf-8") as handle:
            handle.write(self.export_csv_text())
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


def _event_params(record: EventRecord) -> dict[str, object]:
    """Flatten an :class:`EventRecord` to SQL bind parameters (attributes → JSON)."""
    params = asdict(record)
    params["attributes"] = json.dumps(record.attributes)
    return params


def _event_from_row(row: sqlite3.Row) -> EventRecord:
    """Build an :class:`EventRecord` from a SQLite row (attributes ← JSON).

    A malformed ``attributes`` cell degrades to an empty dict rather than raising, so a
    hand-edited database can still be opened.
    """
    try:
        attributes = json.loads(row["attributes"]) if row["attributes"] else {}
    except (ValueError, TypeError):
        attributes = {}
    if not isinstance(attributes, dict):
        attributes = {}
    return EventRecord(
        event_id=row["event_id"],
        video_id=row["video_id"],
        row_key=row["row_key"],
        track=row["track"],
        start_frame=int(row["start_frame"]),
        end_frame=int(row["end_frame"]),
        label=row["label"],
        note=row["note"],
        attributes={str(k): str(v) for k, v in attributes.items()},
        color=row["color"],
        updated_at=row["updated_at"],
    )


def _category_from_row(row: sqlite3.Row) -> EventCategory:
    """Build an :class:`EventCategory` from a SQLite row."""
    return EventCategory(
        name=row["name"],
        ordinal=int(row["ordinal"]),
        color=row["color"],
        updated_at=row["updated_at"],
    )
