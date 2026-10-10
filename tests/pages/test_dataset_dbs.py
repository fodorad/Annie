"""Tests for the session-database listing and carrying progress into a saved config."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from annie.core.config import settings
from annie.dataset.storage import ReviewStore
from annie.pages import dataset


class TestSessionDbs(unittest.TestCase):
    def setUp(self) -> None:
        self._original = settings.sessions_dir
        self.tmp = Path(tempfile.mkdtemp())
        settings.sessions_dir = self.tmp

    def tearDown(self) -> None:
        settings.sessions_dir = self._original

    def _touch(self, name: str) -> None:
        (self.tmp / name).write_bytes(b"")
        time.sleep(0.01)  # keep mtimes strictly increasing for a stable order

    def test_renamed_first_then_timestamped_newest_first(self) -> None:
        # Created oldest → newest; the list must invert that within each group.
        self._touch("annie_2026-01-01_10-00-00.db")
        self._touch("annie_2026-05-05_12-00-00.db")
        self._touch("annie_2026-07-01_09-00-00.db")
        self._touch("my_review.db")
        self._touch("labels_pass1.db")

        order = [p.name for p in dataset._session_dbs()]

        self.assertEqual(order[:2], ["labels_pass1.db", "my_review.db"])  # renamed, newest-first
        self.assertEqual(
            order[2:],
            [
                "annie_2026-07-01_09-00-00.db",
                "annie_2026-05-05_12-00-00.db",
                "annie_2026-01-01_10-00-00.db",
            ],
        )

    def test_ignores_non_db_and_hidden_files(self) -> None:
        self._touch("keep.db")
        self._touch("notes.txt")
        self._touch(".hidden.db")

        self.assertEqual([p.name for p in dataset._session_dbs()], ["keep.db"])

    def test_missing_directory_is_empty(self) -> None:
        settings.sessions_dir = self.tmp / "does-not-exist"
        self.assertEqual(dataset._session_dbs(), [])


class TestCarryAction(unittest.TestCase):
    """Saving a config must never silently leave the user's progress behind."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.current = ReviewStore(self.tmp / "session.db")
        self.target = self.tmp / "annie_mydata.db"

    def test_same_database_needs_nothing(self) -> None:
        self.current.set_verdict("v::", "v", None, "bad")
        self.assertEqual(dataset._carry_action(self.current, self.current.db_path), "none")

    def test_no_progress_needs_nothing(self) -> None:
        self.assertEqual(dataset._carry_action(self.current, self.target), "none")

    def test_progress_into_new_database_is_copied(self) -> None:
        self.current.set_verdict("v::", "v", None, "bad")
        self.assertEqual(dataset._carry_action(self.current, self.target), "copy")

    def test_progress_into_empty_existing_database_is_copied(self) -> None:
        ReviewStore(self.target)
        self.current.set_verdict("v::", "v", None, "bad")
        self.assertEqual(dataset._carry_action(self.current, self.target), "copy")

    def test_progress_on_both_sides_asks(self) -> None:
        ReviewStore(self.target).set_verdict("old::", "old", None, "good")
        self.current.set_verdict("v::", "v", None, "bad")
        self.assertEqual(dataset._carry_action(self.current, self.target), "ask")


class TestSetAside(unittest.TestCase):
    def setUp(self) -> None:
        self._original = settings.sessions_dir
        self.tmp = Path(tempfile.mkdtemp())
        settings.sessions_dir = self.tmp / "sessions"

    def tearDown(self) -> None:
        settings.sessions_dir = self._original

    def test_moves_the_database_into_sessions_keeping_its_data(self) -> None:
        db = self.tmp / "annie_mydata.db"
        ReviewStore(db).set_verdict("old::", "old", None, "good")

        aside = dataset._set_aside(db)

        self.assertFalse(db.exists())
        self.assertEqual(aside.parent, settings.sessions_dir)
        self.assertTrue(aside.name.startswith("annie_mydata_replaced_"))
        self.assertIn(aside, dataset._session_dbs())
        self.assertIsNotNone(ReviewStore(aside).get("old::"))


if __name__ == "__main__":
    unittest.main()
