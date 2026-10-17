"""Rare heavy actions run off the event loop, and the Maintenance card re-measures after."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from nicegui import core

from annie.core.config import settings
from annie.core.models import VideoEntry
from annie.core.state import state
from annie.dataset import corrections, datasets
from annie.dataset.scanning import ScanResult
from annie.dataset.sources import CsvRole, DataSource, SourceKind, SourceRegistry
from annie.dataset.storage import ReviewStore
from annie.pages import annotator, convert, dataset
from annie.pages import settings as settings_page
from annie.parsers.participants import load_participants
from tests.fixtures import write_participants
from tests.pages._nicegui import quiet_slow_callback_warnings, ui_client


class TestMaintenanceRemeasures(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved_loop = core.loop
        core.loop = asyncio.get_running_loop()
        quiet_slow_callback_warnings()

    async def asyncTearDown(self) -> None:
        core.loop = self._saved_loop

    async def test_after_a_cleanup_the_card_measures_again(self) -> None:
        stale = settings_page._Space([], [], 123, 456, 789)  # noqa: SLF001
        with ui_client() as client:
            settings_page._maintenance_section(stale)  # noqa: SLF001
            self.assertFalse(any(el.tag == "q-spinner" for el in client.layout.descendants()))
            await settings_page._run_cleanup([])  # noqa: SLF001 - "Nothing to clean up."
            await asyncio.sleep(0.05)
            texts = [getattr(el, "text", "") for el in client.layout.descendants()]
        # Rebuilt without the old sizes: either measuring (spinner) or freshly measured.
        self.assertNotIn("789 B", texts)


class TestCleanupChoice(unittest.TestCase):
    def test_only_what_has_something_to_remove(self) -> None:
        log = Path(tempfile.mkdtemp()) / "old.log"
        full = settings_page._Space([log], [log], 1, 1, 5)  # noqa: SLF001
        self.assertEqual(
            settings_page.cleanup_actions(full),
            [
                settings_page._clear_logs,  # noqa: SLF001
                settings_page._clear_session_dbs,  # noqa: SLF001
                settings_page._clear_render_cache,  # noqa: SLF001
            ],
        )
        empty = settings_page._Space([], [], 0, 0, 0)  # noqa: SLF001
        self.assertEqual(settings_page.cleanup_actions(empty), [])


class TestOffLoopActions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved = (core.loop, state.store, state.scan, state.registry, state.active_config)
        core.loop = asyncio.get_running_loop()
        quiet_slow_callback_warnings()
        self.tmp = Path(tempfile.mkdtemp())
        state.store = ReviewStore(self.tmp / "annie.db")

    async def asyncTearDown(self) -> None:
        core.loop, state.store, state.scan, state.registry, state.active_config = self._saved

    async def test_convert_exports_write_the_files(self) -> None:
        with ui_client():
            ids = await convert._write_export(self.tmp, ["a.mp4", "b.mp4"], [])  # noqa: SLF001
            failed = await convert._write_export(self.tmp, [], [("c.mp4", "boom")])  # noqa: SLF001
            nothing = await convert._write_export(self.tmp, [], [])  # noqa: SLF001
        assert ids is not None and failed is not None
        self.assertIn("a", ids.read_text(encoding="utf-8"))
        self.assertIn("boom", failed.read_text(encoding="utf-8"))
        self.assertIsNone(nothing)

    async def test_clear_queue_dequeues_everything_in_one_go(self) -> None:
        entries = [VideoEntry(f"v{i}", self.tmp / f"v{i}.mp4") for i in range(3)]
        state.scan = ScanResult(entries=entries)
        for e in entries:
            state.store.set_annotate(e.key, e.video_id, None, True)
        with ui_client():
            annotator._clear_queue(entries)  # noqa: SLF001
        self.assertEqual(state.store.annotator_keys(), set())

    async def test_protagonist_export_writes_the_manual_csv(self) -> None:
        source = write_participants(self.tmp / "protagonist.csv", {"v1": 0})
        state.registry = SourceRegistry()
        state.registry.add(
            DataSource(
                SourceKind.CSV,
                source,
                role=CsvRole.PROTAGONIST,
                key_column="uuid",
                value_columns=("track_id",),
            )
        )
        state.store.set_active_track("v1", "v1", None, 2)
        with ui_client():
            await annotator._export()  # noqa: SLF001
        manual = corrections.manual_sibling(source)
        self.assertEqual(load_participants(manual), {"v1": 2})

    async def test_loading_a_config_applies_it(self) -> None:
        config = datasets.save_config(self.tmp / "c.json", SourceRegistry(), "mine")
        with ui_client():
            await dataset._load_config_path(config)  # noqa: SLF001
            await dataset._load_config_path(self.tmp / "broken.json")  # noqa: SLF001 - reported
        self.assertEqual(state.active_config, config.resolve())


class TestCarryProgress(unittest.TestCase):
    def test_old_target_is_set_aside_and_progress_copied(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        saved_sessions = settings.sessions_dir
        settings.sessions_dir = tmp / "sessions"  # never the user's real sessions folder
        self.addCleanup(setattr, settings, "sessions_dir", saved_sessions)
        current = ReviewStore(tmp / "current.db")
        current.set_verdict("v1", "v1", None, "bad")
        target = tmp / "config.db"
        ReviewStore(target).set_verdict("old", "old", None, "bad")
        dataset._carry_progress(current, target)  # noqa: SLF001
        self.assertEqual(ReviewStore(target).get("v1").verdict, "bad")  # type: ignore[union-attr]
        self.assertIsNone(ReviewStore(target).get("old"))
        aside = list(settings.sessions_dir.glob("config_replaced_*.db"))
        self.assertEqual(len(aside), 1)


class TestConfigNameCache(unittest.TestCase):
    def test_a_missing_file_is_named_after_its_stem(self) -> None:
        self.assertEqual(datasets.config_name(Path(tempfile.mkdtemp()) / "gone.json"), "gone")

    def test_saving_a_config_shows_its_new_name(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        path = datasets.save_config(tmp / "c.json", SourceRegistry(), "abc")
        self.assertEqual(datasets.config_name(path), "abc")
        datasets.save_config(tmp / "c.json", SourceRegistry(), "xyz")  # same size
        self.assertEqual(datasets.config_name(path), "xyz")


if __name__ == "__main__":
    unittest.main()
