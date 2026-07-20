"""Tests for the Segment-review domain: loading clips and exporting decisions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.dataset.segments import (
    SegmentClip,
    clip_key,
    export_decision_sets,
    load_segment_clips,
    next_undecided_index,
)
from annie.dataset.sources import CsvRole, DataSource, SegmentationBand, SourceKind
from tests.fixtures import write_table


class TestClipKey(unittest.TestCase):
    def test_composite_key_joins_with_underscore(self) -> None:
        self.assertEqual(clip_key("227426", "15"), "227426_15")

    def test_segment_id_may_already_carry_underscore_prefix(self) -> None:
        # review_band.csv stores segment_id as "_15"; the key must not double the "_".
        self.assertEqual(clip_key("227426", "_15"), "227426_15")


class TestNextUndecidedIndex(unittest.TestCase):
    """Jump-to-next-undecided cycles the backlog and reports when all are decided."""

    @staticmethod
    def _clips(n: int) -> list[SegmentClip]:
        return [SegmentClip(f"v_{i}", "v", str(i), bands=(), tags={}) for i in range(n)]

    def test_finds_the_next_undecided_after_the_cursor(self) -> None:
        clips = self._clips(4)
        decisions = {"v_1": "accept"}
        self.assertEqual(next_undecided_index(clips, decisions, start=0), 2)

    def test_wraps_past_the_end(self) -> None:
        clips = self._clips(4)
        decisions = {"v_3": "drop"}
        # From the last clip, the only undecided ones are behind it → wrap to index 0.
        self.assertEqual(next_undecided_index(clips, decisions, start=3), 0)

    def test_skips_the_current_clip_even_when_undecided(self) -> None:
        clips = self._clips(3)
        # The current clip is undecided but "next" must move on to a different one.
        self.assertEqual(next_undecided_index(clips, {}, start=0), 1)

    def test_returns_none_when_all_decided(self) -> None:
        clips = self._clips(2)
        decisions = {"v_0": "accept", "v_1": "drop"}
        self.assertIsNone(next_undecided_index(clips, decisions, start=0))

    def test_returns_none_for_empty(self) -> None:
        self.assertIsNone(next_undecided_index([], {}, start=0))


class TestLoadSegmentClips(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def _source(self, path: Path) -> DataSource:
        return DataSource(
            SourceKind.CSV,
            path,
            role=CsvRole.SEGMENTATION,
            key_column="video_id",
            segment_column="segment_id",
            bands=(
                SegmentationBand("cut", "cut_start_sec", "cut_end_sec"),
                SegmentationBand("GT", "gt_start_sec", "gt_end_sec"),
            ),
            value_columns=("gt_text",),
        )

    def test_loads_one_clip_per_row_with_bands(self) -> None:
        path = write_table(
            self.root / "band.csv",
            [
                "video_id",
                "segment_id",
                "cut_start_sec",
                "cut_end_sec",
                "gt_start_sec",
                "gt_end_sec",
                "gt_text",
            ],
            [
                {
                    "video_id": "227426",
                    "segment_id": "15",
                    "cut_start_sec": "73.5",
                    "cut_end_sec": "77.9",
                    "gt_start_sec": "74.6",
                    "gt_end_sec": "77.6",
                    "gt_text": "hello",
                },
            ],
        )
        clips = load_segment_clips(self._source(path))
        self.assertEqual(len(clips), 1)
        clip = clips[0]
        self.assertEqual(clip.key, "227426_15")
        self.assertEqual(clip.video_id, "227426")
        self.assertEqual([b.name for b in clip.bands], ["cut", "GT"])
        self.assertAlmostEqual(clip.bands[0].start, 73.5)
        self.assertAlmostEqual(clip.bands[0].end, 77.9)
        self.assertEqual(clip.tags["gt_text"], "hello")

    def test_rows_with_unparseable_span_are_skipped(self) -> None:
        path = write_table(
            self.root / "band.csv",
            [
                "video_id",
                "segment_id",
                "cut_start_sec",
                "cut_end_sec",
                "gt_start_sec",
                "gt_end_sec",
                "gt_text",
            ],
            [
                {
                    "video_id": "a",
                    "segment_id": "0",
                    "cut_start_sec": "n/a",
                    "cut_end_sec": "",
                    "gt_start_sec": "1",
                    "gt_end_sec": "2",
                    "gt_text": "",
                },
            ],
        )
        clips = load_segment_clips(self._source(path))
        # the GT band still parses, so the clip survives with only the valid band
        self.assertEqual(len(clips), 1)
        self.assertEqual([b.name for b in clips[0].bands], ["GT"])

    def test_row_with_no_valid_band_is_dropped(self) -> None:
        path = write_table(
            self.root / "band.csv",
            [
                "video_id",
                "segment_id",
                "cut_start_sec",
                "cut_end_sec",
                "gt_start_sec",
                "gt_end_sec",
                "gt_text",
            ],
            [
                {
                    "video_id": "a",
                    "segment_id": "0",
                    "cut_start_sec": "x",
                    "cut_end_sec": "y",
                    "gt_start_sec": "p",
                    "gt_end_sec": "q",
                    "gt_text": "",
                },
            ],
        )
        self.assertEqual(load_segment_clips(self._source(path)), [])


class TestExportDecisionSets(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_writes_accepted_and_dropped_files(self) -> None:
        clips = [
            SegmentClip("v_0", "v", "0", bands=(), tags={"gt_text": "a"}),
            SegmentClip("v_1", "v", "1", bands=(), tags={"gt_text": "b"}),
            SegmentClip("v_2", "v", "2", bands=(), tags={"gt_text": "c"}),
        ]
        decisions = {"v_0": "accept", "v_1": "drop", "v_2": "accept"}
        accepted, dropped = export_decision_sets(
            clips, decisions, self.root / "accepted.csv", self.root / "dropped.csv"
        )
        acc_text = accepted.read_text(encoding="utf-8")
        drop_text = dropped.read_text(encoding="utf-8")
        self.assertIn("v_0", acc_text)
        self.assertIn("v_2", acc_text)
        self.assertNotIn("v_1", acc_text)
        self.assertIn("v_1", drop_text)
        self.assertNotIn("v_0", drop_text)
        # passthrough columns preserved
        self.assertIn("gt_text", acc_text)

    def test_undecided_clips_appear_in_neither_file(self) -> None:
        clips = [SegmentClip("v_0", "v", "0", bands=(), tags={})]
        accepted, dropped = export_decision_sets(
            clips, {}, self.root / "a.csv", self.root / "d.csv"
        )
        self.assertNotIn("v_0", accepted.read_text(encoding="utf-8"))
        self.assertNotIn("v_0", dropped.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
