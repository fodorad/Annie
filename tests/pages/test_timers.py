"""Guard against NiceGUI < 3.16 timers crashing when their container is rebuilt.

Annie rebuilds refreshable views (e.g. the event timeline with its 0.1 s playhead poll)
while their ``ui.timer`` may not have ticked yet. NiceGUI before 3.16 then logged
``RuntimeError: The parent slot of the element has been deleted`` as an unhandled
exception; the pinned minimum version stops the loop quietly instead.
"""

from __future__ import annotations

import asyncio
import gc
import unittest

from nicegui import ui

from tests.pages._nicegui import ui_client


class TestTimerInDeletedContainer(unittest.IsolatedAsyncioTestCase):
    async def test_a_timer_whose_container_is_gone_stops_quietly(self) -> None:
        with ui_client() as client:
            client.tab_id = "tab"  # behave as a connected browser tab
            box = ui.column()
            with box:
                timer = ui.timer(0.01, lambda: None)
            loop = timer._run_in_loop()  # noqa: SLF001 - drive the loop without a server
            box.delete()
            del box
            gc.collect()
            await asyncio.wait_for(loop, timeout=1)  # raised RuntimeError on NiceGUI 3.14


if __name__ == "__main__":
    unittest.main()
