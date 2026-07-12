"""Tests for the "use existing DB" session-database listing order."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from annie.core.config import settings
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


if __name__ == "__main__":
    unittest.main()
