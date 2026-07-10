"""Tests for dataset config save/load/discover."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from annie.dataset import datasets
from annie.dataset.sources import CsvRole, DataSource, SourceKind, SourceRegistry


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

    def test_bundled_example_config_is_valid(self) -> None:
        bundled = datasets.bundled_config_dir() / "mosei_mini.json"
        self.assertTrue(bundled.is_file(), "the bundled example config should exist")
        name, reg, _db = datasets.load_config(bundled)
        self.assertEqual(name, "[Example] CMU-MOSEI Mini")
        self.assertTrue(reg.has_video)  # resolves to the bundled example videos
        self.assertEqual(len(reg.label_sources), 1)
        self.assertEqual(reg.label_sources[0].column_types.get("sentiment"), "float")


if __name__ == "__main__":
    unittest.main()
