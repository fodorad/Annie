"""Tests for the session-only UI preference defaults and last-config restore."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.app_state import AppState, UiSettings
from annie.core.config import settings
from annie.dataset import datasets
from annie.dataset.sources import DataSource, SourceKind, SourceRegistry
from annie.dataset.storage import ReviewStore


class TestUiSettings(unittest.TestCase):
    def test_defaults(self) -> None:
        ui = UiSettings()
        self.assertEqual(ui.browse_row_height, 110)
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


class TestProtagonistCorrectionsFollowTheDb(unittest.TestCase):
    """Switching review DB shows that DB's protagonist corrections at once.

    They used to be layered on only by a rescan or a restart, so after a switch the
    videos kept the previous DB's corrections (or lacked the new DB's).
    """

    def setUp(self) -> None:
        from annie.core.models import VideoEntry
        from annie.dataset.scanning import ScanResult

        self.tmp = Path(tempfile.mkdtemp())
        self.state = AppState(registry=SourceRegistry(), store=ReviewStore(self.tmp / "a.db"))
        self.corrected = VideoEntry("corrected", active_track_id=2)
        self.untouched = VideoEntry("untouched", active_track_id=1)
        self.state.scan = ScanResult(entries=[self.corrected, self.untouched])
        # What rescan() records: the protagonist from the heuristic CSV.
        self.state.scanned_tracks = {"corrected": 2, "untouched": 1}
        self.db_a = self.tmp / "a.db"
        self.db_b = self.tmp / "b.db"
        ReviewStore(self.db_a).set_active_track("corrected", "corrected", None, 5)
        ReviewStore(self.db_b).set_active_track("untouched", "untouched", None, 7)

    def _tracks(self) -> tuple[int, int]:
        return self.corrected.active_track_id, self.untouched.active_track_id

    def test_switching_db_swaps_the_corrections(self) -> None:
        self.state.set_store(self.db_a)
        self.assertEqual(self._tracks(), (5, 1))
        self.state.set_store(self.db_b)
        self.assertEqual(self._tracks(), (2, 7), "a.db's correction must not linger")
        self.state.set_store(self.db_a)
        self.assertEqual(self._tracks(), (5, 1))

    def test_a_db_without_corrections_restores_the_heuristic(self) -> None:
        self.state.set_store(self.db_a)
        self.state.set_store(self.tmp / "fresh.db")
        self.assertEqual(self._tracks(), (2, 1))

    def test_no_scan_yet_is_fine(self) -> None:
        self.state.scan = None
        self.state.set_store(self.db_a)  # must not raise
        self.assertIsNone(self.state.scan)


if __name__ == "__main__":
    unittest.main()


class TestScanningFlag(unittest.TestCase):
    """Every scan, the start-up one included, is visible as ``state.scanning``."""

    def test_flag_is_up_during_a_scan_and_down_after(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        seen: list[bool] = []
        app = AppState(registry=SourceRegistry(), store=ReviewStore(tmp / "a.db"))

        class _Spy(SourceRegistry):
            @property
            def video(self):  # noqa: ANN202 - read during the scan
                seen.append(app.scanning)
                return None

        app.registry = _Spy()
        self.assertFalse(app.scanning)
        app.rescan()
        self.assertEqual(seen[:1], [True])
        self.assertFalse(app.scanning)


class TestReviewLookup(unittest.TestCase):
    """One query serves a whole filter pass, with the same answers as per-row reads."""

    def test_batched_lookup_matches_per_row_reads(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        app = AppState(registry=SourceRegistry(), store=ReviewStore(tmp / "r.db"))
        app.store.set_verdict("bad", "bad", None, "bad")
        app.store.set_note("noted", "noted", None, "check audio")
        app.store.set_annotate("queued", "queued", None, True)
        lookup = app.review_lookup()
        for key in ("bad", "noted", "queued", "never-touched"):
            self.assertEqual(lookup(key), app.review_state(key), key)
