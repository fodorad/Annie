"""Typed filters apply after a pause, and each browser tab keeps its own Log filter."""

from __future__ import annotations

import unittest

from nicegui import ui

from annie.core.logbook import LOG
from annie.pages import logs
from annie.pages.utils import DEBOUNCE
from tests.pages._nicegui import ui_client


def _inputs(client) -> list[ui.input]:  # noqa: ANN001
    return [el for el in client.layout.descendants() if isinstance(el, ui.input)]


class TestLogFilter(unittest.TestCase):
    def test_each_tab_has_its_own_debounced_filter(self) -> None:
        with ui_client() as tab_a:
            logs.render()
            logs._filter().text = "decode"  # noqa: SLF001
            (text_a,) = _inputs(tab_a)
            self.assertIn("debounce", text_a.props)
            self.assertEqual(str(text_a.props["debounce"]), DEBOUNCE.split("=")[1])
        with ui_client():
            logs.render()
            self.assertEqual(logs._filter().text, "")  # noqa: SLF001 - tab B unaffected

    def test_filtered_matches_level_and_text(self) -> None:
        start = LOG.latest_seq()
        LOG.add("error", "Could not decode clip_a")
        LOG.add("info", "Scan finished")
        new = [e for e in logs.filtered(logs.LogFilter()) if e.seq > start]
        self.assertEqual(len(new), 2)
        errors = logs.filtered(logs.LogFilter(level="error", text="clip_a"))
        self.assertTrue(errors)
        self.assertTrue(all(e.level == "error" and "clip_a" in e.message for e in errors))


if __name__ == "__main__":
    unittest.main()
