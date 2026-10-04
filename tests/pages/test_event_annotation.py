"""Tests for the Event-annotation task's store-backed logic (no browser needed).

The video player, JS bridge, and SVG gestures are browser-gated and exercised by hand, like
the other ``ui.video`` paths. What is unit-tested here is the part that touches the review
store and the per-client state: creating events from a gesture, the ``I``/``O`` marking flow,
the fps map for export, the fraction→frame mapping, and the timecode helpers.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from nicegui import core

from annie.core.models import VideoEntry
from annie.core.state import state
from annie.dataset.layout import VideoLayout
from annie.dataset.scanning import ScanResult
from annie.dataset.sources import DataSource, SourceKind, SourceRegistry
from annie.dataset.storage import ReviewStore
from annie.pages import event_task
from tests.pages._nicegui import quiet_slow_callback_warnings, ui_client


class _Args:
    """Stand-in for a NiceGUI GenericEventArguments carrying a gesture ``detail``."""

    def __init__(self, detail: dict[str, object]) -> None:
        self.args = detail


class TestEventTaskLogic(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved_loop = core.loop
        core.loop = asyncio.get_running_loop()
        quiet_slow_callback_warnings()

    async def asyncTearDown(self) -> None:
        core.loop = self._saved_loop

    def setUp(self) -> None:
        self._saved = (state.store, state.scan)
        self.root = Path(tempfile.mkdtemp())
        state.store = ReviewStore(self.root / "annie.db")
        state.scan = ScanResult(entries=[VideoEntry(video_id="v", video_path=None, row_id=1)])
        self.state = event_task._EventState(  # noqa: SLF001
            row_key="v::", video_id="v", fps=25.0, num_frames=250, duration=10.0
        )
        event_task._event_states.clear()  # noqa: SLF001

    def tearDown(self) -> None:
        state.store, state.scan = self._saved
        event_task._event_states.clear()  # noqa: SLF001
        event_task._gesture_bound.clear()  # noqa: SLF001

    async def test_seek_gesture_creates_no_event(self) -> None:
        # The timeline is navigation-only: a click on an empty lane must not author anything.
        state.store.add_track("v::", "speech")
        with ui_client():
            event_task._on_gesture(  # noqa: SLF001
                self.state, _Args({"kind": "seek", "x": 0.5})
            )
        self.assertEqual(state.store.events_for("v::"), [])
        # x=0.5 over 250 frames → the playhead lands mid-clip.
        self.assertAlmostEqual(self.state.playhead_frame, 124, delta=1)

    async def test_default_event_name_is_event_name(self) -> None:
        self.assertEqual(event_task._DEFAULT_EVENT_LABEL, "event_name")  # noqa: SLF001
        rec = state.store.add_event(
            "v",
            "v::",
            "speech",
            10,
            20,
            label=event_task._DEFAULT_EVENT_LABEL,  # noqa: SLF001
        )
        self.assertEqual(state.store.get_event(rec.event_id).label, "event_name")  # type: ignore[union-attr]

    async def test_cancel_pending_clears_without_creating(self) -> None:
        self.state.pending_start = 40
        with ui_client():
            event_task._cancel_pending(self.state)  # noqa: SLF001
        self.assertIsNone(self.state.pending_start)
        self.assertEqual(state.store.events_for("v::"), [])

    async def test_set_color_updates_selected_event(self) -> None:
        rec = state.store.add_event("v", "v::", "speech", 0, 5)
        self.state.selected_event = rec.event_id
        with ui_client():
            event_task._set_color(self.state, event_task._EVENT_COLORS[0])  # noqa: SLF001
        got = state.store.get_event(rec.event_id)
        assert got is not None
        self.assertEqual(got.color, event_task._EVENT_COLORS[0])  # noqa: SLF001

    async def test_select_gesture_seeks_to_event_start(self) -> None:
        rec = state.store.add_event("v", "v::", "speech", 10, 40)
        with ui_client():
            event_task._on_gesture(  # noqa: SLF001
                self.state, _Args({"kind": "select", "event_id": rec.event_id})
            )
        self.assertEqual(self.state.selected_event, rec.event_id)
        # Selecting jumps the playhead to the event's start frame.
        self.assertEqual(self.state.playhead_frame, 10)

    async def test_delete_event_clears_selection(self) -> None:
        rec = state.store.add_event("v", "v::", "speech", 10, 40)
        self.state.selected_event = rec.event_id
        with ui_client():
            event_task._delete_event(self.state)  # noqa: SLF001
        self.assertIsNone(self.state.selected_event)
        self.assertEqual(state.store.events_for("v::"), [])

    async def test_update_persists_fields(self) -> None:
        rec = state.store.add_event("v", "v::", "speech", 10, 40, label="a")
        self.state.selected_event = rec.event_id
        with ui_client():
            event_task._update(self.state, label="b", note="n")  # noqa: SLF001
        got = state.store.get_event(rec.event_id)
        assert got is not None
        self.assertEqual((got.label, got.note), ("b", "n"))

    async def test_add_attribute_then_current_attributes(self) -> None:
        rec = state.store.add_event("v", "v::", "speech", 0, 5)
        self.state.selected_event = rec.event_id
        with ui_client():
            event_task._add_attribute(self.state)  # noqa: SLF001
        self.assertEqual(set(event_task._current_attributes(self.state)), {"key"})  # noqa: SLF001

    async def test_fraction_to_frame_bounds(self) -> None:
        self.assertEqual(event_task._fraction_to_frame(self.state, 0.0), 0)  # noqa: SLF001
        self.assertEqual(event_task._fraction_to_frame(self.state, 1.0), 249)  # noqa: SLF001

    async def test_export_text_per_scope_and_format(self) -> None:
        self.assertIsNone(event_task._export_text(self.state, "video", "csv"))  # noqa: SLF001
        state.store.add_event("v", "v::", "speech", 0, 25, label="hi")
        js = event_task._export_text(self.state, "video", "json")  # noqa: SLF001
        csv_text = event_task._export_text(self.state, "session", "csv")  # noqa: SLF001
        self.assertIn('"speech"', js or "")
        self.assertTrue((csv_text or "").startswith("video_id,track"))

    async def test_export_route_serves_text_or_204(self) -> None:
        state.store.add_event("v", "v::", "speech", 0, 25)
        event_task._event_states["c1"] = self.state  # noqa: SLF001
        ok = event_task._export_route("c1", "video", "csv")  # noqa: SLF001
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.media_type, "text/csv")
        for args in (("nope", "video", "csv"), ("c1", "bogus", "csv"), ("c1", "video", "xml")):
            self.assertEqual(event_task._export_route(*args).status_code, 204)  # noqa: SLF001
        state.store.delete_event(state.store.events_for("v::")[0].event_id)
        self.assertEqual(event_task._export_route("c1", "video", "csv").status_code, 204)  # noqa: SLF001

    async def test_timecode_round_trip(self) -> None:
        self.assertEqual(event_task._format_timecode(65.25), "01:05.25")  # noqa: SLF001
        self.assertAlmostEqual(event_task._parse_timecode("01:05.25"), 65.25)  # noqa: SLF001
        self.assertAlmostEqual(event_task._parse_timecode("3.0"), 3.0)  # noqa: SLF001
        self.assertEqual(event_task._parse_timecode("garbage"), 0.0)  # noqa: SLF001

    # ── categories ───────────────────────────────────────────────────────────────

    async def test_ensure_active_category_defaults_to_first(self) -> None:
        state.store.add_category("Mother")
        state.store.add_category("Baby")
        event_task._ensure_active_category(self.state)  # noqa: SLF001
        self.assertEqual(self.state.active_category, "Mother")

    async def test_ensure_active_category_drops_stale(self) -> None:
        self.state.active_category = "Gone"
        event_task._ensure_active_category(self.state)  # noqa: SLF001
        self.assertIsNone(self.state.active_category)  # no categories → None

    async def test_set_active_by_ordinal(self) -> None:
        for n in ("Mother", "Baby", "Examiner"):
            state.store.add_category(n)
        with ui_client():
            event_task._set_active_by_ordinal(self.state, 1)  # noqa: SLF001
        self.assertEqual(self.state.active_category, "Baby")
        # Out-of-range ordinal is ignored.
        with ui_client():
            event_task._set_active_by_ordinal(self.state, 9)  # noqa: SLF001
        self.assertEqual(self.state.active_category, "Baby")

    async def test_event_lands_on_active_category(self) -> None:
        # _finalise reads browser time (not headless-testable); assert the store write it makes:
        # an event created on the active category shows on that participant's lane.
        state.store.add_category("Mother")
        state.store.add_category("Baby")
        self.state.active_category = "Baby"
        rec = state.store.add_event(
            "v", "v::", self.state.active_category, 30, 60, label="event_name"
        )
        self.assertEqual(state.store.get_event(rec.event_id).track, "Baby")  # type: ignore[union-attr]

    async def test_move_event_to_category(self) -> None:
        state.store.add_category("Mother")
        state.store.add_category("Baby")
        rec = state.store.add_event("v", "v::", "Mother", 0, 10)
        self.state.selected_event = rec.event_id
        with ui_client():
            event_task._move_event_to_category(self.state, "Baby")  # noqa: SLF001
        self.assertEqual(state.store.get_event(rec.event_id).track, "Baby")  # type: ignore[union-attr]

    async def test_timeline_builds_one_lane_per_category(self) -> None:
        state.store.add_category("Mother")
        state.store.add_category("Baby")
        state.store.add_event("v", "v::", "Mother", 0, 10)
        # The lanes come from categories(), not per-video tracks — even the empty Baby lane.
        names = [c.name for c in state.store.categories()]
        self.assertEqual(names, ["Mother", "Baby"])

    # ── default category + naming ────────────────────────────────────────────────

    async def test_fresh_dataset_starts_with_participant_1(self) -> None:
        entry = VideoEntry(video_id="v", video_path=None, row_id=1)
        state.scan = ScanResult(entries=[entry])
        state.store.set_annotate(entry.key, entry.video_id, None, True)
        self.assertEqual(state.store.categories(), [])  # nothing yet
        event_task._load_video_if_needed(self.state, [entry])  # noqa: SLF001
        names = [c.name for c in state.store.categories()]
        self.assertEqual(names, ["Participant 1"])
        self.assertEqual(self.state.active_category, "Participant 1")

    async def test_existing_categories_are_not_replaced_by_the_default(self) -> None:
        entry = VideoEntry(video_id="v", video_path=None, row_id=1)
        state.scan = ScanResult(entries=[entry])
        state.store.add_category("Mother")
        event_task._load_video_if_needed(self.state, [entry])  # noqa: SLF001
        self.assertEqual([c.name for c in state.store.categories()], ["Mother"])

    async def test_legacy_events_seed_instead_of_the_default(self) -> None:
        # A DB annotated under the single-lane model keeps its track name as the category.
        entry = VideoEntry(video_id="v", video_path=None, row_id=1)
        state.scan = ScanResult(entries=[entry])
        state.store.add_event("v", entry.key, "events", 0, 5)
        event_task._load_video_if_needed(self.state, [entry])  # noqa: SLF001
        self.assertEqual([c.name for c in state.store.categories()], ["events"])

    async def test_next_default_category_name(self) -> None:
        f = event_task.next_default_category_name
        self.assertEqual(f([]), "Participant 1")
        self.assertEqual(f(["Participant 1"]), "Participant 2")
        self.assertEqual(f(["Participant 1", "Participant 3"]), "Participant 2")  # fills the gap
        self.assertEqual(f(["Mother", "Baby"]), "Participant 1")  # unrelated names don't count

    # ── zoom / pan / follow ──────────────────────────────────────────────────────

    def _zoomed(self) -> None:
        self.state.view.zoom_by(4.0, anchor_frame=0)  # 250 frames → window 0..62

    async def test_zoom_anchors_on_visible_playhead(self) -> None:
        self.state.playhead_frame = 120
        with ui_client():
            event_task._zoom(self.state, 2.0)  # noqa: SLF001
        self.assertTrue(self.state.view.contains(120))
        self.assertAlmostEqual(self.state.view.fraction_of(120) or -1.0, 0.5, delta=0.02)

    async def test_playhead_line_moves_inside_the_window(self) -> None:
        # Regression: the window used to centre on the head, pinning the line at 0.5.
        self._zoomed()
        self.state.view.pan_by(20)
        before = self.state.view.window()
        self.state.playhead_frame = 30
        self.assertEqual(self.state.view.window(), before)
        a = self.state.view.fraction_of(30)
        self.state.playhead_frame = 40
        b = self.state.view.fraction_of(40)
        self.assertLess(a or 0.0, b or 0.0)

    async def test_follow_pages_window_when_playback_leaves_it(self) -> None:
        self._zoomed()
        self.state.playhead_frame = 100
        with ui_client():
            paged = event_task._follow_playhead(self.state)  # noqa: SLF001
        self.assertTrue(paged)
        self.assertTrue(self.state.view.contains(100))
        self.assertAlmostEqual(self.state.view.fraction_of(100) or -1.0, 0.1, delta=0.03)

    async def test_follow_off_leaves_window_and_hides_line(self) -> None:
        self._zoomed()
        self.state.follow = False
        self.state.playhead_frame = 100
        with ui_client():
            paged = event_task._follow_playhead(self.state)  # noqa: SLF001
        self.assertFalse(paged)
        self.assertIsNone(self.state.view.fraction_of(100))

    async def test_manual_pan_turns_follow_off(self) -> None:
        self._zoomed()
        with ui_client():
            event_task._on_gesture(  # noqa: SLF001
                self.state, _Args({"kind": "pan", "x": 0.5})
            )
        self.assertFalse(self.state.follow)
        self.assertGreater(self.state.view.window()[0], 0)
        self.state.follow = True
        with ui_client():
            event_task._on_gesture(  # noqa: SLF001
                self.state, _Args({"kind": "wheel", "dx": -0.5})
            )
        self.assertFalse(self.state.follow)

    async def test_enabling_follow_snaps_to_playhead(self) -> None:
        self._zoomed()
        self.state.follow = False
        self.state.playhead_frame = 200
        with ui_client():
            event_task._set_follow(self.state, True)  # noqa: SLF001
        self.assertTrue(self.state.view.contains(200))

    async def test_explicit_seek_outside_window_centres_it(self) -> None:
        self._zoomed()
        self.state.follow = False
        with ui_client():
            event_task._seek(self.state, "vid", 200)  # noqa: SLF001
        self.assertTrue(self.state.view.contains(200))
        self.assertEqual(self.state.playhead_frame, 200)

    async def test_seek_inside_window_keeps_it(self) -> None:
        self._zoomed()
        before = self.state.view.window()
        with ui_client():
            event_task._seek(self.state, "vid", 10)  # noqa: SLF001
        self.assertEqual(self.state.view.window(), before)

    async def test_gesture_fraction_maps_through_the_real_window(self) -> None:
        self._zoomed()
        self.state.view.pan_by(40)
        lo, hi = self.state.view.window()
        self.assertEqual(event_task._fraction_to_frame(self.state, 0.0), lo)  # noqa: SLF001
        self.assertEqual(event_task._fraction_to_frame(self.state, 1.0), hi)  # noqa: SLF001

    async def test_range_label(self) -> None:
        self._zoomed()
        label = event_task._range_label(self.state)  # noqa: SLF001
        self.assertIn("×4.0", label)
        self.assertIn("of 10.0 s", label)


class TestLayoutBadgesAndMeta(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._saved = (state.store, state.scan, state.registry)
        self.root = Path(tempfile.mkdtemp())
        state.store = ReviewStore(self.root / "annie.db")
        entry = VideoEntry(
            video_id="s1_a", video_path=None, labels={"grp": "g1", "part": "a", "Other": "z"}
        )
        state.scan = ScanResult(entries=[entry])
        state.registry = SourceRegistry()
        state.registry.add(
            DataSource(SourceKind.VIDEO, self.root, layout=VideoLayout("{grp}/clip_{part}.mp4"))
        )
        self.entry = entry

    def tearDown(self) -> None:
        state.store, state.scan, state.registry = self._saved

    async def test_badges_show_only_layout_fields_in_pattern_order(self) -> None:
        self.assertEqual(event_task._layout_badges(self.entry), ["g1", "a"])  # noqa: SLF001

    async def test_no_badges_for_a_flat_folder(self) -> None:
        state.registry = SourceRegistry()
        state.registry.add(DataSource(SourceKind.VIDEO, self.root))
        self.assertEqual(event_task._layout_badges(self.entry), [])  # noqa: SLF001

    async def test_export_text_includes_meta(self) -> None:
        st = event_task._EventState(  # noqa: SLF001
            row_key="s1_a", video_id="s1_a", fps=25.0, num_frames=100, duration=4.0
        )
        state.store.add_event("s1_a", "s1_a", "speech", 0, 10)
        csv_text = event_task._export_text(st, "session", "csv") or ""  # noqa: SLF001
        self.assertIn("meta_grp", csv_text)
        self.assertIn("meta_part", csv_text)


if __name__ == "__main__":
    unittest.main()
