"""Tests for the in-memory + file event log."""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from annie.core.logbook import LogBook, report_exception


def _reset_events_logger() -> None:
    """Drop any file handler left on the shared ``annie.events`` logger by a prior test."""
    logger = logging.getLogger("annie.events")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


class TestLogBook(unittest.TestCase):
    def setUp(self) -> None:
        self.book = LogBook(capacity=5)

    def test_add_assigns_increasing_seq(self) -> None:
        a = self.book.add("info", "first")
        b = self.book.add("error", "second")
        self.assertEqual((a.seq, b.seq), (1, 2))
        self.assertEqual([e.message for e in self.book.events()], ["first", "second"])

    def test_since_returns_only_new(self) -> None:
        self.book.add("info", "a")
        _events, seq = self.book.since(0)
        self.book.add("error", "b")
        new, latest = self.book.since(seq)
        self.assertEqual([e.message for e in new], ["b"])
        self.assertEqual(latest, 2)

    def test_capacity_drops_oldest(self) -> None:
        for i in range(7):
            self.book.add("info", str(i))
        messages = [e.message for e in self.book.events()]
        self.assertEqual(messages, ["2", "3", "4", "5", "6"])  # maxlen=5

    def test_clear(self) -> None:
        self.book.add("info", "x")
        self.book.clear()
        self.assertEqual(self.book.events(), [])
        self.assertEqual(self.book.latest_seq(), 1)  # seq keeps counting

    def test_clipboard_text(self) -> None:
        event = self.book.add("error", "boom", "stack line 1\nstack line 2")
        text = event.as_clipboard()
        self.assertIn("ERROR: boom", text)
        self.assertIn("stack line 2", text)

    def test_attach_file_names_log_after_the_db(self) -> None:
        _reset_events_logger()
        tmp = Path(tempfile.mkdtemp())
        path = self.book.attach_file(tmp, "annie_2026-01-02_09-00-00")
        self.assertEqual(path.name, "annie_2026-01-02_09-00-00.log")  # same stem as the DB
        self.book.add("error", "to-file")
        self.assertIn("to-file", path.read_text(encoding="utf-8"))

    def test_retarget_renames_log_and_keeps_content(self) -> None:
        _reset_events_logger()
        tmp = Path(tempfile.mkdtemp())
        self.book.attach_file(tmp, "annie_ts")
        self.book.add("error", "before-rename")

        new = self.book.retarget(tmp.parent / "sessions" / "my_review.db")

        assert new is not None
        self.assertEqual(new.name, "my_review.log")  # follows the DB stem
        self.assertEqual(new.parent, tmp)  # stays in the log dir
        self.assertFalse((tmp / "annie_ts.log").exists())  # old file moved, not orphaned
        self.book.add("error", "after-rename")
        content = new.read_text(encoding="utf-8")
        self.assertIn("before-rename", content)  # content carried across
        self.assertIn("after-rename", content)

    def test_retarget_without_attached_file_is_noop(self) -> None:
        _reset_events_logger()
        self.assertIsNone(LogBook().retarget("/tmp/whatever.db"))


class TestReportException(unittest.TestCase):
    def test_details_contain_traceback(self) -> None:
        book_event = None
        try:
            raise ValueError("kaboom")
        except ValueError as exc:
            book_event = report_exception("caught it", exc)
        assert book_event is not None
        self.assertEqual(book_event.level, "error")
        self.assertIn("ValueError: kaboom", book_event.details)


if __name__ == "__main__":
    unittest.main()
