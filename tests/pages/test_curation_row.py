"""Tests for the Annotator Curation row's verdict persistence.

The Curation task carries the like/dislike/note supervision that used to live on Browse
rows. This drives the real buttons on a built row (media decode is skipped) and asserts
the verdict is written to the review store.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from nicegui import core, ui
from nicegui.events import ClickEventArguments

from annie.core.models import VideoEntry
from annie.core.state import state
from annie.dataset.scanning import ScanResult
from annie.dataset.storage import ReviewStore
from annie.pages import annotator
from tests.pages._nicegui import quiet_slow_callback_warnings, ui_client


class TestCurationRowVerdict(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved_loop = core.loop
        core.loop = asyncio.get_running_loop()
        quiet_slow_callback_warnings()

    async def asyncTearDown(self) -> None:
        core.loop = self._saved_loop

    def setUp(self) -> None:
        self._saved = (state.store, state.scan)
        self.tmp = Path(tempfile.mkdtemp())
        state.store = ReviewStore(self.tmp / "annie.db")
        # No video_path → media decode is skipped, so the row builds without ffmpeg.
        state.scan = ScanResult(entries=[VideoEntry(video_id="v1", row_id=1)])

    def tearDown(self) -> None:
        state.store, state.scan = self._saved

    @staticmethod
    def _click(button: ui.button) -> None:
        for listener in list(button._event_listeners.values()):  # noqa: SLF001
            if listener.type == "click":
                event = ClickEventArguments(sender=button, client=button.client)
                try:
                    listener.handler(event)
                except TypeError:
                    listener.handler()

    async def test_dislike_then_like_persists_verdict(self) -> None:
        entry = state.scan.entries[0]
        with ui_client(), ui.column() as root:
            annotator._curation_row(entry)  # noqa: SLF001
            buttons = [el for el in root.descendants() if isinstance(el, ui.button)]
            like = next(b for b in buttons if b._props.get("icon") == "thumb_up")  # noqa: SLF001
            dislike = next(b for b in buttons if b._props.get("icon") == "thumb_down")  # noqa: SLF001

            self._click(dislike)
            record = state.store.get(entry.key)
            assert record is not None
            self.assertEqual(record.verdict, "bad")

            self._click(like)
            record = state.store.get(entry.key)
            assert record is not None
            self.assertEqual(record.verdict, "good")


if __name__ == "__main__":
    unittest.main()
