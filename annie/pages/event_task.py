"""Event-annotation task UI — the video-editor timeline surface (UI).

One queued video at a time: a large HTML5 player centred at the top, a transport bar
(play/pause, ±5 s, frame-step, "Start/End here"), linked frame/second/timecode jump fields,
then the SVG timeline (one lane per track) and an event editor. Events are authored from
scratch and saved to the review DB **immediately** on every change — there is no Save button;
export (JSON for code, CSV for a spreadsheet) is a separate step.

Which video is shown comes from the Browse annotator queue, like Curation. Frame accuracy is
provided by :mod:`annie.pages.videoclock` (HTML5 ``currentTime`` stepping); the timeline is
drawn by :mod:`annie.pages.timeline` (Python owns the model, JS reports gestures). The layout
is fixed: the video and the editor sit side by side on top, with the full-width timeline in
its own row beneath both.

This module is heavy on browser interaction (video, JS bridge, SVG gestures) so its wiring is
verified in the browser; the pure geometry/export/store pieces it calls are unit-tested in
their own modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nicegui import context, ui

from annie.core import logbook, theme
from annie.core.state import state
from annie.dataset.events import (
    clamp_event,
    export_events_csv,
    export_events_json,
    frames_to_seconds,
    seconds_to_frame,
)
from annie.media.decode import media_available, video_metadata
from annie.pages import timeline, videoclock

if TYPE_CHECKING:
    from collections.abc import Callable

    from nicegui.events import GenericEventArguments, KeyEventArguments

    from annie.core.models import VideoEntry
    from annie.dataset.storage import EventRecord

#: Base name for the per-client timeline gesture event (the client id is appended, so one
#: client's ``ui.on`` handler never fires on another's gestures).
_GESTURE_EVENT = "annie-timeline-gesture"

#: Client ids whose gesture ``ui.on`` handler is already registered (it is process-global,
#: so it must be attached exactly once per client, not once per refreshable rebuild).
_gesture_bound: set[str] = set()

#: Fixed video-height steps (px) the −/+ buttons cycle through; aspect ratio stays locked.
#: Extends down to 180 so the video can be halved twice from the default for a timeline-heavy
#: layout, and up to 840 for close frame work.
_VIDEO_HEIGHTS = (180, 240, 360, 480, 600, 720, 840)

#: Name a freshly-created event carries until the reviewer renames it, so a new box is never
#: blank on the timeline; the editor pre-selects it so typing replaces it in one go.
_DEFAULT_EVENT_LABEL = "event_name"

#: The single track every video starts with. A track is just the timeline lane events live
#: on — not a category — so one is enough; it is created on load and never shown to the user
#: as a name (the multi-track UI is intentionally hidden for now).
_DEFAULT_TRACK = "events"

#: Eight pastel, mutually-distinct colours the editor offers for per-event colour-coding. The
#: first (``None`` sentinel handled in the UI) means "use the track's colour"; these override
#: it. Pastel so a filled timeline stays easy on the eye while still separable.
_EVENT_COLORS: tuple[str, ...] = (
    "#a8dadc",  # pale cyan
    "#ffb4a2",  # salmon
    "#b5e48c",  # light green
    "#ffd6a5",  # peach
    "#cdb4db",  # lilac
    "#fdffb6",  # pale yellow
    "#a2d2ff",  # sky blue
    "#ffc8dd",  # pink
)


@dataclass
class _EventState:
    """Per-client Event-annotation position and view settings.

    Attributes:
        row_key: The queued video currently open, or ``None`` before the first loads.
        video_id: Its video id.
        index: Position in the queue (for the n / m readout and prev/next).
        fps: The open video's frames per second (0 until metadata loads).
        num_frames: Its total frame count (0 until metadata loads).
        duration: Its duration in seconds.
        zoom: Timeline zoom — how many times *less* than the whole video is visible; 1.0
            shows the whole clip, higher shows a proportionally narrower window.
        playhead_frame: The last known playhead frame, tracked so the timeline window can
            centre on it and the red line can be redrawn after any seek without a poll.
        video_height: Current video box height in px.
        selected_event: The id of the event open in the editor, or ``None``.
        pending_start: A frame set by ``I`` awaiting an ``O`` to close the event.
        focus_name: Transient flag: the next rebuild should focus the editor's name input
            (set right after finalising a new event, cleared once the focus is issued).
        restore_frame: Transient frame the video should be re-seeked to after a full rebuild
            recreates the ``<video>`` (which the browser resets to 0); ``None`` when no
            restore is pending. Cleared once issued.
    """

    row_key: str | None = None
    video_id: str = ""
    index: int = 0
    fps: float = 0.0
    num_frames: int = 0
    duration: float = 0.0
    zoom: float = 1.0
    playhead_frame: int = 0
    video_height: int = 480
    selected_event: str | None = None
    pending_start: int | None = field(default=None)
    focus_name: bool = False
    restore_frame: int | None = field(default=None)

    def view_window(self) -> tuple[int, int]:
        """The visible ``[start, end]`` frame window at the current zoom, centred on the head.

        At zoom 1.0 the whole clip is shown; higher zoom shows ``num_frames / zoom`` frames
        centred on :attr:`playhead_frame`, clamped so the window never runs off either end.
        """
        total = max(1, self.num_frames)
        if self.zoom <= 1.0 or total <= 1:
            return 0, total - 1
        span = max(1, round((total - 1) / self.zoom))
        start = self.playhead_frame - span // 2
        start = max(0, min(start, total - 1 - span))
        return start, start + span


#: Per-client Event-annotation state, keyed by client id; cleaned up on disconnect.
_event_states: dict[str, _EventState] = {}


def cleanup(client_id: str) -> None:
    """Drop a disconnected client's Event-annotation state and gesture handler flag."""
    _event_states.pop(client_id, None)
    _gesture_bound.discard(client_id)


