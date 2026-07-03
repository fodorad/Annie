"""Tests for the domain models."""

from __future__ import annotations

import unittest
from pathlib import Path

from annie.core.models import NO_ACTIVE_TRACK, BBox, Event, FrameAnnotation, VideoEntry


class TestBBox(unittest.TestCase):
    def setUp(self) -> None:
        self.box = BBox(x=10, y=20, w=100, h=200, score=0.9)

    def test_edges_and_area(self) -> None:
        self.assertEqual(self.box.x2, 110)
        self.assertEqual(self.box.y2, 220)
        self.assertEqual(self.box.area, 20000)

    def test_area_clamps_negative_dimensions(self) -> None:
        self.assertEqual(BBox(0, 0, -5, 10, 0.5).area, 0)

    def test_contains_inclusive_bounds(self) -> None:
        self.assertTrue(self.box.contains(10, 20))
        self.assertTrue(self.box.contains(60, 120))
        self.assertTrue(self.box.contains(110, 220))
        self.assertFalse(self.box.contains(9, 20))
        self.assertFalse(self.box.contains(60, 221))


class TestVideoEntry(unittest.TestCase):
    def test_label_and_key_are_the_video_id(self) -> None:
        entry = VideoEntry("vid")
        self.assertEqual(entry.label, "vid")
        self.assertEqual(entry.key, "vid")

    def test_presence_flags(self) -> None:
        bare = VideoEntry("v")
        self.assertFalse(bare.has_video)
        self.assertFalse(bare.has_vdet)
        self.assertFalse(bare.has_track)
        full = VideoEntry(
            "v",
            video_path=Path("/x/v.mp4"),
            vdet_path=Path("/a/v.vdet"),
            track_paths=[Path("/t/v__track0.csv")],
            track_ids=[0],
        )
        self.assertTrue(full.has_video)
        self.assertTrue(full.has_vdet)
        self.assertTrue(full.has_track)

    def test_active_track_defaults_to_none_sentinel(self) -> None:
        entry = VideoEntry("v")
        self.assertEqual(entry.active_track_id, NO_ACTIVE_TRACK)
        self.assertFalse(entry.has_active_track)

    def test_has_active_track_when_assigned(self) -> None:
        self.assertTrue(VideoEntry("v", active_track_id=0).has_active_track)
        self.assertFalse(VideoEntry("v", active_track_id=-1).has_active_track)

    def test_default_status_is_linked(self) -> None:
        self.assertEqual(VideoEntry("v").status, "linked")

    def test_labels_default_empty_and_settable(self) -> None:
        self.assertEqual(VideoEntry("v").labels, {})
        self.assertEqual(VideoEntry("v", labels={"Sentiment": "neg"}).labels["Sentiment"], "neg")


class TestFrameAnnotationAndEvent(unittest.TestCase):
    def test_frame_annotation_default_boxes(self) -> None:
        fa = FrameAnnotation(frame_idx=5)
        self.assertEqual(fa.frame_idx, 5)
        self.assertEqual(fa.boxes, [])

    def test_event_metadata_default(self) -> None:
        event = Event("e1", "smile", 1.0, 2.5)
        self.assertEqual(event.metadata, {})
        self.assertEqual(event.end, 2.5)


if __name__ == "__main__":
    unittest.main()
