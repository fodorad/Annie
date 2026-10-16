"""Tests for the table dialog's join editor and its naming rules."""

from __future__ import annotations

import unittest
from pathlib import Path

from nicegui import ui

from annie.dataset.sources import DataSource, SourceKind
from annie.pages.csv_dialog import VIDEO_ID_TARGET, _JoinEditor, tag_clashes
from tests.pages._nicegui import ui_client

HEADER = ["subject", "visit_date", "score", "How satisfied were you with the visit overall?"]
ROWS = [
    {"subject": "4021", "visit_date": "2024-01-01", "score": "low", HEADER[3]: "3"},
    {"subject": "4021", "visit_date": "2024-06-01", "score": "high", HEADER[3]: "4"},
    {"subject": "4022", "visit_date": "2024-02-01", "score": "mid", HEADER[3]: "5"},
]
TARGETS = {
    VIDEO_ID_TARGET: {"4021_a": "4021_a", "4021_b": "4021_b", "4022_a": "4022_a"},
    "subject": {"4021_a": "4021", "4021_b": "4021", "4022_a": "4022"},
}
PATH = Path("intake.xlsx")


class TestTagClashes(unittest.TestCase):
    def _source(self, *columns: str, names: dict[str, str] | None = None) -> DataSource:
        return DataSource(
            SourceKind.CSV, PATH, key_column="id", value_columns=columns, column_names=names or {}
        )

    def test_unique_names_pass(self) -> None:
        self.assertEqual(tag_clashes([self._source("a"), self._source("b")], {"part"}), [])

    def test_clash_with_a_path_field_or_another_sheet(self) -> None:
        sources = [self._source("part"), self._source("a"), self._source("x", names={"x": "a"})]
        self.assertEqual(tag_clashes(sources, {"part"}), ["part", "a"])


class TestJoinEditor(unittest.TestCase):
    """What the user picks becomes a correctly-formed label source."""

    def _editor(self) -> _JoinEditor:
        with ui_client(), ui.column():
            return _JoinEditor(HEADER, ROWS, TARGETS)

    def test_suggests_the_path_field_and_an_order_column(self) -> None:
        editor = self._editor()
        self.assertEqual((editor._key.value, editor._match.value), ("subject", "subject"))
        self.assertEqual(editor._order.value, "visit_date")  # a time-like column, newest first
        self.assertTrue(editor._descending.value)
        self.assertIn("3 of 3 videos matched", editor._summary.text)

    def test_repeated_ids_require_an_order_column(self) -> None:
        editor = self._editor()
        editor._checks["score"].set_value(True)
        editor._order.set_value(None)
        result = editor.collect(PATH, "visits")
        self.assertIsInstance(result, str)
        self.assertIn("Order by", str(result))

    def test_collect_builds_the_source(self) -> None:
        editor = self._editor()
        editor._checks["score"].set_value(True)
        editor._checks[HEADER[3]].set_value(True)
        editor._names[HEADER[3]].set_value("Satisfaction")
        source = editor.collect(PATH, "visits")
        assert isinstance(source, DataSource)
        self.assertEqual(source.sheet, "visits")
        self.assertEqual(source.match_field, "subject")
        self.assertEqual(source.order_column, "visit_date")
        self.assertEqual(source.value_columns, ("score", HEADER[3]))
        # Only a real rename is stored; an unchanged header keeps its own name.
        self.assertEqual(source.column_names, {HEADER[3]: "Satisfaction"})

    def test_video_id_target_means_no_match_field(self) -> None:
        editor = self._editor()
        editor._match.set_value(VIDEO_ID_TARGET)
        editor._substring.set_value(True)
        editor._checks["score"].set_value(True)
        source = editor.collect(PATH, None)
        assert isinstance(source, DataSource)
        self.assertIsNone(source.match_field)
        self.assertTrue(source.match_substring)
        self.assertIn("3 of 3 videos matched", editor._summary.text)  # 4021 ⊂ 4021_a

    def test_unique_ids_need_no_order(self) -> None:
        with ui_client(), ui.column():
            editor = _JoinEditor(HEADER, ROWS[1:], TARGETS)
        editor._checks["score"].set_value(True)
        source = editor.collect(PATH, None)
        assert isinstance(source, DataSource)
        self.assertIsNone(source.order_column)


if __name__ == "__main__":
    unittest.main()
