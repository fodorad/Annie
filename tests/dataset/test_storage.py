"""Tests for the SQLite review-status store."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from annie.dataset.storage import ReviewStore


class TestReviewStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = ReviewStore(self.tmp / "annie.db")

    def test_unknown_row_is_none(self) -> None:
        self.assertIsNone(self.store.get("missing::"))

    def test_set_and_get_verdict(self) -> None:
        self.store.set_verdict("v::track0", "v", "track0", "good")
        record = self.store.get("v::track0")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.verdict, "good")

    def test_clear_verdict(self) -> None:
        self.store.set_verdict("v::", "v", None, "bad")
        self.store.set_verdict("v::", "v", None, None)
        record = self.store.get("v::")
        assert record is not None
        self.assertIsNone(record.verdict)

    def test_note_and_verdict_are_independent(self) -> None:
        self.store.set_verdict("v::", "v", None, "good")
        self.store.set_note("v::", "v", None, "looks fine")
        record = self.store.get("v::")
        assert record is not None
        self.assertEqual(record.verdict, "good")  # preserved
        self.assertEqual(record.note, "looks fine")

    def test_list_by_verdict(self) -> None:
        self.store.set_verdict("a::", "a", None, "good")
        self.store.set_verdict("b::", "b", None, "bad")
        self.store.set_verdict("c::", "c", None, "good")
        good = self.store.list_by_verdict("good")
        self.assertEqual({r.video_id for r in good}, {"a", "c"})

    def test_list_by_verdict_good_includes_null(self) -> None:
        # A row with NULL verdict was never explicitly rated — it should appear in "good".
        self.store.set_verdict("a::", "a", None, "good")
        self.store.set_verdict("b::", "b", None, "bad")
        # Write c with a note only (no verdict → NULL in DB).
        self.store.set_note("c::", "c", None, "just a note")
        good = self.store.list_by_verdict("good")
        self.assertIn("c", {r.video_id for r in good})
        self.assertNotIn("b", {r.video_id for r in good})

    def test_export_json_and_csv(self) -> None:
        self.store.set_verdict("v::", "v", None, "good")
        json_path = self.store.export_json(self.tmp / "out.json")
        csv_path = self.store.export_csv(self.tmp / "out.csv")
        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(data[0]["verdict"], "good")
        self.assertIn("row_key", csv_path.read_text(encoding="utf-8"))

    def test_import_records_round_trip(self) -> None:
        self.store.set_verdict("v::", "v", None, "good")
        self.store.set_note("v::", "v", None, "note")
        exported = json.loads(self.store.export_json(self.tmp / "o.json").read_text("utf-8"))

        fresh = ReviewStore(self.tmp / "fresh.db")
        count = fresh.import_records(exported)
        self.assertEqual(count, 1)
        record = fresh.get("v::")
        assert record is not None
        self.assertEqual(record.note, "note")
        self.assertEqual(record.verdict, "good")

    def test_persists_across_instances(self) -> None:
        self.store.set_verdict("v::", "v", None, "bad")
        reopened = ReviewStore(self.tmp / "annie.db")
        record = reopened.get("v::")
        assert record is not None
        self.assertEqual(record.verdict, "bad")

    def test_annotate_flag_and_keys(self) -> None:
        self.store.set_annotate("a::", "a", None, True)
        self.store.set_annotate("b::", "b", None, True)
        self.store.set_annotate("b::", "b", None, False)
        self.assertEqual(self.store.annotator_keys(), {"a::"})
        record = self.store.get("a::")
        assert record is not None
        self.assertTrue(record.annotate)

    def test_annotate_preserves_verdict_and_note(self) -> None:
        self.store.set_verdict("v::", "v", None, "bad")
        self.store.set_note("v::", "v", None, "crop")
        self.store.set_annotate("v::", "v", None, True)
        record = self.store.get("v::")
        assert record is not None
        self.assertEqual(record.verdict, "bad")
        self.assertEqual(record.note, "crop")
        self.assertTrue(record.annotate)

    def test_dequeue_preserves_verdict_and_note(self) -> None:
        """The Annotator's X button clears `annotate` without touching curation."""
        self.store.set_verdict("v::", "v", None, "bad")
        self.store.set_note("v::", "v", None, "crop")
        self.store.set_annotate("v::", "v", None, True)

        self.store.set_annotate("v::", "v", None, False)

        record = self.store.get("v::")
        assert record is not None
        self.assertFalse(record.annotate)
        self.assertEqual(record.verdict, "bad")
        self.assertEqual(record.note, "crop")
        self.assertEqual(self.store.annotator_keys(), set())

    def test_export_includes_annotate(self) -> None:
        self.store.set_annotate("v::", "v", None, True)
        data = json.loads(self.store.export_json(self.tmp / "o.json").read_text("utf-8"))
        self.assertTrue(data[0]["annotate"])

    def test_migrates_legacy_database_without_annotate(self) -> None:
        legacy = self.tmp / "legacy.db"
        with sqlite3.connect(legacy) as conn:
            conn.execute(
                "CREATE TABLE review (row_key TEXT PRIMARY KEY, video_id TEXT NOT NULL, "
                "annotation_suffix TEXT, verdict TEXT, note TEXT NOT NULL DEFAULT '', "
                "updated_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO review VALUES ('v::', 'v', NULL, 'bad', 'old', '2020-01-01T00:00:00')"
            )
        store = ReviewStore(legacy)  # opening triggers the migration
        record = store.get("v::")
        assert record is not None
        self.assertFalse(record.annotate)
        store.set_annotate("v::", "v", None, True)
        self.assertEqual(store.annotator_keys(), {"v::"})

    def test_active_track_only_lists_overridden_rows(self) -> None:
        self.store.set_active_track("a", "a", None, 3)
        self.store.set_active_track("b", "b", None, 7)
        self.store.set_active_track("b", "b", None, 2)  # a later choice wins
        self.store.set_verdict("c", "c", None, "bad")  # no override → not listed
        self.assertEqual(self.store.active_tracks(), {"a": 3, "b": 2})

    def test_active_track_survives_other_writes(self) -> None:
        self.store.set_active_track("a", "a", None, 5)
        self.store.set_verdict("a", "a", None, "bad")  # unrelated field
        self.store.set_annotate("a", "a", None, True)
        self.assertEqual(self.store.active_tracks(), {"a": 5})

    def test_set_annotate_many_queues_every_video(self) -> None:
        written = self.store.set_annotate_many([("a", "a"), ("b", "b"), ("c", "c")], value=True)
        self.assertEqual(written, 3)
        self.assertEqual(self.store.annotator_keys(), {"a", "b", "c"})

        self.store.set_annotate_many([("a", "a"), ("b", "b")], value=False)
        self.assertEqual(self.store.annotator_keys(), {"c"})

    def test_set_annotate_many_preserves_other_fields(self) -> None:
        self.store.set_verdict("a", "a", None, "bad")
        self.store.set_note("a", "a", None, "shaky")
        self.store.set_active_track("a", "a", None, 3)

        self.store.set_annotate_many([("a", "a")], value=True)

        record = self.store.get("a")
        assert record is not None
        self.assertEqual((record.verdict, record.note, record.active_track), ("bad", "shaky", 3))
        self.assertTrue(record.annotate)

    def test_set_annotate_many_with_no_videos(self) -> None:
        self.assertEqual(self.store.set_annotate_many([], value=True), 0)
        self.assertEqual(self.store.annotator_keys(), set())

    def test_set_and_get_decision(self) -> None:
        self.store.set_decision("227426_15", "227426", "accept")
        record = self.store.get("227426_15")
        assert record is not None
        self.assertEqual(record.decision, "accept")

    def test_decision_only_lists_decided_rows(self) -> None:
        self.store.set_decision("a_0", "a", "accept")
        self.store.set_decision("b_1", "b", "drop")
        self.store.set_verdict("c_2", "c", None, "good")  # no decision → not listed
        self.assertEqual(self.store.decisions(), {"a_0": "accept", "b_1": "drop"})

    def test_list_by_decision(self) -> None:
        self.store.set_decision("a_0", "a", "accept")
        self.store.set_decision("b_1", "b", "drop")
        self.store.set_decision("c_2", "c", "accept")
        accepted = self.store.list_by_decision("accept")
        self.assertEqual({r.row_key for r in accepted}, {"a_0", "c_2"})

    def test_decision_can_be_changed(self) -> None:
        self.store.set_decision("a_0", "a", "accept")
        self.store.set_decision("a_0", "a", "drop")  # reviewer flips it
        self.assertEqual(self.store.decisions(), {"a_0": "drop"})

    def test_clear_decision_returns_a_clip_to_undecided(self) -> None:
        self.store.set_decision("a_0", "a", "accept")
        self.store.clear_decision("a_0")
        # Gone from the decided set, so the progress bar and jump-to-undecided see it.
        self.assertEqual(self.store.decisions(), {})
        record = self.store.get("a_0")
        assert record is not None
        self.assertIsNone(record.decision)

    def test_clear_decision_keeps_the_rows_other_fields(self) -> None:
        self.store.set_decision("a_0", "a", "accept")
        self.store.set_note("a_0", "a", None, "clean cut")
        self.store.clear_decision("a_0")
        record = self.store.get("a_0")
        assert record is not None
        self.assertIsNone(record.decision)
        self.assertEqual(record.note, "clean cut")  # only the verdict is dropped

    def test_set_verdict_preserves_a_stored_decision(self) -> None:
        """Curating a clip's good/bad verdict must not disturb its accept/drop.

        The two are independent axes on one row. This holds today via the ``ON CONFLICT``
        branch, which simply does not touch ``decision``; the guard matters because
        ``set_verdict`` builds a full ``ReviewRecord`` carrying the existing decision
        forward, so it reads as though the INSERT persists it. Anyone extending that
        conflict clause would otherwise have to notice the mismatch unaided.
        """
        self.store.set_decision("a_0", "a", "accept")
        self.store.set_verdict("a_0", "a", None, "bad")
        record = self.store.get("a_0")
        assert record is not None
        self.assertEqual(record.verdict, "bad")
        self.assertEqual(record.decision, "accept")

    def test_set_verdict_on_a_fresh_row_leaves_the_decision_null(self) -> None:
        self.store.set_verdict("fresh", "a", None, "good")
        record = self.store.get("fresh")
        assert record is not None
        self.assertIsNone(record.decision)

    def test_clear_decision_on_an_undecided_row_is_a_no_op(self) -> None:
        self.store.clear_decision("never_seen")
        self.assertEqual(self.store.decisions(), {})
        self.assertIsNone(self.store.get("never_seen"))

    def test_decision_survives_other_writes(self) -> None:
        self.store.set_decision("a_0", "a", "accept")
        self.store.set_note("a_0", "a", None, "clean cut")
        record = self.store.get("a_0")
        assert record is not None
        self.assertEqual(record.decision, "accept")
        self.assertEqual(record.note, "clean cut")

    def test_export_includes_decision(self) -> None:
        self.store.set_decision("a_0", "a", "accept")
        data = json.loads(self.store.export_json(self.tmp / "o.json").read_text("utf-8"))
        self.assertEqual(data[0]["decision"], "accept")
        self.assertIn("decision", self.store.export_csv(self.tmp / "o.csv").read_text("utf-8"))

    def test_import_round_trips_decision(self) -> None:
        self.store.set_decision("a_0", "a", "drop")
        exported = json.loads(self.store.export_json(self.tmp / "o.json").read_text("utf-8"))
        fresh = ReviewStore(self.tmp / "fresh_dec.db")
        fresh.import_records(exported)
        self.assertEqual(fresh.decisions(), {"a_0": "drop"})

    def test_migrates_legacy_database_without_decision(self) -> None:
        legacy = self.tmp / "legacy_no_decision.db"
        with sqlite3.connect(legacy) as conn:
            conn.execute(
                "CREATE TABLE review (row_key TEXT PRIMARY KEY, video_id TEXT NOT NULL, "
                "annotation_suffix TEXT, verdict TEXT, note TEXT NOT NULL DEFAULT '', "
                "annotate INTEGER NOT NULL DEFAULT 0, active_track INTEGER, "
                "updated_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO review VALUES "
                "('v::', 'v', NULL, 'good', '', 0, NULL, '2020-01-01T00:00:00')"
            )
        store = ReviewStore(legacy)  # opening adds the decision column
        self.assertEqual(store.decisions(), {})
        store.set_decision("a_0", "a", "accept")
        self.assertEqual(store.decisions(), {"a_0": "accept"})

    def test_migrates_legacy_database_without_active_track(self) -> None:
        legacy = self.tmp / "legacy_no_active.db"
        with sqlite3.connect(legacy) as conn:
            conn.execute(
                "CREATE TABLE review (row_key TEXT PRIMARY KEY, video_id TEXT NOT NULL, "
                "annotation_suffix TEXT, verdict TEXT, note TEXT NOT NULL DEFAULT '', "
                "annotate INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO review VALUES ('v::', 'v', NULL, 'good', '', 0, '2020-01-01T00:00:00')"
            )
        store = ReviewStore(legacy)  # opening adds the active_track column
        self.assertEqual(store.active_tracks(), {})
        store.set_active_track("v::", "v", None, 4)
        self.assertEqual(store.active_tracks(), {"v::": 4})


