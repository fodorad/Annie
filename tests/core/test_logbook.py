"""Tests for the in-memory + file event log."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.core.logbook import LogBook, report_exception


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

    def test_attach_file_writes_dated_log(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        path = self.book.attach_file(tmp)
        self.assertTrue(path.name.startswith("Annie_") and path.suffix == ".log")
        self.book.add("error", "to-file")
        self.assertIn("to-file", path.read_text(encoding="utf-8"))


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
