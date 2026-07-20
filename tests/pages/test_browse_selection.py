"""Tests for Browse's read-only, tick-based Annotator selection.

Browse no longer records verdicts or notes — it only *selects* videos for the
Annotator. Clicking a row's header line toggles that selection and persists it to the
review store, painting a check tick like the quick-selection grid box. These tests
build a real row card and fire the header's click handler, asserting the store flag
flips, so the selection wiring is covered end to end rather than by inspection.
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
from annie.pages import annotator, browse
from tests.pages._nicegui import quiet_slow_callback_warnings, ui_client


class TestBrowseSelectionTick(unittest.IsolatedAsyncioTestCase):
    """Async, because toggling selection refreshes the Annotator's availability
    refreshable, which schedules its rebuild on ``core.loop`` as the running app does."""

    async def asyncSetUp(self) -> None:
        self._saved_loop = core.loop
        core.loop = asyncio.get_running_loop()
        quiet_slow_callback_warnings()

    async def asyncTearDown(self) -> None:
        core.loop = self._saved_loop

    def setUp(self) -> None:
        self._saved_store = state.store
        self._saved_scan = state.scan
        self.tmp = Path(tempfile.mkdtemp())
        state.store = ReviewStore(self.tmp / "annie.db")
        state.scan = ScanResult(entries=[VideoEntry(video_id="v1", row_id=1)])

    def tearDown(self) -> None:
        state.store = self._saved_store
        state.scan = self._saved_scan

    @staticmethod
    def _fire_clicks(target: ui.element) -> None:
        """Invoke every click handler registered on ``target``."""
        handlers = [
            listener.handler
            for listener in list(target._event_listeners.values())  # noqa: SLF001
            if listener.type == "click"
        ]
        event = ClickEventArguments(sender=target, client=target.client)
        for handler in handlers:
            # NiceGUI adapts handler arity at dispatch; mirror that for zero-arg lambdas.
            try:
                handler(event)
            except TypeError:
                handler()

    def _selection_corner(self, root: ui.element) -> ui.element:
        """The row's top-right selection corner — the cursor-pointer div with a click."""
        return next(
            el
            for el in root.descendants()
            if "cursor-pointer" in el._classes  # noqa: SLF001
            and any(li.type == "click" for li in el._event_listeners.values())  # noqa: SLF001
        )

    async def test_corner_click_toggles_selection_and_respects_store(self) -> None:
        """One row, driven through: pre-seeded selected → click deselects → click selects.

        Kept as a single method because the Annotator's module-level refreshable holds
        slot state across methods (the same reason the dequeue regression test is one
        method); one flow still exercises both the paint-from-store read and the write.
        """
        entry = state.scan.entries[0]
        # Pre-seed the store as *selected* so the row paints its tick from stored state.
        state.store.set_annotate(entry.key, entry.video_id, None, True)
        with ui_client(), ui.column() as root:
            # Mount the Annotator body so its availability refreshable has a real slot
            # to rebuild into, exactly as annotator.render() does in the running app.
            annotator._content()  # noqa: SLF001
            browse._row_card(entry, can_decode=False)  # noqa: SLF001
            corner = self._selection_corner(root)

            self.assertIn(entry.key, state.store.annotator_keys())  # starts selected

            self._fire_clicks(corner)  # → deselect
            self.assertNotIn(entry.key, state.store.annotator_keys())

            self._fire_clicks(corner)  # → select again
            self.assertIn(entry.key, state.store.annotator_keys())


if __name__ == "__main__":
    unittest.main()
