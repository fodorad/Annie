"""A queue change made outside the Annotator updates its tab, not its (hidden) body."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from nicegui import core, ui

from annie.core.models import VideoEntry
from annie.core.state import state
from annie.dataset.scanning import ScanResult
from annie.dataset.sources import SourceRegistry
from annie.dataset.storage import ReviewStore
from annie.pages import annotator
from tests.pages._nicegui import quiet_slow_callback_warnings, ui_client


class TestAvailability(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved = (core.loop, state.store, state.scan, state.registry, dict(annotator._tabs))
        core.loop = asyncio.get_running_loop()
        quiet_slow_callback_warnings()
        tmp = Path(tempfile.mkdtemp())
        state.store = ReviewStore(tmp / "a.db")
        self.entry = VideoEntry("clip", tmp / "clip.mp4")
        state.scan = ScanResult(entries=[self.entry])
        state.registry = SourceRegistry()

    async def asyncTearDown(self) -> None:
        core.loop, state.store, state.scan, state.registry, tabs = self._saved
        annotator._tabs.clear()
        annotator._tabs.update(tabs)

    def _body_ids(self, client) -> set[int]:  # noqa: ANN001
        return {el.id for el in client.layout.descendants()}

    async def test_tab_updates_but_body_is_kept(self) -> None:
        with ui_client() as client:
            with ui.tabs():
                tab = ui.tab("annotator")
            annotator.set_tab(tab)
            annotator.render()
            before = self._body_ids(client)
            state.store.set_annotate(self.entry.key, self.entry.video_id, None, True)
            annotator.update_availability()
            await asyncio.sleep(0.05)
            self.assertFalse(bool(tab.props.get("disable")))
            self.assertEqual(self._body_ids(client), before)  # nothing rebuilt

    async def test_rebuild_redraws_the_body(self) -> None:
        with ui_client() as client:
            with ui.tabs():
                tab = ui.tab("annotator")
            annotator.set_tab(tab)
            annotator.render()
            before = self._body_ids(client)
            annotator.update_availability(rebuild=True)
            await asyncio.sleep(0.05)
            self.assertNotEqual(self._body_ids(client), before)


if __name__ == "__main__":
    unittest.main()
