"""Tests for the pure timeline window model."""

from __future__ import annotations

import unittest

from annie.pages.timeline_view import TimelineView


def _view(zoom: float = 1.0, start: int = 0, total: int = 1001) -> TimelineView:
    return TimelineView(total_frames=total, zoom=zoom, start=start)


class TestWindow(unittest.TestCase):
    def test_unzoomed_shows_whole_clip(self) -> None:
        self.assertEqual(_view().window(), (0, 1000))
        self.assertFalse(_view().zoomed_in)

    def test_zoomed_span(self) -> None:
        v = _view(zoom=4.0, start=100)
        self.assertEqual(v.span, 250)
        self.assertEqual(v.window(), (100, 350))

    def test_window_is_independent_of_any_playhead(self) -> None:
        # Regression: the window used to be centred on the playhead, freezing the line.
        v = _view(zoom=4.0, start=100)
        before = v.window()
        for frame in (0, 120, 340, 900):
            v.fraction_of(frame)
        self.assertEqual(v.window(), before)

    def test_start_is_clamped(self) -> None:
        self.assertEqual(_view(zoom=4.0, start=-50).window(), (0, 250))
        self.assertEqual(_view(zoom=4.0, start=9999).window(), (750, 1000))

    def test_unknown_clip_does_not_divide_by_zero(self) -> None:
        v = TimelineView(total_frames=0, zoom=5.0)
        self.assertEqual(v.window(), (0, 1))
        self.assertEqual(v.thumb(), (0.0, 1.0))
        self.assertEqual(v.frame_at(0.5), 0)


class TestFractions(unittest.TestCase):
    def test_fraction_inside_and_outside(self) -> None:
        v = _view(zoom=4.0, start=100)
        self.assertAlmostEqual(v.fraction_of(225) or 0.0, 0.5)
        self.assertEqual(v.fraction_of(100), 0.0)
        self.assertIsNone(v.fraction_of(99))
        self.assertIsNone(v.fraction_of(351))

    def test_clamped_fraction_clips(self) -> None:
        v = _view(zoom=4.0, start=100)
        self.assertEqual(v.fraction_clamped(0), 0.0)
        self.assertEqual(v.fraction_clamped(900), 1.0)

    def test_frame_at_round_trips(self) -> None:
        v = _view(zoom=4.0, start=100)
        for frame in (100, 175, 225, 350):
            self.assertEqual(v.frame_at(v.fraction_of(frame) or 0.0), frame)

    def test_frame_at_bounds(self) -> None:
        v = _view()
        self.assertEqual(v.frame_at(-1.0), 0)
        self.assertEqual(v.frame_at(2.0), 1000)


class TestZoom(unittest.TestCase):
    def test_anchor_keeps_screen_position(self) -> None:
        v = _view()
        v.zoom_by(4.0, anchor_frame=500)
        self.assertAlmostEqual(v.fraction_of(500) or -1.0, 0.5, places=2)

    def test_anchor_off_centre_stays_put(self) -> None:
        v = _view(zoom=2.0, start=0)  # window 0..500
        before = v.fraction_of(125)
        v.zoom_by(2.0, anchor_frame=125)
        self.assertAlmostEqual(v.fraction_of(125) or -1.0, before or 0.0, places=2)

    def test_no_anchor_uses_window_centre(self) -> None:
        v = _view(zoom=2.0, start=500)  # window 500..1000
        v.zoom_by(2.0)
        lo, hi = v.window()
        self.assertTrue(lo <= 750 <= hi)
        self.assertAlmostEqual(v.fraction_of(750) or -1.0, 0.5, places=2)

    def test_offscreen_anchor_falls_back_to_centre(self) -> None:
        v = _view(zoom=2.0, start=500)
        v.zoom_by(2.0, anchor_frame=10)
        self.assertTrue(v.contains(750))

    def test_zoom_bounds_and_reset(self) -> None:
        v = _view()
        v.zoom_by(10_000.0)
        self.assertEqual(v.zoom, 100.0)
        v.zoom_by(1e-6)
        self.assertEqual(v.zoom, 1.0)
        self.assertEqual(v.window(), (0, 1000))


class TestPan(unittest.TestCase):
    def test_pan_by_clamps(self) -> None:
        v = _view(zoom=4.0, start=100)
        v.pan_by(-500)
        self.assertEqual(v.window()[0], 0)
        v.pan_by(5000)
        self.assertEqual(v.window()[1], 1000)

    def test_pan_by_fraction(self) -> None:
        v = _view(zoom=4.0, start=100)
        v.pan_by_fraction(0.5)
        self.assertEqual(v.window()[0], 225)

    def test_pan_noop_when_not_zoomed(self) -> None:
        v = _view()
        v.pan_by(100)
        v.pan_by_fraction(0.5)
        v.set_start_fraction(0.5)
        v.center_on(500)
        self.assertEqual(v.window(), (0, 1000))

    def test_set_start_fraction(self) -> None:
        v = _view(zoom=4.0)
        v.set_start_fraction(0.0)
        self.assertEqual(v.window()[0], 0)
        v.set_start_fraction(1.0)
        self.assertEqual(v.window(), (750, 1000))
        v.set_start_fraction(0.5)
        self.assertEqual(v.window()[0], 375)

    def test_center_on(self) -> None:
        v = _view(zoom=4.0)
        v.center_on(500)
        self.assertEqual(v.window(), (375, 625))
        v.center_on(5)
        self.assertEqual(v.window()[0], 0)


class TestPageTo(unittest.TestCase):
    def test_forward_flip_leaves_lead_in(self) -> None:
        v = _view(zoom=4.0, start=0)  # 0..250
        v.page_to(251)
        self.assertEqual(v.window()[0], 251 - 25)
        self.assertAlmostEqual(v.fraction_of(251) or -1.0, 0.1, places=2)

    def test_backward_flip_shows_history(self) -> None:
        v = _view(zoom=4.0, start=500)  # 500..750
        v.page_to(499)
        lo, hi = v.window()
        self.assertTrue(lo < 499 < hi)
        self.assertAlmostEqual(v.fraction_of(499) or -1.0, 0.9, places=2)

    def test_clamped_at_clip_end(self) -> None:
        v = _view(zoom=4.0, start=0)
        v.page_to(990)
        self.assertEqual(v.window(), (750, 1000))

    def test_noop_when_not_zoomed(self) -> None:
        v = _view()
        v.page_to(500)
        self.assertEqual(v.window(), (0, 1000))


class TestThumb(unittest.TestCase):
    def test_thumb_matches_window(self) -> None:
        left, width = _view(zoom=4.0, start=250).thumb()
        self.assertAlmostEqual(left, 0.25)
        self.assertAlmostEqual(width, 0.25)

    def test_unzoomed_thumb_fills_track(self) -> None:
        self.assertEqual(_view().thumb(), (0.0, 1.0))


if __name__ == "__main__":
    unittest.main()