def _event_state() -> _EventState:
    """The current client's Event-annotation state, created on first use."""
    return _event_states.setdefault(context.client.id, _EventState())


def _queued_entries() -> list[VideoEntry]:
    """The manifest entries queued for annotation (shared with the other tasks)."""
    if state.scan is None:
        return []
    keys = state.store.annotator_keys()
    return [e for e in state.scan.entries if e.key in keys]


def _load_video_if_needed(state_: _EventState, entries: list[VideoEntry]) -> VideoEntry | None:
    """Ensure ``state_`` points at a valid queued video and its metadata is loaded.

    Returns the current entry, or ``None`` when the queue is empty.
    """
    if not entries:
        state_.row_key = None
        return None
    state_.index = max(0, min(state_.index, len(entries) - 1))
    entry = entries[state_.index]
    if state_.row_key == entry.key and state_.num_frames:
        return entry  # already loaded
    state_.row_key = entry.key
    state_.video_id = entry.video_id
    state_.selected_event = None
    state_.pending_start = None
    state_.fps = 0.0
    state_.num_frames = 0
    state_.duration = 0.0
    if entry.video_path is not None and media_available():
        try:
            meta = video_metadata(entry.video_path)
            state_.fps = meta.fps
            state_.num_frames = meta.num_frames
            state_.duration = frames_to_seconds(meta.num_frames, meta.fps)
        except Exception as exc:  # noqa: BLE001 - a bad file must not break the tab
            logbook.report(f"Could not read metadata for {entry.video_path}: {exc}")
    # Every video has exactly one track for now; create it so a fresh video already has a lane
    # to draw events on (the multi-track UI is hidden).
    if _DEFAULT_TRACK not in state.store.tracks_for(entry.key):
        state.store.add_track(entry.key, _DEFAULT_TRACK)
    return entry


def _events_by_track(row_key: str) -> dict[str, list[EventRecord]]:
    """Group a video's stored events by track name (order preserved by the store)."""
    grouped: dict[str, list[EventRecord]] = {}
    for event in state.store.events_for(row_key):
        grouped.setdefault(event.track, []).append(event)
    return grouped


# ── build ────────────────────────────────────────────────────────────────────


@ui.refreshable
def event_annotation_task() -> None:
    """Build the Event-annotation work area for the current queued video."""
    entries = _queued_entries()
    state_ = _event_state()
    entry = _load_video_if_needed(state_, entries)
    if entry is None:
        ui.label("No videos queued. Select rows on the Browse tab to annotate events here.").style(
            f"color:{theme.NEUTRAL}"
        )
        return
    if entry.video_path is None:
        ui.label("This queue item has no video file to annotate.").style(f"color:{theme.NEUTRAL}")
        return
    if not media_available():
        ui.label("Install the 'media' extra to read frame counts and annotate events.").style(
            f"color:{theme.WARNING}"
        )
        return

    _toolbar(state_, entries)
    _keyboard(state_)

    # A single layout: the top row splits into the video (with its transport) and the editor
    # side by side; the timeline sits in its own row beneath, spanning the full width under
    # both — so it is always 100% wide, never cramped into the video column.
    with ui.column().classes("w-full gap-2"):
        with ui.row().classes("w-full items-start gap-3 no-wrap"):
            with ui.column().classes("flex-grow min-w-0 gap-2"):
                _video_and_transport(state_, entry)
            with ui.column().classes("gap-2").style("width:340px;flex:0 0 340px"):
                _editor(state_)
        _timeline_block(state_)


