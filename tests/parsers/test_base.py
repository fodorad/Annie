"""Tests for the shared CSV schema and base parser helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.parsers.base import (
    CSV_COLUMNS,
    AnnotationParser,
    group_rows_by_frame,
    parse_bbox_row,
    read_csv_rows,
)
from annie.parsers.vdet import load_vdet
from tests.fixtures import write_csv, write_vdet


class TestSchema(unittest.TestCase):
    def test_seventeen_columns_in_order(self) -> None:
        self.assertEqual(len(CSV_COLUMNS), 17)
        self.assertEqual(CSV_COLUMNS[0], "frame_id")
        self.assertEqual(CSV_COLUMNS[-1], "right_mouth_y")

    def test_load_vdet_satisfies_parser_protocol(self) -> None:
        self.assertIsInstance(load_vdet, AnnotationParser)


class TestReadAndParse(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_read_csv_handles_crlf_and_header(self) -> None:
        path = write_vdet(self.tmp, "vid", frames=2)
        rows = read_csv_rows(path)
        self.assertEqual(len(rows), 2)
        self.assertIn("frame_id", rows[0])
        self.assertNotIn("\r", rows[0]["right_mouth_y"])  # CRLF stripped

    def test_parse_bbox_row_landmarks_and_floats(self) -> None:
        rows = read_csv_rows(write_vdet(self.tmp, "vid", frames=1))
        box = parse_bbox_row(rows[0], track_id=7)
        self.assertEqual(box.track_id, 7)
        self.assertEqual(
            set(box.landmarks), {"left_eye", "right_eye", "nose", "left_mouth", "right_mouth"}
        )
        self.assertEqual(len(box.landmarks["nose"]), 2)

    def test_parse_bbox_row_rounds_float_coordinates(self) -> None:
        path = write_csv(
            self.tmp / "f.vdet",
            ["0,src,0.9,10.6,20.4,30.5,40.5,1,2,3,4,5,6,7,8,9,10"],
        )
        box = parse_bbox_row(read_csv_rows(path)[0])
        self.assertEqual((box.x, box.y, box.w, box.h), (11, 20, 30, 40))

    def test_group_rows_sorted_by_frame(self) -> None:
        tail = "1," * 13 + "1"  # x,y,w,h + 10 landmark coords = 14 numeric fields
        path = write_csv(self.tmp / "u.vdet", [f"2,s,0.9,{tail}", f"0,s,0.9,{tail}"])
        frames = group_rows_by_frame(read_csv_rows(path))
        self.assertEqual([f.frame_idx for f in frames], [0, 2])


if __name__ == "__main__":
    unittest.main()
