"""Tests for Browse's lazy ``#frames`` / ``audio`` badges: they spin, then settle."""

from __future__ import annotations

import unittest
from pathlib import Path

from nicegui import ui

from annie.core.models import VideoEntry
from annie.core.state import state
from annie.pages import browse
from tests.pages._nicegui import ui_client


def _texts(slot: ui.element) -> list[str]:
    return [el.text for el in slot.descendants() if el.tag == "q-badge"]


def _spins(slot: ui.element) -> bool:
    return any(el.tag == "q-spinner" for el in slot.descendants())


class TestLazyBadges(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (dict(state.audio_cache), dict(state.frames_cache))
        state.audio_cache.clear()
        state.frames_cache.clear()
        self.entry = VideoEntry("v1", Path("v1.mp4"))

    def tearDown(self) -> None:
        state.audio_cache.clear()
        state.frames_cache.clear()
        state.audio_cache.update(self._saved[0])
        state.frames_cache.update(self._saved[1])

    def test_unknown_values_spin(self) -> None:
        with ui_client():
            badges = browse._tags(self.entry)  # noqa: SLF001
            assert badges.frames is not None and badges.audio_slot is not None
            self.assertTrue(_spins(badges.frames))
            self.assertTrue(_spins(badges.audio_slot))

    def test_cached_values_show_at_once(self) -> None:
        state.audio_cache[self.entry.key] = True
        state.frames_cache[self.entry.key] = 42
        with ui_client():
            badges = browse._tags(self.entry)  # noqa: SLF001
            assert badges.frames is not None and badges.audio_slot is not None
            self.assertEqual(_texts(badges.frames), ["#frames: 42"])
            self.assertEqual(_texts(badges.audio_slot), ["audio"])
            self.assertFalse(_spins(badges.frames))

    def test_rows_that_cannot_decode_settle_as_unknown(self) -> None:
        with ui_client():
            badges = browse._tags(self.entry)  # noqa: SLF001
            browse._settle_unknown(badges)  # noqa: SLF001
            assert badges.frames is not None and badges.audio_slot is not None
            self.assertEqual(_texts(badges.frames), ["#frames: ?"])
            self.assertEqual(_texts(badges.audio_slot), ["audio: ?"])

    def test_no_audio_is_reported_plainly(self) -> None:
        with ui_client():
            slot = ui.element("span")
            browse._settle_audio(slot, False)  # noqa: SLF001
            self.assertEqual(_texts(slot), ["no audio"])


if __name__ == "__main__":
    unittest.main()
