"""Tests for the vdet parser."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.parsers.vdet import load_vdet
from tests.fixtures import write_vdet


class TestLoadVdet(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_one_frame_annotation_per_frame(self) -> None:
        frames = load_vdet(write_vdet(self.tmp, "vid", frames=4, faces_per_frame=1))
        self.assertEqual(len(frames), 4)
        self.assertEqual([f.frame_idx for f in frames], [0, 1, 2, 3])

    def test_multiple_faces_grouped_into_one_frame(self) -> None:
        frames = load_vdet(write_vdet(self.tmp, "vid", frames=2, faces_per_frame=3))
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(len(f.boxes) == 3 for f in frames))

    def test_detections_have_no_track_id(self) -> None:
        frames = load_vdet(write_vdet(self.tmp, "vid", frames=1, faces_per_frame=2))
        self.assertTrue(all(box.track_id is None for box in frames[0].boxes))


if __name__ == "__main__":
    unittest.main()
