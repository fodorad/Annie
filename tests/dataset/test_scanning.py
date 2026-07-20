"""Tests for the dataset scanner: per-video aggregation, status, metrics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.core.models import VideoEntry
from annie.dataset.scanning import ScanResult, build_manifest, resolve_video_stem, scan_dataset
from annie.dataset.sources import CsvRole, DataSource, SourceKind, SourceRegistry
from tests.fixtures import (
    write_appledouble_junk,
    write_participants,
    write_table,
    write_track,
    write_vdet,
    write_video,
)

HEURISTIC = "participant_face_track_heuristic.csv"


class TestResolveVideoStem(unittest.TestCase):
    def test_exact_match(self) -> None:
        self.assertEqual(resolve_video_stem("vid", ["vid", "other"]), "vid")

    def test_prefix_match_with_underscore(self) -> None:
        self.assertEqual(resolve_video_stem("vid__track0", ["vid"]), "vid")

    def test_longest_stem_first_prevents_swallowing(self) -> None:
        self.assertEqual(resolve_video_stem("X2__track0", ["X2", "X"]), "X2")

    def test_no_match_returns_none(self) -> None:
        self.assertIsNone(resolve_video_stem("ghost__track0", ["vid"]))


class TestByVideoId(unittest.TestCase):
    """The ``video_id`` index behind the Segment-review task's per-keystroke lookup."""

    @staticmethod
    def _result(video_ids: list[str]) -> ScanResult:
        return ScanResult(entries=[VideoEntry(video_id=vid) for vid in video_ids])

    def test_indexes_every_entry_by_id(self) -> None:
        result = self._result(["a", "b", "c"])
        self.assertEqual(sorted(result.by_video_id), ["a", "b", "c"])
        self.assertIs(result.by_video_id["b"], result.entries[1])

    def test_missing_id_is_absent(self) -> None:
        self.assertNotIn("ghost", self._result(["a"]).by_video_id)

    def test_duplicate_ids_keep_the_first_in_manifest_order(self) -> None:
        # One video may appear under several annotation suffixes; the linear next(...)
        # search this replaced returned the first match, so the index must agree.
        result = self._result(["dup", "dup"])
        self.assertIs(result.by_video_id["dup"], result.entries[0])

    def test_index_is_built_once_and_reused(self) -> None:
        result = self._result(["a"])
        self.assertIs(result.by_video_id, result.by_video_id)

    def test_empty_manifest_yields_an_empty_index(self) -> None:
        self.assertEqual(ScanResult().by_video_id, {})