def _toolbar(state_: _EventState, entries: list[VideoEntry]) -> None:
    """Queue position, prev/next video, and the export menu."""
    with ui.card().classes("w-full"), ui.row().classes("w-full items-center gap-3 wrap"):
        ui.button(icon="chevron_left", on_click=lambda: _step_video(state_, -1)).props(
            "flat round"
        ).tooltip("Previous video")
        ui.label(f"video {state_.index + 1} / {len(entries)} — {state_.video_id}").classes(
            "text-sm font-medium"
        )
        ui.button(icon="chevron_right", on_click=lambda: _step_video(state_, 1)).props(
            "flat round"
        ).tooltip("Next video")

        ui.element("div").classes("flex-grow")

        with ui.button("Export", icon="download").props("flat dense"):
            with ui.menu():
                ui.menu_item(
                    "This video — JSON", lambda: _export(state_, scope="video", fmt="json")
                )
                ui.menu_item("This video — CSV", lambda: _export(state_, scope="video", fmt="csv"))
                ui.menu_item(
                    "All session — JSON", lambda: _export(state_, scope="session", fmt="json")
                )
                ui.menu_item(
                    "All session — CSV", lambda: _export(state_, scope="session", fmt="csv")
                )


def _video_and_transport(state_: _EventState, entry: VideoEntry) -> None:
    """The centred HTML5 player, the video-size buttons, transport bar, and jump fields."""
    vid_id = videoclock.element_id(context.client.id)
    with ui.row().classes("w-full justify-end"):
        ui.label("video size").classes("text-xs self-center").style(f"color:{theme.NEUTRAL}")
        ui.button(icon="remove", on_click=lambda: _resize_video(state_, -1)).props(
            "flat dense round"
        )
        ui.button(icon="add", on_click=lambda: _resize_video(state_, 1)).props("flat dense round")
    video_path = entry.video_path
    assert video_path is not None  # noqa: S101 - the caller returns early for no-video items
    with ui.row().classes("w-full justify-center"):
        (
            ui.video(video_path, controls=True)
            .props(f"id={vid_id}")
            .style(f"height:{state_.video_height}px;max-width:100%;aspect-ratio:16/9")
        )
    if state_.restore_frame is not None:
        # A full rebuild recreated the <video> (which resets to frame 0); put it back where the
        # reviewer was. Deferred so the new element exists before the seek runs.
        target = state_.restore_frame
        state_.restore_frame = None
        ui.timer(0.05, lambda: videoclock.seek_frame(vid_id, target, state_.fps), once=True)

    with ui.row().classes("w-full items-center justify-center gap-2 wrap"):
        ui.button(icon="replay_5", on_click=lambda: videoclock.nudge_seconds(vid_id, -5)).props(
            "flat round"
        ).tooltip("Back 5 s [Shift+←]")
        ui.button(
            icon="skip_previous", on_click=lambda: videoclock.step_frames(vid_id, -1, state_.fps)
        ).props("flat round").tooltip("Previous frame [←]")
        ui.button(icon="play_arrow", on_click=lambda: videoclock.toggle(vid_id)).props(
            "flat round"
        ).tooltip("Play / pause [space]")
        ui.button(
            icon="skip_next", on_click=lambda: videoclock.step_frames(vid_id, 1, state_.fps)
        ).props("flat round").tooltip("Next frame [→]")
        ui.button(icon="forward_5", on_click=lambda: videoclock.nudge_seconds(vid_id, 5)).props(
            "flat round"
        ).tooltip("Forward 5 s [Shift+→]")

    _jump_fields(state_, vid_id)

    _capture_controls(state_)
    _start_playhead_poll(state_, vid_id)


@ui.refreshable
def _capture_controls(state_: _EventState) -> None:
    """The single dynamic capture button, plus Cancel while a start is armed.

    Idle shows a neutral *Set start (I)*; once a start is marked the same slot becomes an
    amber *Set end (O)* with a *Cancel (Esc)* beside it, so exactly one primary action is
    offered at a time and the armed state is unmistakable.

    This is its **own** refreshable so arming/cancelling swaps the button without rebuilding
    the whole task — rebuilding would recreate the ``<video>`` element, which resets it to
    frame 0. Arming must keep the video exactly where it is (a start marks *this* moment).
    """
    with ui.row().classes("w-full items-center justify-center gap-2"):
        if state_.pending_start is None:
            ui.button("Set start (I)", icon="flag", on_click=lambda: _arm_start(state_)).props(
                "unelevated dense"
            ).tooltip("Mark the current frame as an event's start [i]")
        else:
            ui.button("Set end (O)", icon="stop", on_click=lambda: _finalise(state_)).props(
                "unelevated dense"
            ).style(f"background:{theme.EVENT_PENDING} !important;color:#111 !important").tooltip(
                "Close the event at the current frame [o]"
            )
            ui.button("Cancel (Esc)", icon="close", on_click=lambda: _cancel_pending(state_)).props(
                "flat dense"
            ).tooltip("Discard the started event [Esc]")


