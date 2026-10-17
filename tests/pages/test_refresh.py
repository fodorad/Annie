"""A refresh redraws only the browser tab that acted; data-wide changes redraw them all."""

from __future__ import annotations

import asyncio
import unittest

from nicegui import core, ui

from annie.pages.refresh import client_refreshable
from tests.pages._nicegui import quiet_slow_callback_warnings, ui_client

_builds: list[str] = []


@client_refreshable
def _view(name: str) -> None:
    _builds.append(name)
    ui.label(name)


class TestClientRefresh(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved_loop = core.loop
        core.loop = asyncio.get_running_loop()
        quiet_slow_callback_warnings()
        _builds.clear()

    async def asyncTearDown(self) -> None:
        core.loop = self._saved_loop
        _view.targets.clear()

    async def test_a_refresh_redraws_only_the_acting_tab(self) -> None:
        with ui_client() as tab_a:
            _view("a")
        with ui_client() as tab_b:
            _view("b")
        _builds.clear()
        with tab_a:
            await _view.refresh()
        self.assertEqual(_builds, ["a"])
        with tab_b:
            await _view.refresh()
        self.assertEqual(_builds, ["a", "b"])

    async def test_outside_any_tab_every_tab_redraws(self) -> None:
        with ui_client():
            _view("a")
        with ui_client():
            _view("b")
        _builds.clear()
        await asyncio.get_running_loop().create_task(_refresh_from_background())
        self.assertEqual(sorted(_builds), ["a", "b"])

    async def test_refresh_everywhere_redraws_every_tab(self) -> None:
        with ui_client() as tab_a:
            _view("a")
        with ui_client():
            _view("b")
        _builds.clear()
        with tab_a:
            await _view.refresh_everywhere()
        self.assertEqual(sorted(_builds), ["a", "b"])


async def _refresh_from_background() -> None:
    """A refresh from a task with no page context (startup, a background service)."""
    await _view.refresh()


if __name__ == "__main__":
    unittest.main()
