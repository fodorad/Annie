"""Tests for box colours, overlay drawing, and click hit-testing."""

from __future__ import annotations

import unittest

import numpy as np
from PIL import Image

from annie.core.models import BBox, FrameAnnotation
from annie.media.color import (
    BLUE,
    GREEN,
    TRACK_PALETTE,
    box_color,
    color_for_track,
    draw_overlay,
    hit_test,
)


class TestColorForTrack(unittest.TestCase):
    def test_active_track_is_green(self) -> None:
        self.assertEqual(color_for_track(5, active=True), GREEN)

    def test_deterministic_per_id(self) -> None:
        self.assertEqual(color_for_track(3), color_for_track(3))

    def test_palette_excludes_reserved_colors(self) -> None:
        self.assertNotIn(BLUE, TRACK_PALETTE)
        self.assertNotIn(GREEN, TRACK_PALETTE)
        for track_id in range(50):
            color = color_for_track(track_id)
            self.assertNotEqual(color, BLUE)
            self.assertNotEqual(color, GREEN)


class TestBoxColor(unittest.TestCase):
    def test_vdet_only_is_blue(self) -> None:
        box = BBox(0, 0, 10, 10, 0.9, track_id=None)
        self.assertEqual(box_color(box, has_tracks=False, active_track_id=None), BLUE)

    def test_tracked_box_uses_palette(self) -> None:
        box = BBox(0, 0, 10, 10, 0.9, track_id=2)
        self.assertEqual(box_color(box, has_tracks=True, active_track_id=None), color_for_track(2))

    def test_active_track_overrides_to_green(self) -> None:
        box = BBox(0, 0, 10, 10, 0.9, track_id=2)
        self.assertEqual(box_color(box, has_tracks=True, active_track_id=2), GREEN)


class TestDrawOverlay(unittest.TestCase):
    def test_returns_image_without_mutating_input(self) -> None:
        frame = np.zeros((40, 60, 3), dtype=np.uint8)
        annotation = FrameAnnotation(0, [BBox(5, 5, 20, 20, 0.9, track_id=0)])
        image = draw_overlay(frame, annotation, has_tracks=True, active_track_id=0)
        self.assertIsInstance(image, Image.Image)
        self.assertEqual(image.size, (60, 40))
        self.assertTrue(np.all(frame == 0))  # original untouched

    def test_draws_some_colored_pixels(self) -> None:
        frame = np.zeros((40, 60, 3), dtype=np.uint8)
        annotation = FrameAnnotation(0, [BBox(5, 5, 20, 20, 0.9, track_id=None)])
        image = draw_overlay(frame, annotation, has_tracks=False)
        self.assertGreater(int(np.asarray(image).sum()), 0)


class TestHitTest(unittest.TestCase):
    def test_returns_none_when_no_box_hit(self) -> None:
        annotation = FrameAnnotation(0, [BBox(0, 0, 10, 10, 0.9, track_id=1)])
        self.assertIsNone(hit_test((50, 50), annotation))

    def test_smallest_box_wins_on_overlap(self) -> None:
        big = BBox(0, 0, 100, 100, 0.9, track_id=1)
        small = BBox(40, 40, 20, 20, 0.9, track_id=2)
        annotation = FrameAnnotation(0, [big, small])
        self.assertEqual(hit_test((45, 45), annotation), 2)

    def test_largest_box_wins_when_configured(self) -> None:
        big = BBox(0, 0, 100, 100, 0.9, track_id=1)
        small = BBox(40, 40, 20, 20, 0.9, track_id=2)
        annotation = FrameAnnotation(0, [big, small])
        self.assertEqual(hit_test((45, 45), annotation, smallest_wins=False), 1)


if __name__ == "__main__":
    unittest.main()
