"""Tests for the data-source registry and source descriptors."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.dataset.sources import CsvRole, DataSource, SourceKind, SourceRegistry
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

    def test_main_character_flag_and_label(self) -> None:
        mc = DataSource(
            SourceKind.CSV,
            self.root / "mc.csv",
            role=CsvRole.MAIN_CHARACTER,
            key_column="uuid",
            value_columns=("track_id",),
        )
        self.assertTrue(mc.is_main_character)
        self.assertEqual(mc.label, "Main character file")


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

    def test_main_character_is_singleton(self) -> None:
        for name in ("mc1.csv", "mc2.csv"):
            self.reg.add(
                DataSource(
                    SourceKind.CSV,
                    self.root / name,
                    role=CsvRole.MAIN_CHARACTER,
                    key_column="uuid",
                    value_columns=("track_id",),
                )
            )
        mc = self.reg.main_character
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


if __name__ == "__main__":
    unittest.main()
