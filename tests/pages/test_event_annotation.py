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
from annie.dataset.scanning import ScanResult
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

    async def test_export_writes_json_and_csv(self) -> None:
        state.store.add_event("v", "v::", "speech", 0, 25, label="hi")
        from annie.core.config import settings

        settings.temp_dir.mkdir(parents=True, exist_ok=True)
        with ui_client():
            event_task._export(self.state, scope="video", fmt="json")  # noqa: SLF001
            event_task._export(self.state, scope="video", fmt="csv")  # noqa: SLF001
        out_json = settings.temp_dir / "annie_events_v.json"
        out_csv = settings.temp_dir / "annie_events_v.csv"
        self.assertTrue(out_json.exists())
        self.assertTrue(out_csv.exists())
        self.assertIn("speech", out_csv.read_text(encoding="utf-8"))

    async def test_timecode_round_trip(self) -> None:
        self.assertEqual(event_task._format_timecode(65.25), "01:05.25")  # noqa: SLF001
        self.assertAlmostEqual(event_task._parse_timecode("01:05.25"), 65.25)  # noqa: SLF001
        self.assertAlmostEqual(event_task._parse_timecode("3.0"), 3.0)  # noqa: SLF001
        self.assertEqual(event_task._parse_timecode("garbage"), 0.0)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