class TestScanDataset(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.videos = self.root / "video"
        self.vdets = self.root / "vdets"
        self.tracks = self.root / "tracks"

    def _scan(self, participants: Path | None = None):
        return scan_dataset(self.videos, self.vdets, self.tracks, participants)

    def test_per_video_aggregation(self) -> None:
        write_video(self.videos, "A")
        write_vdet(self.vdets, "A")
        write_track(self.tracks, "A", track_id=0)
        write_track(self.tracks, "A", track_id=1)

        result = self._scan()
        self.assertEqual(len(result.entries), 1)  # one row for the video
        entry = result.entries[0]
        self.assertTrue(entry.has_vdet)
        self.assertEqual(entry.track_ids, [0, 1])  # sorted
        self.assertEqual(entry.status, "linked")

    def test_three_statuses(self) -> None:
        write_video(self.videos, "A")  # has track -> linked
        write_track(self.tracks, "A", track_id=0)
        write_video(self.videos, "B")  # no annotation -> video_only
        write_track(self.tracks, "GHOST", track_id=0)  # no video -> annotation_only

        statuses = {e.video_id: e.status for e in self._scan().entries}
        self.assertEqual(statuses["A"], "linked")
        self.assertEqual(statuses["B"], "video_only")
        self.assertEqual(statuses["GHOST"], "annotation_only")

    def test_overview_counts(self) -> None:
        for vid in ("A", "B", "C"):
            write_video(self.videos, vid)
        write_vdet(self.vdets, "A")
        write_track(self.tracks, "A", track_id=0)
        write_vdet(self.vdets, "B")  # B has vdet only
        write_track(self.tracks, "C", track_id=0)  # C has track only

        counts = self._scan().counts
        self.assertEqual(counts["num_videos"], 3)
        self.assertEqual(counts["num_vdet_files"], 2)
        self.assertEqual(counts["num_track_files"], 2)
        self.assertEqual(counts["videos_vdet_and_track"], 1)  # only A
        self.assertEqual(counts["videos_with_vdet"], 2)  # A, B
        self.assertEqual(counts["videos_with_track"], 2)  # A, C

    def test_colliding_vdets_prefer_exact_and_count_all(self) -> None:
        # "A_extra.vdet" prefix-resolves to video "A", colliding with "A.vdet".
        write_video(self.videos, "A")
        exact = write_vdet(self.vdets, "A")
        write_vdet(self.vdets, "A_extra")

        result = self._scan()
        entry = next(e for e in result.entries if e.video_id == "A")
        self.assertEqual(entry.vdet_path, exact)  # exact-stem file wins, not iteration order
        self.assertEqual(result.counts["num_vdet_files"], 2)  # both files counted

    def test_protagonist_resolution_and_availability(self) -> None:
        write_video(self.videos, "A")
        write_track(self.tracks, "A", track_id=1)
        participants = self.root / HEURISTIC
        write_participants(participants, {"A": 1})

        result = self._scan(participants)
        self.assertTrue(result.counts["protagonist_available"])
        self.assertEqual(result.entries[0].active_track_id, 1)

    def test_protagonist_unavailable_without_file(self) -> None:
        write_video(self.videos, "A")
        self.assertFalse(self._scan().counts["protagonist_available"])

    def test_row_ids_number_the_whole_sorted_manifest_from_one(self) -> None:
        for name in ("C", "A", "B"):
            write_video(self.videos, name)
        entries = self._scan().entries
        self.assertEqual([e.video_id for e in entries], ["A", "B", "C"])
        self.assertEqual([e.row_id for e in entries], [1, 2, 3])

    def test_row_id_survives_filtering_to_a_subset(self) -> None:
        """A filtered view must show dataset numbers, not renumber from 1."""
        for name in ("A", "B", "C", "D"):
            write_video(self.videos, name)
        entries = self._scan().entries
        subset = [e for e in entries if e.video_id in {"B", "D"}]
        self.assertEqual([e.row_id for e in subset], [2, 4])

    def test_skips_apple_double_junk(self) -> None:
        write_video(self.videos, "A")
        write_track(self.tracks, "A", track_id=0)
        write_appledouble_junk(self.videos, "A.mp4")
        write_appledouble_junk(self.tracks, "A__track0.csv")
        self.assertEqual(len(self._scan().entries), 1)

    def test_missing_folders_yield_empty(self) -> None:
        result = scan_dataset(self.root / "nope", self.root / "x", self.root / "y")
        self.assertEqual(result.entries, [])
        self.assertEqual(result.counts["num_videos"], 0)

    def test_entries_sorted_by_video_id(self) -> None:
        for vid in ("B", "A", "C"):
            write_video(self.videos, vid)
        self.assertEqual([e.video_id for e in self._scan().entries], ["A", "B", "C"])


class TestBuildManifest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.videos = self.root / "video"
        self.tracks = self.root / "tracks"
        write_video(self.videos, "A")
        write_video(self.videos, "B")
        write_track(self.tracks, "A", track_id=0)

    def _registry(self) -> SourceRegistry:
        reg = SourceRegistry()
        reg.add(DataSource(SourceKind.VIDEO, self.videos))
        reg.add(DataSource(SourceKind.TRACK, self.tracks))
        return reg

    def test_no_videos_without_video_source(self) -> None:
        # Browse gates on a video source; the scan still aggregates annotation-only
        # entries, but none of them count as a video.
        reg = SourceRegistry()
        reg.add(DataSource(SourceKind.TRACK, self.tracks))
        result = build_manifest(reg)
        self.assertEqual(result.counts["num_videos"], 0)
        self.assertTrue(all(not e.has_video for e in result.entries))

    def test_label_source_attaches_values_and_columns(self) -> None:
        labels = write_table(
            self.root / "labels.csv",
            ["uuid", "Sentiment", "Angry"],
            [
                {"uuid": "A", "Sentiment": "negative", "Angry": "0.33"},
                {"uuid": "B", "Sentiment": "positive", "Angry": "0.00"},
            ],
        )
        reg = self._registry()
        reg.add(
            DataSource(
                SourceKind.CSV, labels, key_column="uuid", value_columns=("Sentiment", "Angry")
            )
        )
        result = build_manifest(reg)
        by_id = {e.video_id: e for e in result.entries}
        self.assertEqual(by_id["A"].labels, {"Sentiment": "negative", "Angry": "0.33"})
        self.assertEqual(result.label_columns, ["Sentiment", "Angry"])
        self.assertEqual(result.label_values("Sentiment"), ["negative", "positive"])

    def test_protagonist_source_resolves_active_track(self) -> None:
        mc = write_table(
            self.root / "mc.csv", ["uuid", "track_id"], [{"uuid": "A", "track_id": "0"}]
        )
        reg = self._registry()
        reg.add(
            DataSource(
                SourceKind.CSV,
                mc,
                role=CsvRole.PROTAGONIST,
                key_column="uuid",
                value_columns=("track_id",),
            )
        )
        result = build_manifest(reg)
        self.assertTrue(result.protagonist_available)
        self.assertEqual({e.video_id: e.active_track_id for e in result.entries}["A"], 0)


if __name__ == "__main__":
    unittest.main()