def _start_playhead_poll(state_: _EventState, vid_id: str) -> None:
    """Keep ``playhead_frame`` and the red line in step with the real video time.

    The transport buttons and the ``<video>`` element's own controls change ``currentTime``
    in the browser without telling Python, so a light poll reads it back a few times a second
    and moves the line — the single source of truth for "where the video actually is". The
    timer lives inside the refreshable, so each rebuild replaces the previous one rather than
    stacking polls.
    """
    svg_id = f"annie-timeline-{context.client.id}"

    async def _tick() -> None:
        try:
            seconds = await videoclock.current_time(vid_id)
        except Exception:  # noqa: BLE001 - the element may be gone between ticks
            return
        frame = seconds_to_frame(float(seconds or 0.0), state_.fps)
        if frame == state_.playhead_frame:
            return
        state_.playhead_frame = frame
        _sync_playhead(state_, svg_id)
        # While a start is armed, grow the amber band's right edge with the playhead — a cheap
        # JS resize, no rebuild, so it tracks playback smoothly.
        if state_.pending_start is not None:
            _sync_pending_band(state_, svg_id)

    ui.timer(0.25, _tick)


def _jump_fields(state_: _EventState, vid_id: str) -> None:
    """The linked frame / seconds / timecode fields that seek the playhead when typed.

    All three stay consistent: editing any one seeks the video, moves the red timeline line,
    and rewrites the other two. They start at the current playhead, not zero, so returning to
    a video shows where you were.
    """
    head = state_.playhead_frame
    head_sec = frames_to_seconds(head, state_.fps)
    with ui.row().classes("w-full items-center justify-center gap-3 wrap"):
        ui.label("Jump to").classes("text-xs").style(f"color:{theme.NEUTRAL}")
        frame_in = ui.number("frame", value=head, min=0, precision=0).props("dense").classes("w-28")
        sec_in = (
            ui.number("seconds", value=round(head_sec, 3), min=0).props("dense").classes("w-28")
        )
        tc_in = (
            ui.input("mm:ss.ff", value=_format_timecode(head_sec)).props("dense").classes("w-28")
        )

        def seek_to_frame(frame: int) -> None:
            frame = _seek(state_, vid_id, frame)
            _sync_jump_fields(state_, frame, frame_in, sec_in, tc_in)

        frame_in.on("blur", lambda: seek_to_frame(int(frame_in.value or 0)))
        sec_in.on(
            "blur",
            lambda: seek_to_frame(seconds_to_frame(float(sec_in.value or 0.0), state_.fps)),
        )
        tc_in.on(
            "blur",
            lambda: seek_to_frame(seconds_to_frame(_parse_timecode(tc_in.value), state_.fps)),
        )


def _timeline_block(state_: _EventState) -> None:
    """The zoom controls and the SVG timeline itself."""
    with ui.row().classes("w-full items-center gap-2"):
        ui.label("Timeline").classes("text-sm font-medium")
        ui.button(icon="zoom_out", on_click=lambda: _zoom(state_, 1 / 1.5)).props(
            "flat dense round"
        )
        ui.button(icon="zoom_in", on_click=lambda: _zoom(state_, 1.5)).props("flat dense round")

    grouped = _events_by_track(state_.row_key or "")
    track_names = state.store.tracks_for(state_.row_key or "")
    tracks = [
        timeline.TimelineTrack(
            name,
            [
                timeline.TimelineEvent(
                    e.event_id,
                    e.start_frame,
                    e.end_frame,
                    e.label,
                    selected=e.event_id == state_.selected_event,
                    color=e.color,
                )
                for e in grouped.get(name, [])
            ],
        )
        for name in track_names
    ]
    svg_id = f"annie-timeline-{context.client.id}"
    view_start, view_end = state_.view_window()
    svg = timeline.build_svg(
        tracks,
        view_start=view_start,
        view_end=view_end,
        fps=state_.fps,
        svg_id=svg_id,
        pending_start=state_.pending_start,
        pending_end=state_.playhead_frame,
    )
    # The surface is always full width; the wrapper and the ui.html element both stretch to
    # 100% so the SVG's own width:100% actually fills the column (a bare ui.html shrinks to
    # its content otherwise). Zoom narrows the time window instead of widening the element.
    ui.html(svg).classes("w-full").style("width:100%")
    # Gestures come back through NiceGUI's global event bus (emitEvent → ui.on), so there is
    # no fragile DOM-event forwarding. Registered once per client (guarded), since ui.on is
    # process-global and this refreshable rebuilds often.
    _ensure_gesture_handler(state_)
    ui.run_javascript(timeline.gesture_script(svg_id, _gesture_event_name()))
    # Put the red playhead line where the current frame sits within the visible window.
    _sync_playhead(state_, svg_id)


