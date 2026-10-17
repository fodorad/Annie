"""Tabs are built the first time they are opened, and only once."""

from __future__ import annotations

import unittest

from nicegui import ui

from annie.pages.tabs import LazyTabs
from tests.pages._nicegui import ui_client


class TestLazyTabs(unittest.TestCase):
    def test_only_opened_tabs_are_built_and_only_once(self) -> None:
        built: list[str] = []
        with ui_client():
            with ui.tabs() as tabs:
                ui.tab("a")
                ui.tab("b")
            with ui.tab_panels(tabs, value="a"):
                lazy = LazyTabs({"a": lambda: built.append("a"), "b": lambda: built.append("b")})
            self.assertEqual(built, [])
            self.assertTrue(lazy.open("a"))
            self.assertFalse(lazy.open("a"))  # already built
            self.assertEqual(built, ["a"])
            self.assertFalse(lazy.is_built("b"))
            self.assertTrue(lazy.open("b"))
            self.assertEqual(built, ["a", "b"])
            self.assertFalse(lazy.open("unknown"))


if __name__ == "__main__":
    unittest.main()
