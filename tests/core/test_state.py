"""Tests for the session-only UI preference defaults and last-config restore."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.core.config import settings
from annie.core.state import AppState, UiSettings
from annie.dataset import datasets
from annie.dataset.sources import DataSource, SourceKind, SourceRegistry
from annie.dataset.storage import ReviewStore


class TestUiSettings(unittest.TestCase):
    def test_defaults(self) -> None:
        ui = UiSettings()
        self.assertEqual(ui.browse_row_height, 135)
        self.assertEqual(ui.annotator_row_height, 200)
        self.assertTrue(ui.auto_scroll, "auto-scroll is on unless the user opts out")
        self.assertEqual(ui.page_size, 10)

    def test_fields_are_mutable_per_session(self) -> None:
        ui = UiSettings()
        ui.auto_scroll = False
        ui.page_size = 3
        self.assertFalse(ui.auto_scroll)
        self.assertEqual(ui.page_size, 3)


class TestRestoreLastConfig(unittest.TestCase):
    """Restarting Annie reopens the last config together with its review database."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._saved_home = settings.annie_home
        settings.annie_home = self.tmp / "home"
        self.state = AppState(registry=SourceRegistry(), store=ReviewStore(self.tmp / "env.db"))

    def tearDown(self) -> None:
        settings.annie_home = self._saved_home

    def _save_config(self) -> tuple[Path, Path]:
        registry = SourceRegistry()
        registry.add(DataSource(SourceKind.VIDEO, self.tmp / "video"))
        db = settings.annie_home / "annie_mydata.db"
        config = datasets.save_config(self.tmp / "mydata.json", registry, "My data", db_path=db)
        return config, db

    def test_nothing_to_restore(self) -> None:
        self.assertIsNone(self.state.restore_last_config())
        self.assertIsNone(self.state.active_config)
        self.assertEqual(self.state.store.db_path, self.tmp / "env.db")

    def test_restores_sources_and_database(self) -> None:
        config, db = self._save_config()
        ReviewStore(db).set_verdict("v::", "v", None, "bad")  # progress from the last run
        datasets.remember_last_config(config)

        restored = self.state.restore_last_config()

        self.assertEqual(restored, config.resolve())
        self.assertEqual(self.state.active_config, config.resolve())
        self.assertEqual([s.kind for s in self.state.registry.sources], [SourceKind.VIDEO])
        record = self.state.store.get("v::")
        assert record is not None
        self.assertEqual(record.verdict, "bad")

    def test_broken_config_is_skipped(self) -> None:
        config = self.tmp / "broken.json"
        config.write_text("{not json", encoding="utf-8")
        datasets.remember_last_config(config)

        self.assertIsNone(self.state.restore_last_config())
        self.assertEqual(self.state.store.db_path, self.tmp / "env.db")


if __name__ == "__main__":
    unittest.main()
