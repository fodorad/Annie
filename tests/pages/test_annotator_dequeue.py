"""Regression tests for the Annotator's X button tearing down its own slot.

Clicking X deletes the card the button lives in. ``ui.notify`` resolves its client
through the *parent* of the sender, so any toast raised after that deletion dies with
"the parent element this slot belongs to has been deleted". These tests click the
real buttons on real rows, with NiceGUI's exception handler made to re-raise, so the
crash would surface rather than being swallowed into the log.
"""

from __future__ import annotations

import asyncio
import gc
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


def _reraise(exc: BaseException) -> None:
    """Make NiceGUI surface handler exceptions instead of logging them."""
    raise exc


class TestDequeueDoesNotUseADeletedSlot(unittest.IsolatedAsyncioTestCase):
    """Async, because emptying the queue refreshes a NiceGUI refreshable, which
    schedules its rebuild on ``core.loop`` exactly as the running app does."""

    async def asyncSetUp(self) -> None:
        self._saved_loop = core.loop
        core.loop = asyncio.get_running_loop()

    async def asyncTearDown(self) -> None:
        core.loop = self._saved_loop

    def setUp(self) -> None:
        self._saved_handler = core.app.handle_exception
        core.app.handle_exception = _reraise
        self._saved_store = state.store
        self._saved_scan = state.scan

        # A real file: ReviewStore opens a connection per call, so ":memory:" would
        # hand every call its own empty database.
        self.tmp = Path(tempfile.mkdtemp())
        state.store = ReviewStore(self.tmp / "annie.db")
        state.scan = ScanResult(entries=self._entries())
        for entry in state.scan.entries:
            state.store.set_annotate(entry.key, entry.video_id, None, True)

    def tearDown(self) -> None:
        core.app.handle_exception = self._saved_handler
        state.store = self._saved_store
        state.scan = self._saved_scan

    @staticmethod
    def _entries() -> list[VideoEntry]:
        return [VideoEntry(video_id=f"v{i}", row_id=i) for i in (1, 2, 3)]

    def _click_first_x(self, root: ui.element) -> bool:
        """Click the topmost X button. Returns False when none remain."""
        buttons = [
            element
            for element in root.descendants()
            if isinstance(element, ui.button) and element._props.get("icon") == "close"  # noqa: SLF001
        ]
        if not buttons:
            return False
        button = buttons[0]
        # Snapshot: the handler mutates the element tree while we iterate it.
        handlers = [
            listener.handler
            for listener in list(button._event_listeners.values())  # noqa: SLF001
            if listener.type == "click"
        ]
        for handler in handlers:
            handler(ClickEventArguments(sender=button, client=button.client))
        gc.collect()  # drop the deleted row, so a stale slot weakref would die
        return True

    async def test_clicking_x_on_every_row_in_turn_never_touches_a_dead_slot(self) -> None:
        """Dequeue three rows back to back, as a reviewer clearing their backlog does.

        The last click empties the queue, taking the ``update_availability`` branch;
        the earlier two take the ``card.delete()`` branch that originally crashed.
        """
        with ui.column() as root:
            for entry in state.scan.entries:
                annotator._row_card(entry, can_decode=False, default_tid=-1)  # noqa: SLF001

        for expected_remaining in (2, 1, 0):
            self.assertTrue(self._click_first_x(root))
            self.assertEqual(len(state.store.annotator_keys()), expected_remaining)


if __name__ == "__main__":
    unittest.main()
