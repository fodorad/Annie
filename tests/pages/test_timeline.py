"""Tests for the pure SVG-timeline geometry and lane packing (no browser needed)."""

from __future__ import annotations

import unittest

from annie.core import theme
from annie.pages.timeline import (
    FONT_SIZE,
    LANE_LABEL_HEIGHT,
    MIN_BOX_PERCENT,
    TimelineEvent,
    TimelineTrack,
    _tick_step_seconds,
    _x_of_frame,
    build_svg,
    pack_lanes,
)


class TestTickStep(unittest.TestCase):
    def test_picks_nice_steps(self) -> None:
        # ~one tick per 90px over 1000px ≈ 11 ticks; 10s / 11 ≈ 0.9 → snaps to 1s.
        self.assertEqual(_tick_step_seconds(10.0, 1000.0), 1.0)

    def test_scales_up_for_long_windows(self) -> None:
        step = _tick_step_seconds(600.0, 1000.0)
        self.assertIn(step, (50.0, 100.0))

    def test_degenerate_inputs(self) -> None:
        self.assertEqual(_tick_step_seconds(0.0, 1000.0), 1.0)
        self.assertEqual(_tick_step_seconds(10.0, 0.0), 1.0)


class TestFrameX(unittest.TestCase):
    def test_window_endpoints(self) -> None:
        self.assertEqual(_x_of_frame(100, 100, 200, 1000.0), 0.0)
        self.assertAlmostEqual(_x_of_frame(200, 100, 200, 1000.0), 1000.0)

    def test_midpoint(self) -> None:
        self.assertAlmostEqual(_x_of_frame(150, 100, 200, 1000.0), 500.0)

    def test_degenerate_window(self) -> None:
        self.assertEqual(_x_of_frame(5, 5, 5, 1000.0), 0.0)


class TestPackLanes(unittest.TestCase):
    def test_non_overlapping_share_one_row(self) -> None:
        events = [TimelineEvent("a", 0, 10, ""), TimelineEvent("b", 20, 30, "")]
        self.assertEqual(pack_lanes(events), [0, 0])

    def test_overlapping_are_stacked(self) -> None:
        events = [TimelineEvent("a", 0, 20, ""), TimelineEvent("b", 10, 30, "")]
        rows = pack_lanes(events)
        self.assertNotEqual(rows[0], rows[1])

    def test_touching_intervals_are_separated(self) -> None:
        # b starts exactly where a ends → treated as overlapping (inclusive frames).
        events = [TimelineEvent("a", 0, 10, ""), TimelineEvent("b", 10, 20, "")]
        rows = pack_lanes(events)
        self.assertNotEqual(rows[0], rows[1])

    def test_three_way_overlap_uses_three_rows(self) -> None:
        events = [
            TimelineEvent("a", 0, 30, ""),
            TimelineEvent("b", 5, 25, ""),
            TimelineEvent("c", 10, 20, ""),
        ]
        self.assertEqual(sorted(pack_lanes(events)), [0, 1, 2])

    def test_rows_returned_in_original_order(self) -> None:
        # Input not sorted by start; result must align with input positions.
        events = [TimelineEvent("late", 50, 60, ""), TimelineEvent("early", 0, 10, "")]
        rows = pack_lanes(events)
        self.assertEqual(len(rows), 2)
        # Both fit on row 0 (they do not overlap), regardless of input order.
        self.assertEqual(rows, [0, 0])

    def test_freed_row_is_reused(self) -> None:
        events = [
            TimelineEvent("a", 0, 10, ""),
            TimelineEvent("b", 5, 15, ""),
            TimelineEvent("c", 20, 30, ""),  # after a ends → reuses row 0
        ]
        rows = pack_lanes(events)
        self.assertEqual(rows[2], 0)


