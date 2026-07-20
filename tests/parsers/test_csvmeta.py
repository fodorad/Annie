"""Tests for the generic CSV metadata parser."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.parsers.csvmeta import (
    count_rows,
    distinct_column_values,
    distinct_values,
    load_value_map,
    read_header,
    read_rows,
    suggest_key_column,
)
from tests.fixtures import write_table


class TestCsvMeta(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.path = write_table(
            self.tmp / "labels.csv",
            ["uuid", "Sentiment", "Angry"],
            [
                {"uuid": "A", "Sentiment": "negative", "Angry": "0.33"},
                {"uuid": "B", "Sentiment": "positive", "Angry": "0.00"},
                {"uuid": "C", "Sentiment": "negative", "Angry": "0.33"},
            ],
        )

    def test_read_header(self) -> None:
        self.assertEqual(read_header(self.path), ["uuid", "Sentiment", "Angry"])

    def test_read_header_missing_is_empty(self) -> None:
        self.assertEqual(read_header(self.tmp / "nope.csv"), [])

    def test_count_rows_excludes_header(self) -> None:
        self.assertEqual(count_rows(self.path), 3)
        self.assertEqual(count_rows(self.tmp / "nope.csv"), 0)

    def test_suggest_key_column_by_stem_match(self) -> None:
        self.assertEqual(suggest_key_column(self.path, {"A", "B", "C"}), "uuid")

    def test_suggest_key_column_falls_back_to_hint(self) -> None:
        # No stems match, but a column is named like an id.
        self.assertEqual(suggest_key_column(self.path, {"X", "Y"}), "uuid")

    def test_suggest_key_column_first_when_no_hint(self) -> None:
        path = write_table(self.tmp / "p.csv", ["alpha", "beta"], [{"alpha": "1", "beta": "2"}])
        self.assertEqual(suggest_key_column(path, None), "alpha")

    def test_load_value_map(self) -> None:
        mapping = load_value_map(self.path, "uuid", ("Sentiment", "Angry"))
        self.assertEqual(mapping["A"], {"Sentiment": "negative", "Angry": "0.33"})
        self.assertEqual(mapping["B"]["Sentiment"], "positive")

    def test_load_value_map_skips_blank_keys(self) -> None:
        path = write_table(
            self.tmp / "blank.csv",
            ["uuid", "v"],
            [{"uuid": "", "v": "x"}, {"uuid": "A", "v": "y"}],
        )
        self.assertEqual(set(load_value_map(path, "uuid", ("v",))), {"A"})

    def test_distinct_values(self) -> None:
        mapping = load_value_map(self.path, "uuid", ("Sentiment",))
        self.assertEqual(distinct_values(mapping, "Sentiment"), ["negative", "positive"])

    def test_distinct_column_values_keeps_file_order_and_drops_blanks(self) -> None:
        rows = read_rows(self.path) + [{"uuid": " A ", "Sentiment": ""}, {"uuid": "", "Angry": "1"}]
        self.assertEqual(distinct_column_values(rows, "uuid"), ["A", "B", "C"])
        self.assertEqual(distinct_column_values(rows, "Sentiment"), ["negative", "positive"])
        self.assertEqual(distinct_column_values(rows, "nope"), [])


if __name__ == "__main__":
    unittest.main()