def _editor(state_: _EventState) -> None:
    """The selected event's editor: name, note, start/end, key-value attributes, delete."""
    with ui.card().classes("w-full"):
        if state_.selected_event is None:
            ui.label("Select or create an event to edit it.").style(f"color:{theme.NEUTRAL}")
            return
        event = state.store.get_event(state_.selected_event)
        if event is None:
            state_.selected_event = None
            ui.label("Select or create an event to edit it.").style(f"color:{theme.NEUTRAL}")
            return

        with ui.row().classes("w-full items-center gap-2"):
            ui.label("Event").classes("text-sm font-medium flex-grow")
            ui.button(icon="delete", on_click=lambda: _delete_event(state_)).props(
                "flat dense round"
            ).tooltip("Delete this event [Del]")

        # The event's name is its timeline label — stored in the same `label` field.
        name_id = f"annie-event-name-{context.client.id}"
        name_in = ui.input("name", value=event.label).props(f"dense id={name_id}").classes("w-full")
        name_in.on("blur", lambda: _update(state_, label=name_in.value))
        if state_.focus_name:
            # Just finalised a new event: focus the name and select its text so the reviewer
            # types over the default in one go. One-shot — cleared so later rebuilds don't
            # steal focus. The input the native id lands on is the inner <input> element.
            state_.focus_name = False
            ui.timer(
                0.05,
                lambda: ui.run_javascript(
                    f"const w=document.getElementById('{name_id}');"
                    f" const el=w?w.querySelector('input'):null;"
                    f" if(el){{el.focus();el.select();}}"
                ),
                once=True,
            )

        _color_picker(state_, event)

        # Start and end each get their own row: a frame field and a seconds field kept in
        # two-way sync, so either can be typed. Both commit through clamp_event, which keeps
        # start <= end and both inside the video.
        bounds = {"start": event.start_frame, "end": event.end_frame}

        def commit_bounds() -> None:
            start, end = clamp_event(bounds["start"], bounds["end"], state_.num_frames)
            _update(state_, start_frame=start, end_frame=end)

        _bound_row(state_, "Start", bounds, "start", commit_bounds)
        _bound_row(state_, "End", bounds, "end", commit_bounds)

        note_in = ui.input("note", value=event.note).props("dense").classes("w-full")
        note_in.on("blur", lambda: _update(state_, note=note_in.value))

        _attribute_editor(state_, event)


def _bound_row(
    state_: _EventState,
    title: str,
    bounds: dict[str, int],
    key: str,
    commit: Callable[[], None],
) -> None:
    """One boundary row: ``<title>`` then a ``frame`` and a ``time (s)`` field, kept in sync.

    Editing either field updates ``bounds[key]`` and the other field, then commits — so the
    frame and seconds views of the same boundary never disagree. The seconds field rounds to
    milliseconds; the frame field is the source of truth stored on the event.

    Args:
        state_: The client's event state (for the fps).
        title: Row label, ``"Start"`` or ``"End"``.
        bounds: The mutable ``{"start": frame, "end": frame}`` map the fields write into.
        key: Which entry of ``bounds`` this row edits.
        commit: Persists the (clamped) bounds and rebuilds.
    """
    with ui.row().classes("w-full items-center gap-2 no-wrap"):
        ui.label(title).classes("text-xs w-10").style(f"color:{theme.NEUTRAL}")
        frame_in = (
            ui.number("frame", value=bounds[key], min=0, precision=0)
            .props("dense")
            .classes("flex-grow")
        )
        sec_in = (
            ui.number("time (s)", value=round(frames_to_seconds(bounds[key], state_.fps), 3), min=0)
            .props("dense")
            .classes("flex-grow")
        )

        def from_frame() -> None:
            bounds[key] = int(frame_in.value or 0)
            sec_in.set_value(round(frames_to_seconds(bounds[key], state_.fps), 3))
            commit()

        def from_sec() -> None:
            bounds[key] = seconds_to_frame(float(sec_in.value or 0.0), state_.fps)
            frame_in.set_value(bounds[key])
            commit()

        frame_in.on("blur", from_frame)
        sec_in.on("blur", from_sec)


def _color_picker(state_: _EventState, event: EventRecord) -> None:
    """A row of eight pastel swatches (plus a reset) to colour-code the selected event.

    Clicking a swatch sets the event's colour override; the reset returns it to the track's
    colour. The current choice is ringed so it reads at a glance.
    """
    with ui.row().classes("w-full items-center gap-1 wrap"):
        ui.label("Colour").classes("text-xs").style(f"color:{theme.NEUTRAL}")
        for swatch in _EVENT_COLORS:
            selected = event.color == swatch
            ring = theme.EVENT_SELECTED if selected else "#00000055"
            # A plain div, not a q-btn: Quasar's button padding/min-width warps a tiny
            # round button into an ellipse. A fixed-size div with border-radius:50% is a
            # true circle. The click is wired with .on('click', ...).
            swatch_el = ui.element("div").style(
                f"background:{swatch};width:22px;height:22px;min-width:22px;min-height:22px;"
                f"border-radius:50%;border:2px solid {ring};cursor:pointer;box-sizing:border-box"
            )
            swatch_el.on("click", lambda c=swatch: _set_color(state_, c))
            swatch_el.tooltip(swatch)
        ui.button(icon="format_color_reset", on_click=lambda: _set_color(state_, None)).props(
            "flat dense round"
        ).tooltip("Use the track's colour")


