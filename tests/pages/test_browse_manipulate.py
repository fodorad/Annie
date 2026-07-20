"""Tests for the Browse Manipulate block's display/filter split.

A column transform always drives the *filter* facets, but ``show_original`` decides what
the sample rows display. These drive the real label helpers against a scanned manifest to
pin that separation down — the case that motivated the flag is thresholding a ``[-3, 3]``
sentiment into a two-way facet while still reading the true float on each row.
"""

from __future__ import annotations

import unittest

from annie.core.models import VideoEntry
from annie.core.state import state
from annie.dataset.manipulate import Transform
from annie.dataset.scanning import ScanResult
from annie.pages import browse
from tests.pages._nicegui import ui_client


class TestDisplayVersusEffectiveLabel(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_scan = state.scan
        self.entry = VideoEntry(video_id="v1", row_id=1, labels={"sentiment": "1.83"})
        state.scan = ScanResult(
            entries=[self.entry],
            label_columns=["sentiment"],
            label_column_types={"sentiment": "float"},  # a threshold only applies to numbers
        )

    def tearDown(self) -> None:
        state.scan = self._saved_scan
        browse._browse_state.clear()  # noqa: SLF001

    def test_untransformed_column_shows_the_raw_value(self) -> None:
        with ui_client():
            self.assertEqual(browse._display_label(self.entry, "sentiment"), "1.83")  # noqa: SLF001
            self.assertEqual(browse._effective_label(self.entry, "sentiment"), "1.83")  # noqa: SLF001

    def test_transform_drives_both_when_show_original_is_off(self) -> None:
        with ui_client():
            browse._state().transforms["sentiment"] = Transform(  # noqa: SLF001
                kind="threshold", threshold=0.0
            )
            # The filter facet and the row tag agree: both are the thresholded value.
            effective = browse._effective_label(self.entry, "sentiment")  # noqa: SLF001
            self.assertEqual(browse._display_label(self.entry, "sentiment"), effective)  # noqa: SLF001
            self.assertNotEqual(effective, "1.83")

    def test_show_original_splits_the_row_tag_from_the_filter(self) -> None:
        with ui_client():
            browse._state().transforms["sentiment"] = Transform(  # noqa: SLF001
                kind="threshold", threshold=0.0, show_original=True
            )
            # The row shows the real float ...
            self.assertEqual(browse._display_label(self.entry, "sentiment"), "1.83")  # noqa: SLF001
            # ... while the filter still matches on the thresholded value.
            self.assertNotEqual(browse._effective_label(self.entry, "sentiment"), "1.83")  # noqa: SLF001

    def test_missing_label_is_none_either_way(self) -> None:
        with ui_client():
            browse._state().transforms["sentiment"] = Transform(  # noqa: SLF001
                kind="threshold", threshold=0.0, show_original=True
            )
            other = VideoEntry(video_id="v2", row_id=2)
            self.assertIsNone(browse._display_label(other, "sentiment"))  # noqa: SLF001
            self.assertIsNone(browse._effective_label(other, "sentiment"))  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
