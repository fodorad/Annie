"""Tests for the data-source registry and source descriptors."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.dataset.sources import (
    CsvRole,
    DataSource,
    SegmentationBand,
    SourceKind,
    SourceRegistry,
    TaskKind,
    task_readiness,
)
from tests.fixtures import write_table, write_track, write_vdet, write_video


class TestDataSource(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_folder_availability_and_counts(self) -> None:
        videos = self.root / "video"
        write_video(videos, "A")
        write_video(videos, "B")
        source = DataSource(SourceKind.VIDEO, videos)
        self.assertTrue(source.available)
        self.assertEqual(source.count(), 2)
        self.assertTrue(source.is_folder)

    def test_missing_folder_is_unavailable_and_zero(self) -> None:
        source = DataSource(SourceKind.VDET, self.root / "nope")
        self.assertFalse(source.available)
        self.assertEqual(source.count(), 0)

    def test_track_count_uses_glob(self) -> None:
        tracks = self.root / "tracks"
        write_track(tracks, "A", track_id=0)
        write_track(tracks, "A", track_id=1)
        self.assertEqual(DataSource(SourceKind.TRACK, tracks).count(), 2)

    def test_vdet_count(self) -> None:
        vdets = self.root / "vdets"
        write_vdet(vdets, "A")
        self.assertEqual(DataSource(SourceKind.VDET, vdets).count(), 1)

    def test_csv_count_and_file_availability(self) -> None:
        csv_path = write_table(
            self.root / "labels.csv", ["uuid", "Sentiment"], [{"uuid": "A", "Sentiment": "neg"}]
        )
        source = DataSource(
            SourceKind.CSV, csv_path, key_column="uuid", value_columns=("Sentiment",)
        )
        self.assertTrue(source.available)
        self.assertFalse(source.is_folder)
        self.assertEqual(source.count(), 1)

    def test_protagonist_flag_and_label(self) -> None:
        mc = DataSource(
            SourceKind.CSV,
            self.root / "mc.csv",
            role=CsvRole.PROTAGONIST,
            key_column="uuid",
            value_columns=("track_id",),
        )
        self.assertTrue(mc.is_protagonist)
        self.assertEqual(mc.label, "Protagonist file")

    def test_segmentation_flag_and_label(self) -> None:
        seg = DataSource(
            SourceKind.CSV,
            self.root / "review_band.csv",
            role=CsvRole.SEGMENTATION,
            key_column="video_id",
            segment_column="segment_id",
            bands=(
                SegmentationBand("cut", "cut_start_sec", "cut_end_sec"),
                SegmentationBand("GT", "gt_start_sec", "gt_end_sec"),
            ),
            value_columns=("gt_text", "whisperx_text"),
        )
        self.assertTrue(seg.is_segmentation)
        self.assertFalse(seg.is_protagonist)
        self.assertEqual(seg.label, "Segmentation file")

    def test_segmentation_role_value_roundtrips(self) -> None:
        self.assertIs(CsvRole("segmentation"), CsvRole.SEGMENTATION)

    def test_non_segmentation_csv_has_no_bands(self) -> None:
        labels = DataSource(
            SourceKind.CSV, self.root / "l.csv", key_column="uuid", value_columns=("x",)
        )
        self.assertFalse(labels.is_segmentation)
        self.assertEqual(labels.bands, ())
        self.assertIsNone(labels.segment_column)

    def test_legacy_main_character_role_value_resolves_to_protagonist(self) -> None:
        """Configs written before the rename store ``"main_character"``."""
        self.assertIs(CsvRole("main_character"), CsvRole.PROTAGONIST)
        self.assertIs(CsvRole("protagonist"), CsvRole.PROTAGONIST)
        self.assertIs(CsvRole("labels"), CsvRole.LABELS)

    def test_unknown_role_value_still_raises(self) -> None:
        with self.assertRaises(ValueError):
            CsvRole("nonsense")


class TestSourceRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.reg = SourceRegistry()

    def test_folder_kinds_are_singletons(self) -> None:
        self.reg.add(DataSource(SourceKind.VIDEO, self.root / "v1"))
        self.reg.add(DataSource(SourceKind.VIDEO, self.root / "v2"))
        self.assertEqual(len(self.reg.sources), 1)
        assert self.reg.video is not None
        self.assertEqual(self.reg.video.path, self.root / "v2")

    def test_protagonist_is_singleton(self) -> None:
        for name in ("mc1.csv", "mc2.csv"):
            self.reg.add(
                DataSource(
                    SourceKind.CSV,
                    self.root / name,
                    role=CsvRole.PROTAGONIST,
                    key_column="uuid",
                    value_columns=("track_id",),
                )
            )
        mc = self.reg.protagonist
        assert mc is not None
        self.assertEqual(mc.path.name, "mc2.csv")
        self.assertEqual(len(self.reg.sources), 1)

    def test_label_csvs_accumulate_and_dedupe_by_path(self) -> None:
        a = DataSource(SourceKind.CSV, self.root / "a.csv", key_column="uuid", value_columns=("x",))
        b = DataSource(SourceKind.CSV, self.root / "b.csv", key_column="uuid", value_columns=("y",))
        self.reg.add(a)
        self.reg.add(b)
        self.reg.add(
            DataSource(SourceKind.CSV, self.root / "a.csv", key_column="uuid", value_columns=("z",))
        )
        self.assertEqual(len(self.reg.label_sources), 2)
        first = next(s for s in self.reg.label_sources if s.path.name == "a.csv")
        self.assertEqual(first.value_columns, ("z",))

    def test_segmentation_sources_accumulate_and_are_listed(self) -> None:
        seg = DataSource(
            SourceKind.CSV,
            self.root / "review_band.csv",
            role=CsvRole.SEGMENTATION,
            key_column="video_id",
            segment_column="segment_id",
            bands=(SegmentationBand("GT", "gt_start_sec", "gt_end_sec"),),
        )
        label = DataSource(
            SourceKind.CSV, self.root / "l.csv", key_column="uuid", value_columns=("x",)
        )
        self.reg.add(label)
        self.reg.add(seg)
        self.assertEqual(len(self.reg.segmentation_sources), 1)
        self.assertEqual(self.reg.segmentation_sources[0].path.name, "review_band.csv")
        # a segmentation CSV is not counted as a label source
        self.assertEqual(len(self.reg.label_sources), 1)

    def test_remove(self) -> None:
        src = DataSource(SourceKind.VIDEO, self.root / "v")
        self.reg.add(src)
        self.reg.remove(src)
        self.assertEqual(self.reg.sources, [])

    def test_has_video_requires_existing_folder(self) -> None:
        self.reg.add(DataSource(SourceKind.VIDEO, self.root / "missing"))
        self.assertFalse(self.reg.has_video)
        write_video(self.root / "present", "A")
        self.reg.add(DataSource(SourceKind.VIDEO, self.root / "present"))
        self.assertTrue(self.reg.has_video)

    def test_available_kinds_drop_present_singletons(self) -> None:
        self.reg.add(DataSource(SourceKind.VIDEO, self.root / "v"))
        kinds = self.reg.available_kinds_to_add()
        self.assertNotIn(SourceKind.VIDEO, kinds)
        self.assertIn(SourceKind.VDET, kinds)
        self.assertIn(SourceKind.CSV, kinds)  # CSVs always addable


class TestTaskReadiness(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.reg = SourceRegistry()

    def _readiness(self, task: TaskKind) -> object:
        return next(r for r in task_readiness(self.reg) if r.task is task)

    def test_curation_ready_with_only_video(self) -> None:
        write_video(self.root / "v", "A")
        self.reg.add(DataSource(SourceKind.VIDEO, self.root / "v"))
        curation = self._readiness(TaskKind.CURATION)
        self.assertTrue(curation.ready)

    def test_protagonist_needs_protagonist_csv(self) -> None:
        write_video(self.root / "v", "A")
        self.reg.add(DataSource(SourceKind.VIDEO, self.root / "v"))
        protagonist = self._readiness(TaskKind.PROTAGONIST)
        self.assertFalse(protagonist.ready)  # no protagonist CSV yet
        mc = write_table(
            self.root / "mc.csv", ["uuid", "track_id"], [{"uuid": "A", "track_id": "0"}]
        )
        self.reg.add(
            DataSource(
                SourceKind.CSV,
                mc,
                role=CsvRole.PROTAGONIST,
                key_column="uuid",
                value_columns=("track_id",),
            )
        )
        self.assertTrue(self._readiness(TaskKind.PROTAGONIST).ready)

    def test_segment_review_needs_segmentation_csv(self) -> None:
        write_video(self.root / "v", "A")
        self.reg.add(DataSource(SourceKind.VIDEO, self.root / "v"))
        self.assertFalse(self._readiness(TaskKind.SEGMENT_REVIEW).ready)
        band = write_table(
            self.root / "review_band.csv",
            ["video_id", "segment_id", "gt_start_sec", "gt_end_sec"],
            [{"video_id": "A", "segment_id": "0", "gt_start_sec": "1.0", "gt_end_sec": "2.0"}],
        )
        self.reg.add(
            DataSource(
                SourceKind.CSV,
                band,
                role=CsvRole.SEGMENTATION,
                key_column="video_id",
                segment_column="segment_id",
                bands=(SegmentationBand("GT", "gt_start_sec", "gt_end_sec"),),
            )
        )
        self.assertTrue(self._readiness(TaskKind.SEGMENT_REVIEW).ready)

    def test_readiness_reports_missing_requirements(self) -> None:
        # empty registry: every task lists what it still needs
        seg = self._readiness(TaskKind.SEGMENT_REVIEW)
        self.assertFalse(seg.ready)
        self.assertTrue(any(not req.present for req in seg.requirements))


if __name__ == "__main__":
    unittest.main()