class TestBuildSvg(unittest.TestCase):
    def _svg(self, **kwargs: object) -> str:
        tracks = kwargs.pop("tracks", None) or [
            TimelineTrack("speech", [TimelineEvent("e1", 0, 50, "hello")])
        ]
        return build_svg(
            tracks,
            view_start=kwargs.get("view_start", 0),
            view_end=kwargs.get("view_end", 99),
            fps=kwargs.get("fps", 25.0),
            svg_id=kwargs.get("svg_id", "tl"),
            pending_start=kwargs.get("pending_start"),
            pending_end=kwargs.get("pending_end"),
        )

    def test_contains_svg_and_id(self) -> None:
        svg = self._svg()
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn('id="tl"', svg)
        self.assertIn("</svg>", svg)

    def test_full_width_100_percent(self) -> None:
        # The surface always fills its container; zoom must not widen it.
        self.assertIn('width="100%"', self._svg())

    def test_event_box_and_label_present(self) -> None:
        svg = self._svg()
        self.assertIn('data-event="e1"', svg)
        self.assertIn("hello", svg)
        self.assertIn('class="annie-event"', svg)

    def test_lane_carries_track_name(self) -> None:
        svg = self._svg()
        self.assertIn('class="annie-lane"', svg)
        self.assertIn('data-track="speech"', svg)

    def test_lane_label_drawn(self) -> None:
        # The category name reads directly on the lane as a coloured text label.
        svg = self._svg(tracks=[TimelineTrack("Mother", [])])
        self.assertIn(">Mother</text>", svg)

    def test_lane_label_escaped(self) -> None:
        svg = self._svg(tracks=[TimelineTrack("<b>", [])])
        self.assertNotIn("<b></text>", svg)
        self.assertIn("&lt;b&gt;", svg)

    def test_empty_category_still_renders_a_labelled_lane(self) -> None:
        svg = self._svg(tracks=[TimelineTrack("Baby", [])])
        self.assertIn('class="annie-lane"', svg)
        self.assertIn(">Baby</text>", svg)

    def test_two_lanes_ordered_top_to_bottom(self) -> None:
        svg = self._svg(tracks=[TimelineTrack("Mother", []), TimelineTrack("Baby", [])])
        self.assertLess(svg.index(">Mother</text>"), svg.index(">Baby</text>"))

    def test_track_color_override_used(self) -> None:
        svg = self._svg(tracks=[TimelineTrack("Mother", [], color="#123456")])
        self.assertIn("#123456", svg)

    # ── ergonomics: un-stretched text, label band, box borders ───────────────────

    def test_svg_is_not_stretched(self) -> None:
        # preserveAspectRatio="none" is what smeared the glyphs horizontally; it must be gone
        # (the viewBox is synced to the real pixel width by JS instead).
        self.assertNotIn("preserveAspectRatio", self._svg())

    def test_text_uses_a_natural_font_size(self) -> None:
        self.assertIn(f"font-size:{FONT_SIZE}px", self._svg())

    def test_lane_label_sits_above_the_lane_not_inside_it(self) -> None:
        svg = self._svg(tracks=[TimelineTrack("Participant 1", [])])
        label_y = float(svg.split(">Participant 1</text>", 1)[0].rsplit('y="', 1)[1].split('"')[0])
        lane = svg.split('class="annie-lane"', 1)[1]
        lane_y = float(lane.split('y="', 1)[1].split('"', 1)[0])
        # The label's baseline is above the lane rectangle's top edge.
        self.assertLess(label_y, lane_y + LANE_LABEL_HEIGHT)
        self.assertLessEqual(label_y, lane_y)

    def test_label_band_adds_to_total_height(self) -> None:
        # (the _svg helper substitutes a default lane for an empty list, so call build_svg)
        empty = self._viewbox_height(
            build_svg([], view_start=0, view_end=99, fps=25.0, svg_id="tl")
        )
        one = self._viewbox_height(self._svg(tracks=[TimelineTrack("A", [])]))
        # One lane adds its label band + rows + gap on top of the empty surface.
        self.assertGreaterEqual(one - empty, LANE_LABEL_HEIGHT)

    def test_every_event_box_has_a_dark_border(self) -> None:
        svg = self._svg(
            tracks=[TimelineTrack("s", [TimelineEvent("e1", 0, 40, "x", color="#fdffb6")])]
        )
        box = svg.split('class="annie-event"', 1)[1].split("</rect>", 1)[0]
        self.assertIn(f'stroke="{theme.EVENT_BORDER}"', box)
        self.assertIn('stroke-width="1.0"', box)

    def test_selected_box_has_thicker_near_black_border(self) -> None:
        svg = self._svg(
            tracks=[TimelineTrack("s", [TimelineEvent("e1", 0, 40, "x", selected=True)])]
        )
        box = svg.split('class="annie-event"', 1)[1].split("</rect>", 1)[0]
        self.assertIn(f'stroke="{theme.EVENT_SELECTED}"', box)
        self.assertIn('stroke-width="2.0"', box)

    def test_geometry_is_percent_based_with_no_viewbox(self) -> None:
        # No viewBox → user units are real pixels; x/width are percentages of the element, so
        # nothing is scaled and text can never be stretched.
        svg = self._svg()
        self.assertNotIn("viewBox", svg)
        box = svg.split('class="annie-event"', 1)[1]
        self.assertRegex(box.split(">", 1)[0], r'x="[\d.]+%"')
        self.assertRegex(box.split(">", 1)[0], r'width="[\d.]+%"')

    def test_lane_fills_the_full_width(self) -> None:
        lane = self._svg().split('class="annie-lane"', 1)[1].split("/>", 1)[0]
        self.assertIn('width="100%"', lane)

    def test_event_box_position_matches_its_frames(self) -> None:
        # frames 10..60 of a 0..100 window → x = 10%, width = 50%.
        svg = self._svg(
            tracks=[TimelineTrack("s", [TimelineEvent("e1", 10, 60, "x")])], view_end=100
        )
        head = svg.split('class="annie-event"', 1)[1].split(">", 1)[0]
        self.assertAlmostEqual(float(head.split('x="', 1)[1].split('%"', 1)[0]), 10.0, places=2)
        self.assertAlmostEqual(float(head.split('width="', 1)[1].split('%"', 1)[0]), 50.0, places=2)

    def test_zero_length_event_is_still_visible(self) -> None:
        svg = self._svg(tracks=[TimelineTrack("s", [TimelineEvent("z", 50, 50, "")])])
        head = svg.split('class="annie-event"', 1)[1].split(">", 1)[0]
        self.assertGreaterEqual(
            float(head.split('width="', 1)[1].split('%"', 1)[0]), MIN_BOX_PERCENT
        )

    def test_event_name_is_a_clipped_text_layer(self) -> None:
        # The name lives in a nested <svg> the size of the box, which clips it; it ignores the
        # pointer so a click still reaches the box beneath.
        svg = self._svg(tracks=[TimelineTrack("s", [TimelineEvent("e1", 0, 80, "hello")])])
        layer = svg.split(">hello</text>", 1)[0].rsplit("<svg", 1)[1]
        self.assertIn('pointer-events="none"', layer)
        self.assertIn('text-anchor="middle"', svg)

    def test_name_that_cannot_fit_is_not_drawn(self) -> None:
        svg = self._svg(
            tracks=[TimelineTrack("s", [TimelineEvent("e1", 0, 2, "a very long event name")])]
        )
        self.assertNotIn(">a very long event name</text>", svg)
        self.assertIn("<title>a very long event name</title>", svg)  # still in the tooltip

    def test_hover_rule_lightens_and_thickens(self) -> None:
        svg = self._svg()
        self.assertIn(".annie-event:hover{fill-opacity:0.85;stroke-width:2}", svg)

    def test_playhead_and_hover_style_present(self) -> None:
        svg = self._svg()
        self.assertIn('class="annie-playhead"', svg)
        self.assertIn(".annie-event:hover", svg)

    def test_event_outside_window_is_skipped(self) -> None:
        tracks = [TimelineTrack("s", [TimelineEvent("far", 500, 550, "x")])]
        svg = self._svg(tracks=tracks, view_start=0, view_end=99)
        self.assertNotIn('data-event="far"', svg)

    def test_event_straddling_edge_is_clamped_and_shown(self) -> None:
        tracks = [TimelineTrack("s", [TimelineEvent("edge", 80, 200, "x")])]
        svg = self._svg(tracks=tracks, view_start=0, view_end=99)
        self.assertIn('data-event="edge"', svg)

    def test_selected_event_uses_selection_stroke(self) -> None:
        tracks = [TimelineTrack("s", [TimelineEvent("e1", 0, 40, "x", selected=True)])]
        self.assertIn(theme.EVENT_SELECTED, self._svg(tracks=tracks))

    def test_html_in_label_is_escaped(self) -> None:
        tracks = [TimelineTrack("s", [TimelineEvent("e1", 0, 40, "<script>")])]
        svg = self._svg(tracks=tracks)
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)

    def _viewbox_height(self, svg: str) -> float:
        # The root <svg ... height="<h>"> carries the total drawing height (there is no viewBox).
        root = svg.split(">", 1)[0]
        return float(root.split('height="', 1)[1].split('"', 1)[0])

    def test_overlapping_events_grow_the_lane(self) -> None:
        one = self._svg(tracks=[TimelineTrack("s", [TimelineEvent("a", 0, 40, "")])])
        two = self._svg(
            tracks=[
                TimelineTrack("s", [TimelineEvent("a", 0, 40, ""), TimelineEvent("b", 10, 50, "")])
            ]
        )
        # Two overlapping events stack, so the lane (and total SVG height) is taller.
        self.assertGreater(self._viewbox_height(two), self._viewbox_height(one))


