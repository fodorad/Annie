"""Tests for composing a video's vdet + track annotations per frame."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.core.models import VideoEntry
from annie.media.compose import load_entry_annotations, merge_frame, strip_track_ids
from tests.fixtures import write_track, write_vdet


class TestComposeAnnotations(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.vdet = write_vdet(self.tmp, "A", frames=3, faces_per_frame=2)
        self.track0 = write_track(self.tmp, "A", track_id=0, frames=3)
        self.track1 = write_track(self.tmp, "A", track_id=1, frames=3)
        self.entry = VideoEntry(
            "A",
            video_path=self.tmp / "A.mp4",
            vdet_path=self.vdet,
            track_paths=[self.track0, self.track1],
            track_ids=[0, 1],
            active_track_id=1,
        )

    def test_load_entry_annotations_indexes_vdet_and_tracks(self) -> None:
        vdet_by_frame, tracks_by_id = load_entry_annotations(self.entry)
        self.assertEqual(len(vdet_by_frame[0].boxes), 2)  # two faces per frame
        self.assertEqual(set(tracks_by_id), {0, 1})
        self.assertIn(0, tracks_by_id[0])

    def test_merge_includes_vdet_plus_selected_tracks(self) -> None:
        vdet_by_frame, tracks_by_id = load_entry_annotations(self.entry)
        merged = merge_frame(0, vdet_by_frame, tracks_by_id, include_track_ids=[1])
        # two vdet boxes (track_id None) + one track box (track_id 1)
        self.assertEqual(len(merged.boxes), 3)
        track_box_ids = {b.track_id for b in merged.boxes if b.track_id is not None}
        self.assertEqual(track_box_ids, {1})

    def test_merge_handles_frame_without_vdet(self) -> None:
        _, tracks_by_id = load_entry_annotations(self.entry)
        merged = merge_frame(0, {}, tracks_by_id, include_track_ids=[0, 1])
        self.assertEqual(len(merged.boxes), 2)  # only the two track boxes

    def test_strip_track_ids_is_active_only(self) -> None:
        self.assertEqual(strip_track_ids(self.entry), [1])
        self.entry.active_track_id = -1
        self.assertEqual(strip_track_ids(self.entry), [])

    def test_entry_without_vdet(self) -> None:
        entry = VideoEntry("A", track_paths=[self.track0], track_ids=[0])
        vdet_by_frame, tracks_by_id = load_entry_annotations(entry)
        self.assertEqual(vdet_by_frame, {})
        self.assertIn(0, tracks_by_id)


if __name__ == "__main__":
    unittest.main()
