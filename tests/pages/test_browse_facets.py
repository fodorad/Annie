"""Tests for the Browse filter bar's scalar facet selects.

Every facet box uses one idiom: empty means *off*. There is no "any" option — clearing the
box is what turns the facet off — but ``"any"`` is still the value stored on the
:class:`~annie.dataset.filtering.FilterSpec`, so these pin the UI↔spec mapping in both
directions.
"""

from __future__ import annotations

import unittest

from nicegui import ui

from annie.core.state import state
from annie.dataset.scanning import ScanResult
from annie.pages import browse
from tests.pages._nicegui import ui_client

_VIDEO_OPTIONS = {"has": "has video", "missing": "no video"}


class TestFacetSelect(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_scan = state.scan
        state.scan = ScanResult(entries=[])
        self.changes = 0

    def tearDown(self) -> None:
        state.scan = self._saved_scan
        browse._browse_state.clear()  # noqa: SLF001

    def _on_change(self) -> None:
        self.changes += 1

    def test_offers_no_any_option(self) -> None:
        with ui_client(), ui.column():
            select = browse._facet_select("video", "video", _VIDEO_OPTIONS, self._on_change)  # noqa: SLF001
            self.assertNotIn("any", select.options)
            self.assertEqual(set(select.options), {"has", "missing"})

    def test_an_off_facet_renders_empty(self) -> None:
        with ui_client(), ui.column():
            # A fresh spec has video="any" — the box must show nothing, not "any".
            select = browse._facet_select("video", "video", _VIDEO_OPTIONS, self._on_change)  # noqa: SLF001
            self.assertIsNone(select.value)

    def test_a_set_facet_renders_its_value(self) -> None:
        with ui_client(), ui.column():
            browse._state().spec.video = "missing"  # noqa: SLF001
            select = browse._facet_select("video", "video", _VIDEO_OPTIONS, self._on_change)  # noqa: SLF001
            self.assertEqual(select.value, "missing")

    def test_choosing_a_value_writes_the_spec(self) -> None:
        with ui_client(), ui.column():
            select = browse._facet_select("video", "video", _VIDEO_OPTIONS, self._on_change)  # noqa: SLF001
            select.value = "has"
            self.assertEqual(browse._state().spec.video, "has")  # noqa: SLF001
            self.assertEqual(self.changes, 1)

    def test_clearing_turns_the_facet_off(self) -> None:
        with ui_client(), ui.column():
            browse._state().spec.video = "has"  # noqa: SLF001
            select = browse._facet_select("video", "video", _VIDEO_OPTIONS, self._on_change)  # noqa: SLF001
            select.value = None  # the clearable "x"
            # Cleared maps back to the spec's off value, so the filter stops applying.
            self.assertEqual(browse._state().spec.video, "any")  # noqa: SLF001
            self.assertFalse(browse._state().spec.is_active)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
