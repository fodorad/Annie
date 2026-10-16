"""Tests for reading Excel workbooks as label tables."""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import time
import unittest
from pathlib import Path

from annie.parsers.workbook import cell_text, is_workbook, read_sheet, sheet_names
from tests.fixtures import write_workbook as _save


class TestCellText(unittest.TestCase):
    def test_integral_float_is_an_integer(self) -> None:
        # Excel stores every number as a float; an id like 4021 must still join as "4021".
        self.assertEqual(cell_text(4021.0), "4021")
        self.assertEqual(cell_text(4021), "4021")

    def test_fractional_float_is_kept(self) -> None:
        self.assertEqual(cell_text(0.25), "0.25")

    def test_dates_and_times_are_iso(self) -> None:
        self.assertEqual(cell_text(dt.datetime(2024, 3, 1)), "2024-03-01")
        self.assertEqual(cell_text(dt.datetime(2024, 3, 1, 9, 5, 7)), "2024-03-01 09:05:07")
        self.assertEqual(cell_text(dt.date(2024, 3, 1)), "2024-03-01")
        self.assertEqual(cell_text(dt.time(0, 3, 19)), "00:03:19")

    def test_empty_bool_and_text(self) -> None:
        self.assertEqual(cell_text(None), "")
        self.assertEqual(cell_text(True), "true")
        self.assertEqual(cell_text("  spaced  "), "spaced")


class TestReadSheet(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_lists_sheets_in_workbook_order(self) -> None:
        path = _save(self.tmp / "book.xlsx", {"visits": [["id"]], "survey": [["id"]]})
        self.assertTrue(is_workbook(path))
        self.assertFalse(is_workbook(self.tmp / "labels.csv"))
        self.assertEqual(sheet_names(path), ["visits", "survey"])

    def test_reads_header_and_normalised_rows(self) -> None:
        path = _save(
            self.tmp / "book.xlsx",
            {"visits": [["id", "score", "seen"], [4021.0, 0.5, dt.datetime(2024, 3, 1)]]},
        )
        header, rows = read_sheet(path, "visits")
        self.assertEqual(header, ["id", "score", "seen"])
        self.assertEqual(rows, [{"id": "4021", "score": "0.5", "seen": "2024-03-01"}])

    def test_header_is_the_first_non_empty_row(self) -> None:
        # A legend-style sheet often starts a row or two down and a column in.
        path = _save(
            self.tmp / "book.xlsx",
            {"legend": [[], [None, "code", "meaning"], [None, 1, "yes"]]},
        )
        header, rows = read_sheet(path, "legend")
        self.assertEqual(header, ["Column A", "code", "meaning"])
        self.assertEqual(rows, [{"Column A": "", "code": "1", "meaning": "yes"}])

    def test_blank_and_duplicate_headers_get_unique_names(self) -> None:
        path = _save(self.tmp / "book.xlsx", {"s": [["id", None, "id"], [1, 2, 3]]})
        header, rows = read_sheet(path, "s")
        self.assertEqual(header, ["id", "Column B", "id (2)"])
        self.assertEqual(rows[0], {"id": "1", "Column B": "2", "id (2)": "3"})

    def test_empty_rows_are_skipped_and_wide_rows_clipped(self) -> None:
        path = _save(self.tmp / "book.xlsx", {"s": [["id"], [], [1, "extra"], [None]]})
        _header, rows = read_sheet(path, "s")
        self.assertEqual(rows, [{"id": "1"}])

    def test_first_sheet_when_none_is_named(self) -> None:
        path = _save(self.tmp / "book.xlsx", {"a": [["x"], [1]], "b": [["y"], [2]]})
        self.assertEqual(read_sheet(path, None)[0], ["x"])

    def test_missing_file_or_sheet_is_empty(self) -> None:
        path = _save(self.tmp / "book.xlsx", {"a": [["x"]]})
        self.assertEqual(read_sheet(self.tmp / "gone.xlsx", "a"), ([], []))
        self.assertEqual(read_sheet(path, "nope"), ([], []))
        self.assertEqual(sheet_names(self.tmp / "gone.xlsx"), [])

    def test_rereads_after_the_file_changes(self) -> None:
        path = _save(self.tmp / "book.xlsx", {"s": [["id"], [1]]})
        self.assertEqual(len(read_sheet(path, "s")[1]), 1)
        _save(path, {"s": [["id"], [1], [2]]})
        stamp = time.time() + 5  # a distinct mtime even on coarse-grained filesystems
        os.utime(path, (stamp, stamp))
        self.assertEqual(len(read_sheet(path, "s")[1]), 2)

    def test_callers_cannot_corrupt_the_cache(self) -> None:
        path = _save(self.tmp / "book.xlsx", {"s": [["id"], [1]]})
        _header, rows = read_sheet(path, "s")
        rows[0]["id"] = "changed"
        self.assertEqual(read_sheet(path, "s")[1][0]["id"], "1")


if __name__ == "__main__":
    unittest.main()
