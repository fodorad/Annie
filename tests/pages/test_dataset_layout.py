"""Tests for the Dataset tab's Layout inputs (pure parsing; the widgets are browser-gated)."""

from __future__ import annotations

import unittest

from annie.pages.dataset import layout_from_inputs


class TestLayoutFromInputs(unittest.TestCase):
    def test_trims_and_maps_empty_id_to_default(self) -> None:
        layout = layout_from_inputs("  {a}/clip_{b}.mp4 ", "   ")
        self.assertEqual(layout.pattern, "{a}/clip_{b}.mp4")
        self.assertIsNone(layout.id_template)

    def test_keeps_a_custom_id_template(self) -> None:
        self.assertEqual(layout_from_inputs("{a}/{b}.mp4", " {b}_{a} ").id_template, "{b}_{a}")

    def test_invalid_input_raises_a_readable_error(self) -> None:
        for pattern, ident in (("", ""), ("plain.mp4", ""), ("{a}.mp4", "{x}")):
            with self.assertRaises(ValueError):
                layout_from_inputs(pattern, ident)


if __name__ == "__main__":
    unittest.main()
