"""Tests for the generic CSV metadata parser."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.parsers.csvmeta import (
    count_rows,
    distinct_column_values,
    distinct_values,
    duplicate_report,
    load_value_map,
    order_rows,
    read_header,
    read_rows,
    short_name,
    suggest_join,
    suggest_key_column,
)
from tests.fixtures import write_table, write_workbook


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


class TestWorkbookDispatch(unittest.TestCase):
    """The table API reads a named worksheet when given a workbook."""

    def setUp(self) -> None:
        self.path = write_workbook(
            Path(tempfile.mkdtemp()) / "book.xlsx",
            {
                "notes": [["note"], ["x"]],
                "visits": [["subject", "score"], [4021.0, 0.5], [4022.0, 0.75]],
            },
        )

    def test_header_rows_and_count_of_a_sheet(self) -> None:
        self.assertEqual(read_header(self.path, sheet="visits"), ["subject", "score"])
        self.assertEqual(
            read_rows(self.path, sheet="visits")[0], {"subject": "4021", "score": "0.5"}
        )
        self.assertEqual(count_rows(self.path, sheet="visits"), 2)

    def test_first_sheet_by_default(self) -> None:
        self.assertEqual(read_header(self.path), ["note"])

    def test_value_map_and_key_suggestion_from_a_sheet(self) -> None:
        values = load_value_map(self.path, "subject", ("score",), sheet="visits")
        self.assertEqual(values, {"4021": {"score": "0.5"}, "4022": {"score": "0.75"}})
        self.assertEqual(suggest_key_column(self.path, {"4022"}, sheet="visits"), "subject")


class TestDuplicates(unittest.TestCase):
    """A repeated id is resolved by an explicit order column, never by file position."""

    ROWS = [
        {"id": "a", "when": "2024-01-02", "v": "old"},
        {"id": "a", "when": "2024-03-01", "v": "new"},
        {"id": "b", "when": "", "v": "undated"},
        {"id": "b", "when": "2024-02-01", "v": "dated"},
    ]

    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.path = write_table(tmp / "l.csv", ["id", "when", "v"], self.ROWS)

    def test_descending_order_keeps_the_newest_row(self) -> None:
        values = load_value_map(self.path, "id", ("v",), order_column="when", descending=True)
        self.assertEqual(values, {"a": {"v": "new"}, "b": {"v": "dated"}})

    def test_ascending_order_keeps_the_oldest_row(self) -> None:
        values = load_value_map(self.path, "id", ("v",), order_column="when", descending=False)
        # An empty order value never wins, whichever the direction.
        self.assertEqual(values, {"a": {"v": "old"}, "b": {"v": "dated"}})

    def test_numbers_order_numerically(self) -> None:
        rows = [{"n": "9"}, {"n": "10"}, {"n": "2"}]
        self.assertEqual([r["n"] for r in order_rows(rows, "n", descending=True)], ["10", "9", "2"])

    def test_text_orders_lexically_and_ties_keep_file_order(self) -> None:
        rows = [{"k": "b", "i": "1"}, {"k": "a", "i": "2"}, {"k": "b", "i": "3"}]
        ordered = order_rows(rows, "k", descending=True)
        self.assertEqual([r["i"] for r in ordered], ["1", "3", "2"])

    def test_report_lists_duplicates_and_ties(self) -> None:
        rows = [*self.ROWS, {"id": "c", "when": "2024-05-05"}, {"id": "c", "when": "2024-05-05"}]
        report = duplicate_report(rows, "id", "when")
        self.assertEqual(report.duplicated, ["a", "b", "c"])
        self.assertEqual(report.ties, ["c"])  # equal order values: only file order decides

    def test_report_without_duplicates(self) -> None:
        report = duplicate_report([{"id": "a"}, {"id": "b"}, {"id": ""}], "id")
        self.assertEqual((report.duplicated, report.ties), ([], []))

    def test_without_an_order_column_ties_are_every_duplicate(self) -> None:
        self.assertEqual(duplicate_report(self.ROWS, "id").ties, ["a", "b"])


class TestSuggestJoin(unittest.TestCase):
    """The dialog proposes the id column and what it matches: the video id or a path field."""

    def test_prefers_the_target_with_the_most_matches(self) -> None:
        rows = [{"rec": "s1", "subject": "4021"}, {"rec": "s2", "subject": "4022"}]
        targets = {"video id": {"4021_a", "4022_a"}, "subject": {"4021", "4022"}}
        found = suggest_join(rows, ["rec", "subject"], targets)
        assert found is not None
        self.assertEqual((found.column, found.target, found.matched), ("subject", "subject", 2))

    def test_no_match_is_none(self) -> None:
        self.assertIsNone(suggest_join([{"x": "1"}], ["x"], {"video id": {"v"}}))


class TestShortName(unittest.TestCase):
    def test_short_header_is_kept(self) -> None:
        self.assertEqual(short_name("score"), "score")

    def test_long_header_is_cut_at_a_word(self) -> None:
        name = short_name("How satisfied were you with the visit overall?", limit=20)
        self.assertEqual(name, "How satisfied were…")
        self.assertLessEqual(len(name), 20)

    def test_whitespace_and_newlines_collapse(self) -> None:
        self.assertEqual(short_name("Did you\n  agree?"), "Did you agree?")


if __name__ == "__main__":
    unittest.main()