def _set_color(state_: _EventState, color: str | None) -> None:
    """Set (or clear) the selected event's colour override and rebuild."""
    if state_.selected_event is None:
        return
    state.store.update_event(state_.selected_event, color=color)
    event_annotation_task.refresh()


def _attribute_editor(state_: _EventState, event: EventRecord) -> None:
    """The add/remove key-value attribute rows for one event."""
    ui.label("Attributes").classes("text-xs").style(f"color:{theme.NEUTRAL}")
    with ui.column().classes("w-full gap-1"):
        for key, value in list(event.attributes.items()):
            _attribute_row(state_, key, value)
    ui.button("Add attribute", icon="add", on_click=lambda: _add_attribute(state_)).props(
        "flat dense"
    )


def _attribute_row(state_: _EventState, key: str, value: str) -> None:
    """One editable key/value attribute row; edits rewrite the event's whole attribute map.

    ``key`` is captured per row (each row is its own function scope), so renaming or removing
    a key acts on the right entry without the default-argument loop-capture trick.
    """
    with ui.row().classes("w-full items-center gap-1 no-wrap"):
        key_in = ui.input(placeholder="key", value=key).props("dense").classes("flex-grow")
        val_in = ui.input(placeholder="value", value=value).props("dense").classes("flex-grow")

        def _save() -> None:
            attrs = dict(_current_attributes(state_))
            attrs.pop(key, None)
            new_key = (key_in.value or "").strip()
            if new_key:
                attrs[new_key] = val_in.value or ""
            _update(state_, attributes=attrs)

        def _remove() -> None:
            attrs = dict(_current_attributes(state_))
            attrs.pop(key, None)
            _update(state_, attributes=attrs)

        key_in.on("blur", _save)
        val_in.on("blur", _save)
        ui.button(icon="close", on_click=_remove).props("flat dense round")


# ── actions ────────────────────────────────────────────────────────────────────


def _current_attributes(state_: _EventState) -> dict[str, str]:
    """Read the selected event's current attributes fresh from the store."""
    if state_.selected_event is None:
        return {}
    event = state.store.get_event(state_.selected_event)
    return dict(event.attributes) if event is not None else {}


def _add_attribute(state_: _EventState) -> None:
    """Add a blank attribute key so the reviewer can fill it in."""
    attrs = dict(_current_attributes(state_))
    base, i = "key", 1
    key = base
    while key in attrs:
        i += 1
        key = f"{base}{i}"
    attrs[key] = ""
    _update(state_, attributes=attrs)


def _update(state_: _EventState, **fields: object) -> None:
    """Persist a change to the selected event and rebuild."""
    if state_.selected_event is None:
        return
    state.store.update_event(state_.selected_event, **fields)
    event_annotation_task.refresh()


def _delete_event(state_: _EventState) -> None:
    """Delete the selected event and clear the editor."""
    if state_.selected_event is None:
        return
    state.store.delete_event(state_.selected_event)
    state_.selected_event = None
    event_annotation_task.refresh()


def _step_video(state_: _EventState, delta: int) -> None:
    """Move to the previous/next queued video."""
    state_.index += delta
    state_.row_key = None  # force a reload of metadata + selection
    event_annotation_task.refresh()


def _resize_video(state_: _EventState, delta: int) -> None:
    """Step the video height through the fixed size list."""
    heights = _VIDEO_HEIGHTS
    try:
        i = heights.index(state_.video_height)
    except ValueError:
        i = 1
    state_.video_height = heights[max(0, min(i + delta, len(heights) - 1))]
    event_annotation_task.refresh()


def _zoom(state_: _EventState, factor: float) -> None:
    """Change the timeline zoom, then rebuild the (fixed-width) surface.

    Zoom is "how many times less than the whole clip is visible", so it never drops below
    1.0 (the whole clip) and the surface width is unaffected — only the visible window
    narrows. The window re-centres on the playhead via :meth:`_EventState.view_window`.
    """
    state_.zoom = max(1.0, min(state_.zoom * factor, 100.0))
    event_annotation_task.refresh()


async def _arm_start(state_: _EventState) -> None:
    """Mark the current frame as an event's start and enter the armed (capturing) state.

    Reads the true browser time so the start matches exactly where the video is paused, stores
    it as ``pending_start``, then refreshes **only** the button and shows the band via JS — the
    video element is left untouched, so it stays on the current frame (it must not jump to 0).
    """
    vid_id = videoclock.element_id(context.client.id)
    seconds = await videoclock.current_time(vid_id)
    state_.playhead_frame = seconds_to_frame(float(seconds or 0.0), state_.fps)
    state_.pending_start = state_.playhead_frame
    _capture_controls.refresh()  # swap Set start → Set end, without rebuilding the video
    _sync_pending_band(state_, f"annie-timeline-{context.client.id}")