class TestPendingBand(unittest.TestCase):
    def _svg(self, **kwargs: object) -> str:
        return build_svg(
            [TimelineTrack("s", [TimelineEvent("e1", 0, 50, "x")])],
            view_start=0,
            view_end=99,
            fps=25.0,
            svg_id="tl",
            pending_start=kwargs.get("pending_start"),
            pending_end=kwargs.get("pending_end"),
        )

    def test_band_hook_always_present(self) -> None:
        # The rect always exists (zero-width when idle) so the JS resize has a stable hook.
        self.assertIn('class="annie-pending"', self._svg())

    def test_idle_band_is_zero_width(self) -> None:
        svg = self._svg(pending_start=None)
        pending = svg.split('class="annie-pending"', 1)[1].split("/>", 1)[0]
        self.assertIn('width="0%"', pending)

    def test_armed_band_spans_start_to_end(self) -> None:
        # start=20, end=60 over a 0..99 window of width 1000 → x≈202, width≈404.
        svg = self._svg(pending_start=20, pending_end=60)
        pending = svg.split('class="annie-pending"', 1)[1].split("/>", 1)[0]
        self.assertNotIn('width="0%"', pending)
        x = float(pending.split('x="', 1)[1].split('%"', 1)[0])
        self.assertAlmostEqual(x, 20 / 99 * 100, delta=0.2)  # percent of the width

    def test_band_defaults_end_to_start(self) -> None:
        # No pending_end → minimal band at the start (width ~0).
        svg = self._svg(pending_start=30)
        pending = svg.split('class="annie-pending"', 1)[1].split("/>", 1)[0]
        width = float(pending.split('width="', 1)[1].split('%"', 1)[0])
        self.assertLess(width, 0.1)

    def test_band_drawn_before_events(self) -> None:
        # Behind the events → its markup appears earlier than the first event box.
        svg = self._svg(pending_start=10, pending_end=40)
        self.assertLess(svg.index('class="annie-pending"'), svg.index('class="annie-event"'))


if __name__ == "__main__":
    unittest.main()