class TestEventStore(unittest.TestCase):
    """Event-annotation table: CRUD, tracks, attributes, and pre-events migration."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = ReviewStore(self.tmp / "annie.db")

    def test_add_and_fetch_event(self) -> None:
        event = self.store.add_event("v", "v::", "speech", 10, 40, label="A")
        self.assertEqual(len(event.event_id), 32)  # uuid4 hex
        fetched = self.store.get_event(event.event_id)
        assert fetched is not None
        self.assertEqual(fetched.track, "speech")
        self.assertEqual((fetched.start_frame, fetched.end_frame), (10, 40))
        self.assertEqual(fetched.label, "A")

    def test_add_event_registers_its_track(self) -> None:
        self.store.add_event("v", "v::", "speech", 0, 5)
        self.assertEqual(self.store.tracks_for("v::"), ["speech"])

    def test_events_for_orders_by_track_then_start(self) -> None:
        self.store.add_event("v", "v::", "speech", 50, 60)
        self.store.add_event("v", "v::", "speech", 10, 20)
        self.store.add_event("v", "v::", "gesture", 30, 40)
        got = [(e.track, e.start_frame) for e in self.store.events_for("v::")]
        self.assertEqual(got, [("gesture", 30), ("speech", 10), ("speech", 50)])

    def test_update_event_changes_only_named_fields(self) -> None:
        event = self.store.add_event("v", "v::", "speech", 10, 40, label="A", note="n")
        self.store.update_event(event.event_id, end_frame=55, label="B")
        got = self.store.get_event(event.event_id)
        assert got is not None
        self.assertEqual((got.end_frame, got.label), (55, "B"))
        self.assertEqual((got.start_frame, got.note), (10, "n"))  # untouched

    def test_update_event_rejects_unknown_field(self) -> None:
        event = self.store.add_event("v", "v::", "speech", 0, 5)
        with self.assertRaises(ValueError):
            self.store.update_event(event.event_id, bogus=1)

    def test_update_missing_event_returns_none(self) -> None:
        self.assertIsNone(self.store.update_event("nope", label="X"))

    def test_attributes_round_trip_including_unicode(self) -> None:
        event = self.store.add_event(
            "v", "v::", "speech", 0, 5, attributes={"speaker": "Á", "intensity": "high"}
        )
        got = self.store.get_event(event.event_id)
        assert got is not None
        self.assertEqual(got.attributes, {"speaker": "Á", "intensity": "high"})

    def test_update_attributes(self) -> None:
        event = self.store.add_event("v", "v::", "speech", 0, 5, attributes={"a": "1"})
        self.store.update_event(event.event_id, attributes={"a": "2", "b": "3"})
        got = self.store.get_event(event.event_id)
        assert got is not None
        self.assertEqual(got.attributes, {"a": "2", "b": "3"})

    def test_delete_event(self) -> None:
        event = self.store.add_event("v", "v::", "speech", 0, 5)
        self.store.delete_event(event.event_id)
        self.assertIsNone(self.store.get_event(event.event_id))

    def test_add_track_appends_and_is_idempotent(self) -> None:
        self.store.add_track("v::", "speech")
        self.store.add_track("v::", "gesture")
        self.store.add_track("v::", "speech")  # no reorder, no duplicate
        self.assertEqual(self.store.tracks_for("v::"), ["speech", "gesture"])

    def test_delete_track_cascades_to_its_events(self) -> None:
        keep = self.store.add_event("v", "v::", "gesture", 0, 5)
        self.store.add_event("v", "v::", "speech", 0, 5)
        self.store.delete_track("v::", "speech")
        self.assertEqual(self.store.tracks_for("v::"), ["gesture"])
        remaining = [e.event_id for e in self.store.events_for("v::")]
        self.assertEqual(remaining, [keep.event_id])

    def test_rename_track_moves_its_events(self) -> None:
        event = self.store.add_event("v", "v::", "speech", 0, 5)
        self.store.rename_track("v::", "speech", "dialogue")
        self.assertEqual(self.store.tracks_for("v::"), ["dialogue"])
        got = self.store.get_event(event.event_id)
        assert got is not None
        self.assertEqual(got.track, "dialogue")

    def test_rename_track_into_existing_merges(self) -> None:
        self.store.add_event("v", "v::", "a", 0, 5)
        self.store.add_event("v", "v::", "b", 10, 15)
        self.store.rename_track("v::", "a", "b")  # merge a into b
        self.assertEqual(self.store.tracks_for("v::"), ["b"])
        self.assertEqual(len(self.store.events_for("v::")), 2)

    def test_rename_track_to_same_name_is_noop(self) -> None:
        self.store.add_event("v", "v::", "speech", 0, 5)
        self.store.rename_track("v::", "speech", "speech")
        self.assertEqual(self.store.tracks_for("v::"), ["speech"])

    def test_empty_track_persists_without_events(self) -> None:
        self.store.add_track("v::", "planned")
        reopened = ReviewStore(self.tmp / "annie.db")
        self.assertEqual(reopened.tracks_for("v::"), ["planned"])

    def test_event_row_keys_lists_annotated_videos(self) -> None:
        self.store.add_event("v", "v::", "speech", 0, 5)
        self.store.add_track("w::", "empty")  # track only, no events — still counts
        self.assertEqual(self.store.event_row_keys(), {"v::", "w::"})

    def test_all_events_spans_videos(self) -> None:
        self.store.add_event("v", "v::", "speech", 0, 5)
        self.store.add_event("w", "w::", "speech", 0, 5)
        self.assertEqual({e.video_id for e in self.store.all_events()}, {"v", "w"})

    def test_events_persist_across_instances(self) -> None:
        self.store.add_event("v", "v::", "speech", 3, 9, label="hi")
        reopened = ReviewStore(self.tmp / "annie.db")
        events = reopened.events_for("v::")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].label, "hi")

    def test_event_color_round_trips(self) -> None:
        rec = self.store.add_event("v", "v::", "speech", 0, 5, color="#a8dadc")
        got = self.store.get_event(rec.event_id)
        assert got is not None
        self.assertEqual(got.color, "#a8dadc")

    def test_event_color_defaults_none_and_updates(self) -> None:
        rec = self.store.add_event("v", "v::", "speech", 0, 5)
        self.assertIsNone(self.store.get_event(rec.event_id).color)  # type: ignore[union-attr]
        self.store.update_event(rec.event_id, color="#ffb4a2")
        self.assertEqual(self.store.get_event(rec.event_id).color, "#ffb4a2")  # type: ignore[union-attr]
        self.store.update_event(rec.event_id, color=None)  # reset to the track colour
        self.assertIsNone(self.store.get_event(rec.event_id).color)  # type: ignore[union-attr]

    def test_migrates_events_table_without_color(self) -> None:
        legacy = self.tmp / "events_no_color.db"
        with sqlite3.connect(legacy) as conn:
            # A first-cut events table: no `color` column yet.
            conn.execute(
                "CREATE TABLE event (event_id TEXT PRIMARY KEY, video_id TEXT NOT NULL, "
                "row_key TEXT NOT NULL, track TEXT NOT NULL, start_frame INTEGER NOT NULL, "
                "end_frame INTEGER NOT NULL, label TEXT NOT NULL DEFAULT '', "
                "note TEXT NOT NULL DEFAULT '', attributes TEXT NOT NULL DEFAULT '{}', "
                "updated_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO event VALUES ('e', 'v', 'v::', 'speech', 0, 5, 'x', '', '{}', "
                "'2020-01-01T00:00:00')"
            )
        store = ReviewStore(legacy)  # opening adds the color column
        got = store.get_event("e")
        assert got is not None
        self.assertIsNone(got.color)
        store.update_event("e", color="#b5e48c")
        self.assertEqual(store.get_event("e").color, "#b5e48c")  # type: ignore[union-attr]

    def test_opening_pre_events_database_creates_tables_empty(self) -> None:
        legacy = self.tmp / "pre_events.db"
        with sqlite3.connect(legacy) as conn:
            conn.execute(
                "CREATE TABLE review (row_key TEXT PRIMARY KEY, video_id TEXT NOT NULL, "
                "annotation_suffix TEXT, verdict TEXT, note TEXT NOT NULL DEFAULT '', "
                "annotate INTEGER NOT NULL DEFAULT 0, active_track INTEGER, decision TEXT, "
                "updated_at TEXT NOT NULL)"
            )
        store = ReviewStore(legacy)  # opening creates the event tables
        self.assertEqual(store.events_for("v::"), [])
        self.assertEqual(store.tracks_for("v::"), [])
        # And they are usable straight away.
        store.add_event("v", "v::", "speech", 0, 5)
        self.assertEqual(len(store.events_for("v::")), 1)


if __name__ == "__main__":
    unittest.main()