async def _finalise(state_: _EventState) -> None:
    """Close the armed event at the current frame, select it, and focus its name field.

    The span runs from ``pending_start`` (or the current frame, if end is pressed without a
    prior start) to the current frame, clamped into the video. The new event is created on the
    single default track with the default name and selected; ``focus_name`` asks the next
    rebuild to focus the editor's name input so the reviewer can type over ``event_name``.
    """
    vid_id = videoclock.element_id(context.client.id)
    seconds = await videoclock.current_time(vid_id)
    frame = seconds_to_frame(float(seconds or 0.0), state_.fps)
    state_.playhead_frame = frame
    start = state_.pending_start if state_.pending_start is not None else frame
    lo, hi = clamp_event(start, frame, state_.num_frames)
    record = state.store.add_event(
        state_.video_id, state_.row_key or "", _DEFAULT_TRACK, lo, hi, label=_DEFAULT_EVENT_LABEL
    )
    state_.pending_start = None
    state_.selected_event = record.event_id
    state_.focus_name = True
    # A full rebuild is needed so the new event box appears on the timeline; that recreates the
    # <video>, which the browser resets to frame 0, so restore it to where the reviewer was.
    state_.restore_frame = frame
    event_annotation_task.refresh()


def _cancel_pending(state_: _EventState) -> None:
    """Discard an armed start without creating an event.

    Like arming, this refreshes only the button and clears the band via JS, leaving the
    ``<video>`` untouched so it stays on the current frame.
    """
    state_.pending_start = None
    _capture_controls.refresh()
    _sync_pending_band(state_, f"annie-timeline-{context.client.id}")


def _gesture_event_name() -> str:
    """The current client's timeline gesture event name (client-scoped)."""
    return f"{_GESTURE_EVENT}-{context.client.id}"


def _ensure_gesture_handler(state_: _EventState) -> None:
    """Register this client's ``ui.on`` gesture handler exactly once.

    ``ui.on`` is process-global, so binding it inside the refreshable would stack a new
    handler on every rebuild. The per-client guard set keeps it to one; the handler is
    dropped in :func:`cleanup` when the client disconnects.
    """
    cid = context.client.id
    if cid in _gesture_bound:
        return
    ui.on(_gesture_event_name(), lambda event: _on_gesture(state_, event))
    _gesture_bound.add(cid)


def _on_gesture(state_: _EventState, event: GenericEventArguments) -> None:
    """Handle a timeline gesture: select an event box, or seek anywhere else.

    The timeline never creates events (that is what START/END EVENT and the i/o keys are
    for), so there are only two kinds. Selecting a box seeks the video and the red line to
    that event's start frame, so the reviewer lands on the scene it marks.
    """
    detail = event.args or {}
    kind = detail.get("kind")
    vid_id = videoclock.element_id(context.client.id)
    if kind == "select":
        state_.selected_event = detail.get("event_id")
        selected = state.store.get_event(state_.selected_event) if state_.selected_event else None
        if selected is not None:
            _seek(state_, vid_id, selected.start_frame)
        event_annotation_task.refresh()  # redraw so the new selection highlights
        return
    # Any non-select gesture is a plain seek to the clicked position.
    frame = _fraction_to_frame(state_, float(detail.get("x", 0.0)))
    _seek(state_, vid_id, frame)


def _export(state_: _EventState, *, scope: str, fmt: str) -> None:
    """Write the current video's or the whole session's events to JSON/CSV."""
    if scope == "video":
        events = state.store.events_for(state_.row_key or "")
        stem = state_.video_id or "video"
    else:
        events = state.store.all_events()
        stem = "session"
    if not events:
        ui.notify("No events to export yet.", color=theme.WARNING)
        return
    fps_by_video = _fps_by_video(events)
    out = settings_temp_dir() / f"annie_events_{stem}.{fmt}"
    if fmt == "json":
        export_events_json(events, fps_by_video, out)
    else:
        export_events_csv(events, fps_by_video, out)
    ui.notify(f"Exported {len(events)} event(s) → {out}", color=theme.PRIMARY)


# ── helpers ────────────────────────────────────────────────────────────────────


def _fps_by_video(events: list[EventRecord]) -> dict[str, float]:
    """Best-effort fps per video id for the export, from the scan manifest.

    The open video's fps is known on ``_EventState``; other videos in a session export are
    looked up from the manifest and probed once. A video whose fps cannot be read exports
    with frame numbers only (seconds ``0``), which the export handles.
    """
    fps: dict[str, float] = {}
    state_ = _event_state()
    if state_.video_id and state_.fps:
        fps[state_.video_id] = state_.fps
    scan = state.scan
    for event in events:
        if event.video_id in fps:
            continue
        if scan is None:
            continue
        entry = scan.by_video_id.get(event.video_id)
        if entry is None or entry.video_path is None or not media_available():
            continue
        try:
            fps[event.video_id] = video_metadata(entry.video_path).fps
        except Exception:  # noqa: BLE001 - a bad file just exports with frames only
            continue
    return fps


