"""Rare heavy actions run off the event loop, and the Maintenance card re-measures after."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from nicegui import core

from annie.core.config import settings
from annie.dataset import datasets
from annie.dataset.sources import SourceRegistry
from annie.dataset.storage import ReviewStore
from annie.pages import dataset
from annie.pages import settings as settings_page
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
    def test_saving_a_config_shows_its_new_name(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        path = datasets.save_config(tmp / "c.json", SourceRegistry(), "abc")
        self.assertEqual(datasets.config_name(path), "abc")
        datasets.save_config(tmp / "c.json", SourceRegistry(), "xyz")  # same size
        self.assertEqual(datasets.config_name(path), "xyz")


if __name__ == "__main__":
    unittest.main()
