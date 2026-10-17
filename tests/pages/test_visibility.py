"""Key handlers and pollers run only while their tab is shown."""

from __future__ import annotations

import unittest

from nicegui import ui

from annie.pages import visibility
from tests.pages._nicegui import ui_client


class TestVisibility(unittest.TestCase):
    def test_members_follow_the_shown_tab(self) -> None:
        with ui_client():
            visibility.show("annotator")
            keys = visibility.only_while_shown(ui.keyboard(on_key=lambda _e: None), "annotator")
            poll = visibility.only_while_shown(ui.timer(10, lambda: None), "annotator")
            self.assertTrue(keys.active and poll.active)
            visibility.show("browse")
            self.assertFalse(keys.active or poll.active)
            self.assertEqual(visibility.shown_tab(), "browse")
            visibility.show("annotator")
            self.assertTrue(keys.active and poll.active)

    def test_built_while_hidden_starts_inactive(self) -> None:
        with ui_client():
            visibility.show("dataset")
            keys = visibility.only_while_shown(ui.keyboard(on_key=lambda _e: None), "annotator")
            self.assertFalse(keys.active)

    def test_each_browser_tab_has_its_own_state(self) -> None:
        with ui_client():
            visibility.show("annotator")
            a = visibility.only_while_shown(ui.keyboard(on_key=lambda _e: None), "annotator")
        with ui_client():
            visibility.show("browse")
        self.assertTrue(a.active)  # another browser tab switching does not touch this one


if __name__ == "__main__":
    unittest.main()