def _fraction_to_frame(state_: _EventState, fraction: float) -> int:
    """Map an ``[0, 1]`` x fraction of the *visible window* to an absolute frame index.

    The fraction is relative to the timeline's current view (which zoom may have narrowed),
    so it is mapped through ``view_window`` rather than the whole clip.
    """
    if state_.num_frames <= 1:
        return 0
    view_start, view_end = state_.view_window()
    frame = round(view_start + fraction * (view_end - view_start))
    return max(0, min(frame, state_.num_frames - 1))


def _seek(state_: _EventState, vid_id: str, frame: int) -> int:
    """Seek the video to ``frame``, record it as the playhead, and move the timeline line.

    Returns the clamped frame actually sought. When the timeline is zoomed and the new
    playhead would fall outside the current window, the task is rebuilt so the window
    re-centres; otherwise only the cheap red-line move runs (no full re-render).
    """
    frame = max(0, min(frame, max(0, state_.num_frames - 1)))
    before = state_.view_window()
    state_.playhead_frame = frame
    videoclock.seek_frame(vid_id, frame, state_.fps)
    after = state_.view_window()
    if after != before:
        event_annotation_task.refresh()  # window shifted; redraw at the new offset
    else:
        _sync_playhead(state_, f"annie-timeline-{context.client.id}")
    return frame


def _sync_playhead(state_: _EventState, svg_id: str) -> None:
    """Move the red playhead line to the current frame's position within the window."""
    view_start, view_end = state_.view_window()
    span = view_end - view_start
    if span <= 0:
        return
    fraction = (state_.playhead_frame - view_start) / span
    videoclock.set_playhead_x(svg_id, fraction)


def _sync_pending_band(state_: _EventState, svg_id: str) -> None:
    """Resize the amber capture band from the armed start to the live playhead (no rebuild).

    When nothing is armed, collapse the band to zero width so cancelling hides it without a
    rebuild.
    """
    view_start, view_end = state_.view_window()
    span = view_end - view_start
    if span <= 0:
        return
    if state_.pending_start is None:
        videoclock.set_pending_band(svg_id, 0.0, 0.0)
        return
    start_frac = (state_.pending_start - view_start) / span
    end_frac = (state_.playhead_frame - view_start) / span
    videoclock.set_pending_band(svg_id, start_frac, end_frac)


def _sync_jump_fields(
    state_: _EventState, frame: int, frame_in: ui.number, sec_in: ui.number, tc_in: ui.input
) -> None:
    """Rewrite the three linked jump fields to a consistent frame/second/timecode."""
    seconds = frames_to_seconds(frame, state_.fps)
    frame_in.set_value(frame)
    sec_in.set_value(round(seconds, 3))
    tc_in.set_value(_format_timecode(seconds))


def _format_timecode(seconds: float) -> str:
    """Format seconds as ``mm:ss.ff`` (hundredths)."""
    seconds = max(0.0, seconds)
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes:02d}:{rest:05.2f}"


def _parse_timecode(text: str) -> float:
    """Parse ``mm:ss.ff`` (or a bare seconds string) to seconds; ``0`` on nonsense."""
    text = (text or "").strip()
    if not text:
        return 0.0
    try:
        if ":" in text:
            minutes, rest = text.split(":", 1)
            return int(minutes) * 60 + float(rest)
        return float(text)
    except ValueError:
        return 0.0


def settings_temp_dir():  # noqa: ANN201 - thin indirection kept import-local
    """Return the export directory (``settings.temp_dir``), created if missing."""
    from annie.core.config import settings

    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    return settings.temp_dir


def _keyboard(state_: _EventState) -> None:
    """Bind transport + marking keys; a fresh handler each rebuild (like segment review)."""
    vid_id = videoclock.element_id(context.client.id)

    def on_key(event: KeyEventArguments) -> None:
        if not event.action.keydown or event.action.repeat:
            return
        key = event.key
        if key == " ":
            videoclock.toggle(vid_id)
        elif key.arrow_left and event.modifiers.shift:
            videoclock.nudge_seconds(vid_id, -5)
        elif key.arrow_right and event.modifiers.shift:
            videoclock.nudge_seconds(vid_id, 5)
        elif key.arrow_left or key == ",":
            videoclock.step_frames(vid_id, -1, state_.fps)
        elif key.arrow_right or key == ".":
            videoclock.step_frames(vid_id, 1, state_.fps)
        elif key == "i":
            ui.timer(0.0, lambda: _arm_start(state_), once=True)
        elif key == "o":
            ui.timer(0.0, lambda: _finalise(state_), once=True)
        elif key == "Escape":
            if state_.pending_start is not None:
                _cancel_pending(state_)
        elif key == "Delete" or key == "Backspace":
            _delete_event(state_)

    ui.keyboard(on_key=on_key)
