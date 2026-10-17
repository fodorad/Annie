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
from types import SimpleNamespace

from nicegui import core, ui
from nicegui.events import KeyboardKey

from annie.core.models import VideoEntry
from annie.core.state import state
from annie.dataset.layout import VideoLayout
from annie.dataset.scanning import ScanResult
from annie.dataset.sources import DataSource, SourceKind, SourceRegistry
from annie.dataset.storage import EventRecord, ReviewStore
from annie.pages import download, event_task
from tests.pages._nicegui import quiet_slow_callback_warnings, ui_client


def _fire(element: object, event: str, args: object = None) -> None:
    """Dispatch a browser event to ``element`` the way NiceGUI does (its listeners run)."""
    for listener_id, listener in list(element._event_listeners.items()):  # type: ignore[attr-defined]  # noqa: SLF001
        if listener.type == event:
            element._handle_event({"listener_id": listener_id, "args": args})  # type: ignore[attr-defined]  # noqa: SLF001


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
        self.assertEqual(f(["Event 1"], prefix="Event"), "Event 2")

    # ── event categories ─────────────────────────────────────────────────────────

    async def _capture(self, seconds: float) -> EventRecord:
        """Close the armed event at ``seconds`` (the browser's video time); return it."""
        category = self.state.active_category or "events"
        with ui_client():
            event_task._finalise_at(self.state, category, seconds)  # noqa: SLF001
        got = state.store.get_event(self.state.selected_event or "")
        assert got is not None
        return got

    async def test_capture_with_an_event_category(self) -> None:
        state.store.add_category("Mother")
        state.store.add_event_type("Smile", color="#b5e48c", attributes={"intensity": "1"})
        self.state.active_category = "Mother"
        self.state.active_event_type = "Smile"
        self.state.pending_start = 10
        got = await self._capture(2.0)
        self.assertEqual((got.label, got.event_type, got.track), ("Smile", "Smile", "Mother"))
        self.assertEqual((got.start_frame, got.end_frame), (10, 50))
        # Linked, not copied: the colour and attributes come from the category.
        self.assertIsNone(got.color)
        self.assertEqual(got.attributes, {})

    async def test_capture_without_an_event_category(self) -> None:
        state.store.add_category("Mother")
        self.state.active_category = "Mother"
        got = await self._capture(1.0)
        self.assertEqual((got.label, got.event_type), ("", None))  # a plain box, no label

    async def test_a_deleted_active_category_captures_plain(self) -> None:
        state.store.add_category("Mother")
        self.state.active_category = "Mother"
        self.state.active_event_type = "Gone"
        got = await self._capture(1.0)
        self.assertIsNone(got.event_type)
        event_task._ensure_active_event_type(self.state)  # noqa: SLF001
        self.assertIsNone(self.state.active_event_type)

    def _press(self, name: str, code: str, *, shift: bool) -> None:
        """Send one keydown through the page's real keyboard handler."""
        with ui_client():
            event_task._on_key(  # noqa: SLF001
                self.state,
                "video",
                SimpleNamespace(
                    action=SimpleNamespace(keydown=True, repeat=False),
                    key=KeyboardKey(name=name, code=code, location=0),
                    modifiers=SimpleNamespace(shift=shift),
                ),
            )

    async def test_shift_digits_choose_the_event_category(self) -> None:
        for name in ("Mother", "Baby"):
            state.store.add_category(name)
        for name in ("Smile", "Gaze away"):
            state.store.add_event_type(name)
        self.state.active_category = "Mother"
        self._press('"', "Digit2", shift=True)  # Shift+2 on a US keyboard types "@"; any layout
        self.assertEqual(self.state.active_event_type, "Gaze away")
        self.assertEqual(self.state.active_category, "Mother")  # participants untouched
        self._press("(", "Digit9", shift=True)  # beyond the list: ignored
        self.assertEqual(self.state.active_event_type, "Gaze away")
        self._press(")", "Digit0", shift=True)
        self.assertIsNone(self.state.active_event_type)
        self._press("2", "Digit2", shift=False)  # without Shift: the participant
        self.assertEqual(self.state.active_category, "Baby")
        self.assertIsNone(self.state.active_event_type)

    async def test_editor_event_category_change(self) -> None:
        # The event category is the event's name: the label always follows it.
        state.store.add_event_type("Smile")
        state.store.add_event_type("Laugh")
        rec = state.store.add_event("v", "v::", "Mother", 0, 10, label="old name")
        self.state.selected_event = rec.event_id
        with ui_client():
            event_task._set_event_type(self.state, "Smile")  # noqa: SLF001
            got = state.store.get_event(rec.event_id)
            assert got is not None
            self.assertEqual((got.event_type, got.label), ("Smile", "Smile"))
            event_task._set_event_type(self.state, "Laugh")  # noqa: SLF001
            self.assertEqual(state.store.get_event(rec.event_id).label, "Laugh")  # type: ignore[union-attr]
            event_task._set_event_type(self.state, "Laugh")  # noqa: SLF001  # no change
            event_task._set_event_type(self.state, None)  # noqa: SLF001
            got = state.store.get_event(rec.event_id)
            assert got is not None
            self.assertEqual((got.event_type, got.label), (None, ""))

    async def test_editor_order_and_no_name(self) -> None:
        state.store.add_category("Mother")
        state.store.add_event_type("Smile", color="#b5e48c", attributes={"intensity": "1"})
        rec = state.store.add_event(
            "v", "v::", "Mother", 0, 10, label="Smile", event_type="Smile", note="n"
        )
        self.state.selected_event = rec.event_id
        with ui_client() as client:
            event_task._editor(self.state)  # noqa: SLF001
            labels = [
                el.props.get("label") for el in client.elements.values() if hasattr(el, "value")
            ]
            pickers = [el for el in client.elements.values() if isinstance(el, ui.color_picker)]
        labels = [label for label in labels if label]
        self.assertNotIn("name", labels)
        self.assertEqual(
            labels,
            ["participant", "event category", "frame", "time (s)", "frame", "time (s)", "note"],
        )
        # One colour dot, showing the colour the event is drawn with (its category's).
        self.assertEqual(len(pickers), 1)
        self.assertEqual(pickers[0].q_color.props["model-value"], "#b5e48c")

    async def test_editor_colour_pick_and_reset(self) -> None:
        state.store.add_category("Mother")
        rec = state.store.add_event("v", "v::", "Mother", 0, 10)
        self.state.selected_event = rec.event_id
        with ui_client() as client:
            event_task._editor(self.state)  # noqa: SLF001
            picker = next(el for el in client.elements.values() if isinstance(el, ui.color_picker))
            _fire(picker.q_color, "change", "#123456")
            self.assertEqual(state.store.get_event(rec.event_id).color, "#123456")  # type: ignore[union-attr]
            reset = next(
                el
                for el in client.elements.values()
                if isinstance(el, ui.button) and el.text == "Use the category's colour"
            )
            _fire(reset, "click")
        self.assertIsNone(state.store.get_event(rec.event_id).color)  # type: ignore[union-attr]

    async def test_editor_shows_inherited_attributes(self) -> None:
        state.store.add_category("Mother")
        state.store.add_event_type("Smile", attributes={"intensity": "1"})
        rec = state.store.add_event(
            "v", "v::", "Mother", 0, 10, label="Smile", event_type="Smile", attributes={"x": "y"}
        )
        self.state.selected_event = rec.event_id
        with ui_client() as client:
            event_task._editor(self.state)  # noqa: SLF001
            values = {
                getattr(el, "value", None)
                for el in client.elements.values()
                if hasattr(el, "value")
            }
        self.assertTrue({"intensity", "1", "x", "y", "Smile"} <= values)
        with ui_client():
            event_task._add_attribute(self.state)  # noqa: SLF001
        # The new key avoids the inherited one too, and the default is not copied.
        self.assertEqual(
            state.store.get_event(rec.event_id).attributes,  # type: ignore[union-attr]
            {"x": "y", "key": ""},
        )

    async def test_bar_and_manager_build(self) -> None:
        state.store.add_category("Mother")
        state.store.add_event_type("Smile", color="#b5e48c", attributes={"a": "b"})
        self.state.active_event_type = "Smile"
        with ui_client() as client:
            event_task._category_bar(self.state)  # noqa: SLF001
            event_task._participant_manager(self.state)  # noqa: SLF001
            event_task._event_type_manager(self.state)  # noqa: SLF001
            kind = state.store.event_types()[0]
            event_task._event_type_row(self.state, kind, 0, 1, {"Smile"}, lambda: None)  # noqa: SLF001
            texts = {getattr(el, "text", "") for el in client.elements.values()}
        self.assertIn("(⇧1) Smile", texts)
        self.assertIn("(⇧0) None", texts)
        self.assertIn("(1) Mother", texts)

    async def test_empty_bar_offers_to_add(self) -> None:
        with ui_client() as client:
            event_task._category_bar(self.state)  # noqa: SLF001
            texts = {getattr(el, "text", "") for el in client.elements.values()}
        self.assertIn("＋ Add participant", texts)
        # Event categories: None plus the ⋯ button, never a text button.
        self.assertIn("(⇧0) None", texts)
        self.assertNotIn("＋ Add event category", texts)

    async def test_each_half_opens_its_own_dialog(self) -> None:
        body = event_task._manager_body  # noqa: SLF001
        self.assertIs(body("participants"), event_task._participant_manager)  # noqa: SLF001
        self.assertIs(body("event_types"), event_task._event_type_manager)  # noqa: SLF001
        with ui_client() as client:
            event_task._open_participants(self.state)  # noqa: SLF001
            event_task._open_event_types(self.state)  # noqa: SLF001
            timers = [el for el in client.layout.descendants() if isinstance(el, ui.timer)]
        self.assertEqual(len(timers), 2)  # each opens its dialog on the next loop turn

    async def test_manager_rows_pick_colours(self) -> None:
        state.store.add_category("Mother")
        state.store.add_event_type("Smile", color="#b5e48c")
        rebuilt: list[int] = []
        with ui_client() as client:
            event_task._manager_row(self.state, "Mother", 0, None, 0, 1, lambda: rebuilt.append(1))  # noqa: SLF001
            kind = state.store.event_types()[0]
            event_task._event_type_row(self.state, kind, 0, 1, set(), lambda: rebuilt.append(1))  # noqa: SLF001
            first, second = [
                el for el in client.elements.values() if isinstance(el, ui.color_picker)
            ]
            _fire(first.q_color, "change", "#111111")
            _fire(second.q_color, "change", "#222222")
            self.assertEqual(state.store.categories()[0].color, "#111111")
            self.assertEqual(state.store.event_types()[0].color, "#222222")
            # Only the event category can fall back (to the participant's colour).
            resets = [
                el
                for el in client.elements.values()
                if isinstance(el, ui.button) and el.text == "Use the participant's colour"
            ]
            self.assertEqual(len(resets), 1)
            _fire(resets[0], "click")
        self.assertIsNone(state.store.event_types()[0].color)
        self.assertEqual(len(rebuilt), 3)
        self.assertEqual(first.q_color.props["palette"], list(event_task._EVENT_COLORS))  # noqa: SLF001

    async def test_manage_event_types(self) -> None:
        for name in ("A", "B"):
            state.store.add_event_type(name)
        rebuilt: list[int] = []

        def rebuild() -> None:
            rebuilt.append(1)

        event_task._move_event_type("B", -1, rebuild)  # noqa: SLF001
        self.assertEqual([t.name for t in state.store.event_types()], ["B", "A"])
        event_task._move_event_type("B", -1, rebuild)  # noqa: SLF001  # already first
        self.state.active_event_type = "A"
        with ui_client():
            event_task._delete_event_type(self.state, "A", rebuild)  # noqa: SLF001  # unused
        self.assertEqual([t.name for t in state.store.event_types()], ["B"])
        self.assertIsNone(self.state.active_event_type)
        self.assertEqual(len(rebuilt), 2)

    async def test_type_attribute_rows_save_and_remove(self) -> None:
        state.store.add_event_type("Smile", attributes={"a": "1"})
        with ui_client():
            event_task._type_attribute_editor(state.store.event_types()[0], lambda: None)  # noqa: SLF001
        self.assertEqual(event_task._type_attributes("Smile"), {"a": "1"})  # noqa: SLF001
        self.assertEqual(event_task._type_attributes("missing"), {})  # noqa: SLF001
        self.assertEqual(event_task._free_key({"key": "", "key2": ""}), "key3")  # noqa: SLF001

    async def test_export_route_serves_xlsx(self) -> None:
        state.store.add_event_type("Smile", color="#b5e48c")
        state.store.add_event("v", "v::", "Mother", 0, 25, label="Smile", event_type="Smile")
        event_task._event_states["c1"] = self.state  # noqa: SLF001
        response = event_task._export_route("c1", "video", "xlsx")  # noqa: SLF001
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, download.MIME_TYPES["xlsx"])
        self.assertTrue(response.body.startswith(b"PK"))  # a zip, as every .xlsx is
        csv_text = event_task._export_text(self.state, "video", "csv")  # noqa: SLF001
        self.assertIn("Smile,Smile,,#b5e48c", csv_text or "")

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
            video_id="s1_a",
            video_path=None,
            labels={"grp": "g1", "part": "a", "Other": "z", "Empty": "", "Text": "word " * 20},
            label_origins={"Other": "t.xlsx › s · id = g1"},
        )
        state.scan = ScanResult(
            entries=[entry], label_columns=["grp", "part", "Other", "Empty", "Text"]
        )
        state.registry = SourceRegistry()
        state.registry.add(
            DataSource(SourceKind.VIDEO, self.root, layout=VideoLayout("{grp}/clip_{part}.mp4"))
        )
        self.entry = entry

    def tearDown(self) -> None:
        state.store, state.scan, state.registry = self._saved

    async def test_badges_show_only_layout_fields_in_pattern_order(self) -> None:
        self.assertEqual(event_task._layout_badges(self.entry), ["g1", "a"])  # noqa: SLF001

    async def test_label_badges_show_loaded_tags_with_their_origin(self) -> None:
        badges = event_task._label_badges(self.entry)  # noqa: SLF001
        self.assertEqual(badges[0], ("Other: z", "t.xlsx › s · id = g1"))
        text, hint = badges[1]  # a long value is shortened, in full on hover
        self.assertTrue(text.startswith("Text: word") and text.endswith("…"))
        self.assertEqual(hint, "word " * 20)

    async def test_flat_folder_shows_every_label_tag(self) -> None:
        state.registry = SourceRegistry()
        state.registry.add(DataSource(SourceKind.VIDEO, self.root))
        texts = [text for text, _ in event_task._label_badges(self.entry)]  # noqa: SLF001
        self.assertEqual(texts[:3], ["grp: g1", "part: a", "Other: z"])

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


