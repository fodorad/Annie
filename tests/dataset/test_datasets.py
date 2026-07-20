"""Tests for dataset config save/load/discover."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from annie.core.config import settings
from annie.dataset import datasets
from annie.dataset.sources import (
    CsvRole,
    DataSource,
    SegmentationBand,
    SourceKind,
    SourceRegistry,
)


class TestConfigIO(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def _registry(self) -> SourceRegistry:
        reg = SourceRegistry()
        reg.add(DataSource(SourceKind.VIDEO, self.tmp / "ds" / "video"))
        reg.add(DataSource(SourceKind.TRACK, self.tmp / "ds" / "track"))
        reg.add(
            DataSource(
                SourceKind.CSV,
                self.tmp / "ds" / "labels.csv",
                role=CsvRole.LABELS,
                key_column="uuid",
                value_columns=("sentiment", "anger"),
            )
        )
        return reg

    def test_round_trip_relative_paths(self) -> None:
        cfg_dir = self.tmp / "config"
        cfg_dir.mkdir(parents=True)
        out = datasets.save_config(
            cfg_dir / "ds.json", self._registry(), "My DS", relative_to=cfg_dir
        )
        # paths in the file are relative
        raw = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(raw["name"], "My DS")
        self.assertFalse(Path(raw["sources"][0]["path"]).is_absolute())

        name, reg, _db = datasets.load_config(out)
        self.assertEqual(name, "My DS")
        self.assertEqual(reg.video.path, (self.tmp / "ds" / "video").resolve())
        labels = reg.label_sources[0]
        self.assertEqual(labels.key_column, "uuid")
        self.assertEqual(labels.value_columns, ("sentiment", "anger"))

    def test_load_resolves_relative_to_config_dir(self) -> None:
        cfg_dir = self.tmp / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "c.json").write_text(
            json.dumps({"name": "rel", "sources": [{"kind": "video", "path": "../example/video"}]}),
            encoding="utf-8",
        )
        _name, reg, _db = datasets.load_config(cfg_dir / "c.json")
        assert reg.video is not None
        self.assertEqual(reg.video.path, (self.tmp / "example" / "video").resolve())

    def test_discover_and_name(self) -> None:
        cfg_dir = self.tmp / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "a.json").write_text(json.dumps({"name": "Alpha", "sources": []}), "utf-8")
        (cfg_dir / "b.json").write_text("not json", "utf-8")
        found = datasets.discover_configs(cfg_dir)
        self.assertEqual([p.name for p in found], ["a.json", "b.json"])
        self.assertEqual(datasets.config_name(cfg_dir / "a.json"), "Alpha")
        self.assertEqual(datasets.config_name(cfg_dir / "b.json"), "b")  # falls back to stem

    def test_round_trip_preserves_column_types(self) -> None:
        cfg_dir = self.tmp / "config"
        cfg_dir.mkdir(parents=True)
        reg = SourceRegistry()
        reg.add(
            DataSource(
                SourceKind.CSV,
                self.tmp / "labels.csv",
                role=CsvRole.LABELS,
                key_column="uuid",
                value_columns=("sentiment", "subset"),
                column_types={"sentiment": "float", "subset": "str"},
            )
        )
        out = datasets.save_config(cfg_dir / "t.json", reg, "Typed", relative_to=cfg_dir)
        _name, loaded, _db = datasets.load_config(out)
        self.assertEqual(
            loaded.label_sources[0].column_types, {"sentiment": "float", "subset": "str"}
        )

    def test_legacy_role_config_loads_as_protagonist(self) -> None:
        """A config written before the protagonist rename still loads."""
        cfg_dir = self.tmp / "legacy"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "mc.csv").write_text("uuid,track_id\nv1,0\n", encoding="utf-8")
        (cfg_dir / "legacy.json").write_text(
            json.dumps(
                {
                    "name": "Legacy",
                    "sources": [
                        {
                            "kind": "csv",
                            "path": "mc.csv",
                            "role": "main_character",
                            "key_column": "uuid",
                            "value_columns": ["track_id"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        _name, reg, _db = datasets.load_config(cfg_dir / "legacy.json")
        protagonist = reg.protagonist
        assert protagonist is not None
        self.assertEqual(protagonist.value_columns, ("track_id",))
        self.assertEqual(reg.label_sources, [])

    def test_legacy_role_config_migrates_on_resave(self) -> None:
        """Loading a legacy config and saving it writes the new role value."""
        cfg_dir = self.tmp / "migrate"
        cfg_dir.mkdir(parents=True)
        reg = SourceRegistry()
        reg.add(
            DataSource(
                SourceKind.CSV,
                cfg_dir / "mc.csv",
                role=CsvRole("main_character"),
                key_column="uuid",
                value_columns=("track_id",),
            )
        )
        out = datasets.save_config(cfg_dir / "m.json", reg, "M", relative_to=cfg_dir)
        raw = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(raw["sources"][0]["role"], "protagonist")

    def test_round_trip_preserves_segmentation_mapping(self) -> None:
        cfg_dir = self.tmp / "config"
        cfg_dir.mkdir(parents=True)
        reg = SourceRegistry()
        reg.add(
            DataSource(
                SourceKind.CSV,
                self.tmp / "review_band.csv",
                role=CsvRole.SEGMENTATION,
                key_column="video_id",
                segment_column="segment_id",
                bands=(
                    SegmentationBand("cut", "cut_start_sec", "cut_end_sec"),
                    SegmentationBand("GT", "gt_start_sec", "gt_end_sec"),
                ),
                value_columns=("gt_text", "whisperx_text"),
            )
        )
        out = datasets.save_config(cfg_dir / "seg.json", reg, "Seg", relative_to=cfg_dir)
        raw = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(raw["sources"][0]["role"], "segmentation")

        _name, loaded, _db = datasets.load_config(out)
        seg = loaded.segmentation_sources[0]
        self.assertEqual(seg.key_column, "video_id")
        self.assertEqual(seg.segment_column, "segment_id")
        self.assertEqual(
            seg.bands,
            (
                SegmentationBand("cut", "cut_start_sec", "cut_end_sec"),
                SegmentationBand("GT", "gt_start_sec", "gt_end_sec"),
            ),
        )
        self.assertEqual(seg.value_columns, ("gt_text", "whisperx_text"))

    def test_bundled_example_config_is_valid(self) -> None:
        bundled = datasets.bundled_config_dir() / "mosei_mini.json"
        self.assertTrue(bundled.is_file(), "the bundled example config should exist")
        name, reg, _db = datasets.load_config(bundled)
        self.assertEqual(name, "[Example] CMU-MOSEI Mini")
        self.assertTrue(reg.has_video)  # resolves to the bundled example videos
        self.assertEqual(len(reg.label_sources), 1)
        self.assertEqual(reg.label_sources[0].column_types.get("sentiment"), "float")


class TestConfigDbPersistence(unittest.TestCase):
    """A config's DB is pinned by a bare filename resolved against ANNIE_HOME."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._saved_home = settings.annie_home
        settings.annie_home = self.home

    def tearDown(self) -> None:
        settings.annie_home = self._saved_home

    def test_bare_db_filename_round_trips_via_annie_home(self) -> None:
        cfg_dir = self.tmp / "config"
        cfg_dir.mkdir()
        reg = SourceRegistry()
        reg.add(DataSource(SourceKind.VIDEO, self.tmp / "video"))
        db = self.home / "annie_mydata.db"
        out = datasets.save_config(cfg_dir / "mydata.json", reg, "My Data", db_path=db)

        raw = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(raw["db"], "annie_mydata.db")  # stored as a bare filename

        _name, _reg, loaded_db = datasets.load_config(out)
        self.assertEqual(loaded_db, db.resolve())  # resolved back against ANNIE_HOME

    def test_absolute_db_path_is_preserved(self) -> None:
        cfg_dir = self.tmp / "config"
        cfg_dir.mkdir()
        reg = SourceRegistry()
        reg.add(DataSource(SourceKind.VIDEO, self.tmp / "video"))
        elsewhere = self.tmp / "elsewhere" / "custom.db"
        out = datasets.save_config(cfg_dir / "c.json", reg, "C", db_path=elsewhere)

        _name, _reg, loaded_db = datasets.load_config(out)
        self.assertEqual(loaded_db, elsewhere.resolve())

    def test_symlinked_home_still_stores_a_bare_filename(self) -> None:
        """A DB under a symlinked ANNIE_HOME is still recognised as living there.

        ``to_config_dict`` decides between a portable bare filename and a machine-specific
        absolute path by comparing the DB's parent against ANNIE_HOME. Callers pass a
        *resolved* DB path, so an unresolved home missed the comparison whenever it held a
        symlink — the default on macOS, where ``/tmp`` links to ``/private/tmp``. The
        config still loaded, just against an absolute path that would not exist on anyone
        else's machine.
        """
        real = self.tmp / "real_home"
        real.mkdir()
        link = self.tmp / "linked_home"
        link.symlink_to(real, target_is_directory=True)
        settings.annie_home = link

        reg = SourceRegistry()
        reg.add(DataSource(SourceKind.VIDEO, self.tmp / "video"))
        db = (link / "annie_linked.db").resolve()
        config = datasets.to_config_dict(reg, "Linked", db_path=db)

        self.assertEqual(config["db"], "annie_linked.db")

    def test_example_configs_pin_their_own_db(self) -> None:
        for stem, expected in (
            ("fi_mini", "annie_fi_mini.db"),
            ("mosei_mini", "annie_mosei_mini.db"),
        ):
            _name, _reg, db = datasets.load_config(datasets.bundled_config_dir() / f"{stem}.json")
            assert db is not None
            self.assertEqual(db, (self.home / expected).resolve())


if __name__ == "__main__":
    unittest.main()
