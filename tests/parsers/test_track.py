"""Tests for the track parser."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.parsers.track import load_track, track_id_from_name
from tests.fixtures import write_track


class TestTrackIdFromName(unittest.TestCase):
    def test_double_underscore_naming(self) -> None:
        self.assertEqual(track_id_from_name("-3g5yACwYnA_10__track0.csv"), 0)
        self.assertEqual(track_id_from_name("vid__track42.csv"), 42)

    def test_video_id_with_underscores(self) -> None:
        self.assertEqual(track_id_from_name("_7HVhnSYX1Y_3__track5.csv"), 5)

    def test_rejects_non_track_name(self) -> None:
        with self.assertRaises(ValueError):
            track_id_from_name("vid.vdet")


class TestLoadTrack(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_returns_track_id_and_frames(self) -> None:
        track_id, frames = load_track(write_track(self.tmp, "vid", track_id=2, frames=3))
        self.assertEqual(track_id, 2)
        self.assertEqual(len(frames), 3)

    def test_boxes_stamped_with_track_id(self) -> None:
        _, frames = load_track(write_track(self.tmp, "vid", track_id=7, frames=2))
        self.assertTrue(all(box.track_id == 7 for f in frames for box in f.boxes))

    def test_one_box_per_frame(self) -> None:
        _, frames = load_track(write_track(self.tmp, "vid", track_id=0, frames=5))
        self.assertTrue(all(len(f.boxes) == 1 for f in frames))


if __name__ == "__main__":
    unittest.main()
