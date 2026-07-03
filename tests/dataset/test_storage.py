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


if __name__ == "__main__":
    unittest.main()
