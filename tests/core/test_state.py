"""Tests for the session-only UI preference defaults."""

from __future__ import annotations

import unittest

from annie.core.state import UiSettings


class TestUiSettings(unittest.TestCase):
    def test_defaults(self) -> None:
        ui = UiSettings()
        self.assertEqual(ui.browse_row_height, 135)
        self.assertEqual(ui.annotator_row_height, 200)
        self.assertTrue(ui.auto_scroll, "auto-scroll is on unless the user opts out")
        self.assertEqual(ui.page_size, 10)

    def test_fields_are_mutable_per_session(self) -> None:
        ui = UiSettings()
        ui.auto_scroll = False
        ui.page_size = 3
        self.assertFalse(ui.auto_scroll)
        self.assertEqual(ui.page_size, 3)


if __name__ == "__main__":
    unittest.main()