class TestPositionReadout(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved_loop = core.loop
        core.loop = asyncio.get_running_loop()
        quiet_slow_callback_warnings()

    async def asyncTearDown(self) -> None:
        core.loop = self._saved_loop

    def setUp(self) -> None:
        self.state = event_task._EventState(  # noqa: SLF001
            row_key="v::", video_id="v", fps=25.0, num_frames=500, duration=20.0
        )

    def _fields(self) -> event_task._PositionFields:  # noqa: SLF001
        fields = event_task._PositionFields(  # noqa: SLF001
            ui.number(), ui.number(), ui.input()
        )
        self.state.position = fields
        return fields

    def _values(self, f: event_task._PositionFields) -> tuple[object, object, object]:  # noqa: SLF001
        return f.frame_in.value, f.sec_in.value, f.tc_in.value

    async def test_show_writes_consistent_values(self) -> None:
        with ui_client():
            f = self._fields()
            f.show(125, 25.0)
        self.assertEqual(self._values(f), (125, 5.0, "00:05.00"))

    async def test_readout_follows_the_playhead(self) -> None:
        with ui_client():
            f = self._fields()
            self.state.playhead_frame = 50
            event_task._show_position(self.state)  # noqa: SLF001
        self.assertEqual(self._values(f), (50, 2.0, "00:02.00"))

    async def test_readout_holds_still_while_editing(self) -> None:
        with ui_client():
            f = self._fields()
            f.show(10, 25.0)
            f.editing = True
            self.state.playhead_frame = 99
            event_task._show_position(self.state)  # noqa: SLF001
        self.assertEqual(f.frame_in.value, 10)

    async def test_seek_updates_the_readout(self) -> None:
        with ui_client():
            f = self._fields()
            event_task._seek(self.state, "vid", 200)  # noqa: SLF001
        self.assertEqual(self._values(f), (200, 8.0, "00:08.00"))

    async def test_typed_frame_jumps_and_rewrites_the_others(self) -> None:
        with ui_client():
            f = self._fields()
            f.show(0, 25.0)
            f.frame_in.value = 75
            event_task._commit_position(self.state, "vid", "frame")  # noqa: SLF001
        self.assertEqual(self.state.playhead_frame, 75)
        self.assertEqual(self._values(f), (75, 3.0, "00:03.00"))

    async def test_typed_seconds_and_timecode_jump(self) -> None:
        with ui_client():
            f = self._fields()
            f.show(0, 25.0)
            f.sec_in.value = 4.0
            event_task._commit_position(self.state, "vid", "seconds")  # noqa: SLF001
            self.assertEqual(self.state.playhead_frame, 100)
            f.tc_in.value = "00:06.00"
            event_task._commit_position(self.state, "vid", "timecode")  # noqa: SLF001
        self.assertEqual(self.state.playhead_frame, 150)

    async def test_unchanged_field_does_not_seek(self) -> None:
        with ui_client():
            f = self._fields()
            f.show(40, 25.0)
            self.state.playhead_frame = 41  # video moved on; field untouched
            f.editing = True
            event_task._commit_position(self.state, "vid", "frame")  # noqa: SLF001
        self.assertEqual(self.state.playhead_frame, 41)
        self.assertFalse(f.editing)

    async def test_commit_without_fields_is_a_noop(self) -> None:
        self.state.position = None
        event_task._commit_position(self.state, "vid", "frame")  # noqa: SLF001
        self.assertEqual(self.state.playhead_frame, 0)


if __name__ == "__main__":
    unittest.main()
