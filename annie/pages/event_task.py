"""Event-annotation task UI — the video-editor timeline surface (UI).

One queued video at a time: a large HTML5 player centred at the top, a transport bar
(play/pause, ±5 s, frame-step, "Start/End here"), linked frame/second/timecode jump fields,
then the SVG timeline (one lane per track) and an event editor. Events are authored from
scratch and saved to the review DB **immediately** on every change — there is no Save button;
export (JSON for code, CSV or XLSX for a spreadsheet) is a separate step.

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

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import Response
from nicegui import app, context, run, ui

from annie.app_state import state
from annie.core import theme
from annie.dataset.events import (
    clamp_event,
    effective_attributes,
    effective_color,
    events_to_csv_text,
    events_to_json_text,
    events_to_xlsx_bytes,
    frames_to_seconds,
    seconds_to_frame,
)
from annie.media.decode import media_available, video_metadata
from annie.media.preview import report_decode_failure
from annie.pages import download, pending, timeline, videoclock
from annie.pages.lazy import schedule
from annie.pages.refresh import client_refreshable
from annie.pages.timeline_view import TimelineView
from annie.pages.visibility import only_while_shown
from annie.parsers.csvmeta import short_name

if TYPE_CHECKING:
    from collections.abc import Callable

    from nicegui.events import GenericEventArguments, KeyEventArguments

    from annie.core.models import VideoEntry
    from annie.dataset.storage import EventRecord, EventType

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

#: Fallback track name for an event captured before any category exists. Normally an event
#: lands on the active category; this only applies if the user somehow captures with an empty
#: category set (the UI blocks that by asking for a category first).
_DEFAULT_TRACK = "events"

#: The category a fresh dataset starts with, so annotation can begin immediately.
_DEFAULT_CATEGORY_NAME = "Participant 1"

#: The export menu's formats, by file extension: JSON for code, CSV and Excel for a spreadsheet.
_EXPORT_FORMATS: dict[str, str] = {"json": "JSON", "csv": "CSV", "xlsx": "Excel (XLSX)"}


def next_default_category_name(existing: list[str], prefix: str = "Participant") -> str:
    """Return the next free ``"<prefix> N"`` name given the existing names.

    Used when a participant or event category is added without a typed name, so "just
    click Add" still yields a clean, consistent label (``Participant 2``, ``Event 3``, …)
    and never a collision.

    Args:
        existing: The names already in use.
        prefix: The name's first word, ``"Participant"`` or ``"Event"``.

    Returns:
        ``"<prefix> N"`` for the smallest ``N >= 1`` not already taken.
    """
    taken = set(existing)
    n = 1
    while f"{prefix} {n}" in taken:
        n += 1
    return f"{prefix} {n}"


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
class _PositionFields:
    """The frame / seconds / timecode inputs, shown as the live position and typed to jump.

    Attributes:
        frame_in: Frame-number input.
        sec_in: Seconds input.
        tc_in: ``mm:ss.ff`` input.
        editing: Whether the user is typing in one of them (the live readout then holds still).
        shown: The ``(frame, seconds, timecode)`` last written by :meth:`show`, so a field the
            user focused but did not change never triggers a seek.
    """

    frame_in: ui.number
    sec_in: ui.number
    tc_in: ui.input
    editing: bool = False
    shown: tuple[int, float, str] = (0, 0.0, "")

    def show(self, frame: int, fps: float) -> None:
        """Write one frame to all three inputs, consistently."""
        seconds = round(frames_to_seconds(frame, fps), 3)
        timecode = _format_timecode(seconds)
        self.shown = (frame, seconds, timecode)
        self.frame_in.set_value(frame)
        self.sec_in.set_value(seconds)
        self.tc_in.set_value(timecode)


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
        view: The timeline's visible window (zoom + start frame). Independent state: the
            playhead moves inside it, and panning/zooming/following change only the window.
        follow: Whether the window pages along with playback. Manual panning turns it off.
        playhead_frame: The last known playhead frame, tracked so the red line can be redrawn
            after any seek without a poll.
        video_height: Current video box height in px.
        selected_event: The id of the event open in the editor, or ``None``.
        pending_start: A frame set by ``I`` awaiting an ``O`` to close the event.
        restore_frame: Transient frame the video should be re-seeked to after a full rebuild
            recreates the ``<video>`` (which the browser resets to 0); ``None`` when no
            restore is pending. Cleared once issued.
        position: The live frame/seconds/timecode inputs of the current build, or ``None``
            before they exist. Transient: replaced on every rebuild.
        active_category: The participant category a newly-captured event lands on. Defaults to
            the first category; ``None`` when the dataset has no categories yet.
        active_event_type: The event category a newly-captured event is made with (its name,
            colour and default attributes), or ``None`` for a plain event.
        meta_key: The video whose metadata read has finished (successfully or not), so it
            is not read again; ``None`` until the first read completes.
        meta_loading: The video whose metadata is being read right now, so a rebuild
            during the read does not start a second one.
    """

    row_key: str | None = None
    video_id: str = ""
    index: int = 0
    fps: float = 0.0
    num_frames: int = 0
    duration: float = 0.0
    view: TimelineView = field(default_factory=lambda: TimelineView(0))
    follow: bool = True
    playhead_frame: int = 0
    video_height: int = 480
    selected_event: str | None = None
    pending_start: int | None = field(default=None)
    restore_frame: int | None = field(default=None)
    active_category: str | None = field(default=None)
    active_event_type: str | None = field(default=None)
    position: _PositionFields | None = field(default=None, repr=False)
    meta_key: str | None = None
    meta_loading: str | None = None

    def __post_init__(self) -> None:
        self.reset_view()

    def reset_view(self) -> None:
        """Show the whole clip again, sized to the current :attr:`num_frames`."""
        self.view = TimelineView(self.num_frames)


_POLL_SECONDS = 0.1
"""How often the real video time is read back, so the position readout and the red line track
playback and frame steps within about a tenth of a second."""

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
    """Ensure ``state_`` points at a valid queued video (its metadata loads separately).

    Opening the video file to read its metadata is slow, so it is not done here: see
    :func:`needs_metadata` and :func:`load_metadata`, which run it off the event loop.

    Returns the current entry, or ``None`` when the queue is empty.
    """
    if not entries:
        state_.row_key = None
        return None
    state_.index = max(0, min(state_.index, len(entries) - 1))
    entry = entries[state_.index]
    if state_.row_key == entry.key:
        return entry  # already selected
    state_.row_key = entry.key
    state_.video_id = entry.video_id
    state_.selected_event = None
    state_.pending_start = None
    state_.fps = 0.0
    state_.num_frames = 0
    state_.duration = 0.0
    state_.reset_view()
    # Categories are DB-scoped (shared by every video of this dataset), not per-video. Seed
    # them from any pre-existing event track names (back-compat for the old single-lane model);
    # if the dataset still has none, create a first "Participant 1" so annotation can start
    # immediately rather than facing an empty "Add category" prompt. Then point the active
    # category at the first one.
    state.store.seed_categories_from_events()
    if not state.store.categories():
        state.store.add_category(_DEFAULT_CATEGORY_NAME)
    _ensure_active_category(state_)
    _ensure_active_event_type(state_)
    return entry


def needs_metadata(state_: _EventState, entry: VideoEntry) -> bool:
    """Whether ``entry``'s metadata still has to be read before its work area can be built."""
    return entry.video_path is not None and media_available() and state_.meta_key != entry.key


async def load_metadata(state_: _EventState, entry: VideoEntry) -> bool:
    """Read ``entry``'s frame rate and length off the event loop and apply them.

    A result that arrives after the reviewer moved on to another video is dropped, so
    stepping quickly always ends on the video asked for last.

    Args:
        state_: The client's Event-annotation state.
        entry: The video whose metadata to read.

    Returns:
        ``True`` if ``state_`` now holds this video's metadata (or its read failed, which
        is reported once), ``False`` if the read was stale, duplicate or not needed.
    """
    path = entry.video_path
    if path is None or not needs_metadata(state_, entry) or state_.meta_loading == entry.key:
        return False
    state_.meta_loading = entry.key
    try:
        meta = await run.io_bound(video_metadata, path)
    except Exception as exc:  # noqa: BLE001 - a bad file must not break the tab
        report_decode_failure(path, exc)  # once per problem, not per video
        meta = None
    finally:
        if state_.meta_loading == entry.key:
            state_.meta_loading = None
    if state_.row_key != entry.key:
        return False  # the reviewer moved on while this read ran
    if meta is not None:
        state_.fps = meta.fps
        state_.num_frames = meta.num_frames
        state_.duration = frames_to_seconds(meta.num_frames, meta.fps)
    state_.meta_key = entry.key
    state_.reset_view()
    return True


async def _load_then_rebuild(state_: _EventState, entry: VideoEntry) -> None:
    """Read the open video's metadata, then rebuild the work area around it."""
    if await load_metadata(state_, entry):
        with contextlib.suppress(RuntimeError):  # the tab was closed meanwhile
            event_annotation_task.refresh()


def _video_pending(state_: _EventState) -> ui.element:
    """A spinner where the video goes, while its metadata is read; returns the box."""
    height = state_.video_height
    with ui.row().classes("w-full justify-center"):
        box = pending.media_box(round(height * 16 / 9), height).style("max-width:100%")
        pending.pending(box, size="xl")
    return box


def _ensure_active_category(state_: _EventState) -> None:
    """Keep ``active_category`` valid: default to the first category, drop it if it's gone."""
    names = [c.name for c in state.store.categories()]
    if state_.active_category not in names:
        state_.active_category = names[0] if names else None


def _events_by_track(row_key: str) -> dict[str, list[EventRecord]]:
    """Group a video's stored events by track name (order preserved by the store)."""
    grouped: dict[str, list[EventRecord]] = {}
    for event in state.store.events_for(row_key):
        grouped.setdefault(event.track, []).append(event)
    return grouped


# ── build ────────────────────────────────────────────────────────────────────


@client_refreshable
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
    if needs_metadata(state_, entry):
        box = _video_pending(state_)
        schedule(box, lambda: _load_then_rebuild(state_, entry))
        return
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
        _category_bar(state_)
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
        entry = entries[state_.index]
        for value in _layout_badges(entry):
            ui.badge(value).props("outline")
        for text, hint in _label_badges(entry):
            badge = ui.badge(text, color=theme.LABEL_COLOR)
            if hint:
                badge.tooltip(hint)
        ui.button(icon="chevron_right", on_click=lambda: _step_video(state_, 1)).props(
            "flat round"
        ).tooltip("Next video")

        ui.element("div").classes("flex-grow")

        with ui.button("Export", icon="download").props("flat dense"), ui.menu():
            for scope, scope_label in (("video", "This video"), ("session", "All session")):
                for fmt, fmt_label in _EXPORT_FORMATS.items():
                    stem = state_.video_id if scope == "video" else "session"
                    download.export_menu_item(
                        f"{scope_label} — {fmt_label}",
                        filename=download.sanitize_filename(
                            f"annie_events_{stem or 'video'}",
                            extension=fmt,
                            default_stem="annie_events",
                        ),
                        url=f"/annie/export/events/{context.client.id}/{scope}/{fmt}",
                        extension=fmt,
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


@client_refreshable
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
        _show_position(state_)
        if _follow_playhead(state_):
            return  # the window paged: the redraw positions the line and the band itself
        _sync_playhead(state_, svg_id)
        # While a start is armed, grow the amber band's right edge with the playhead — a cheap
        # JS resize, no rebuild, so it tracks playback smoothly.
        if state_.pending_start is not None:
            _sync_pending_band(state_, svg_id)

    only_while_shown(ui.timer(_POLL_SECONDS, _tick), "annotator")  # no polling while hidden


def _jump_fields(state_: _EventState, vid_id: str) -> None:
    """The live position readout: frame / seconds / ``mm:ss.ff``, also typed to jump.

    The three fields always show where the video is — during playback, frame stepping, timeline
    clicks — and stay consistent with each other. Typing a value in any one and pressing Enter
    (or leaving the field) seeks there and rewrites the other two. While a field is focused the
    readout holds still so it never overwrites what is being typed.
    """
    head = state_.playhead_frame
    with ui.row().classes("w-full items-center justify-center gap-3 wrap"):
        ui.label("Position").classes("text-xs").style(f"color:{theme.NEUTRAL}")
        frame_in = ui.number("frame", min=0, precision=0).props("dense").classes("w-28")
        sec_in = ui.number("seconds", min=0).props("dense").classes("w-28")
        tc_in = ui.input("mm:ss.ff").props("dense").classes("w-28")
        for field_ in (frame_in, sec_in, tc_in):
            field_.tooltip("Current position — type a value and press Enter to jump there")

    fields = _PositionFields(frame_in, sec_in, tc_in)
    state_.position = fields
    fields.show(head, state_.fps)

    for name, field_ in (("frame", frame_in), ("seconds", sec_in), ("timecode", tc_in)):
        field_.on("focus", lambda: setattr(fields, "editing", True))
        field_.on("blur", lambda n=name: _commit_position(state_, vid_id, n))
        field_.on("keydown.enter", lambda n=name: _commit_position(state_, vid_id, n))


def _commit_position(state_: _EventState, vid_id: str, which: str) -> None:
    """Jump to the value typed in one position field — but only if the user changed it.

    Args:
        state_: The task state (holds the position fields).
        vid_id: The video element's DOM id.
        which: ``"frame"``, ``"seconds"`` or ``"timecode"`` — the field being committed.
    """
    fields = state_.position
    if fields is None:
        return
    fields.editing = False
    shown_frame, shown_sec, shown_tc = fields.shown
    if which == "frame":
        if int(fields.frame_in.value or 0) == shown_frame:
            return
        frame = int(fields.frame_in.value or 0)
    elif which == "seconds":
        if float(fields.sec_in.value or 0.0) == shown_sec:
            return
        frame = seconds_to_frame(float(fields.sec_in.value or 0.0), state_.fps)
    else:
        if (fields.tc_in.value or "") == shown_tc:
            return
        frame = seconds_to_frame(_parse_timecode(fields.tc_in.value), state_.fps)
    _seek(state_, vid_id, frame)  # clamps, seeks, and rewrites all three fields


def _show_position(state_: _EventState) -> None:
    """Refresh the position readout from the playhead, unless the user is typing in it."""
    fields = state_.position
    if fields is None or fields.editing:
        return
    fields.show(state_.playhead_frame, state_.fps)


def _category_color(ordinal: int, override: str | None) -> str:
    """The lane colour for a category: its override, else the palette by ordinal."""
    return override or theme.event_track_color(ordinal)


@client_refreshable
def _category_bar(state_: _EventState) -> None:
    """The Category block: participants on the left half, event categories on the right.

    Both halves share one Manage dialog. On a narrow screen the halves stack.
    """
    with ui.row().classes("w-full items-start gap-4 wrap"):
        with ui.row().classes("flex-1 min-w-[18rem] items-center gap-2 wrap"):
            _participant_chips(state_)
        with ui.row().classes("flex-1 min-w-[18rem] items-center gap-2 wrap"):
            _event_type_chips(state_)


#: The fill of an active chip that has no colour of its own (None, or an uncoloured event
#: category): light, so its dark text stays readable.
_PLAIN_CHIP_FILL = "#dde1e6"


def _chip(
    text: str, colour: str | None, *, active: bool, on_click: Callable[[], None]
) -> ui.button:
    """A category chip: filled with its colour when active, outlined in it otherwise.

    A chip without a colour is outlined in grey and filled light grey when active.
    """
    btn = (
        ui.button(text, on_click=on_click)
        .props("unelevated" if active else "outline")
        .props("dense no-caps")
    )
    if active:
        btn.style(f"background:{colour or _PLAIN_CHIP_FILL} !important;color:#1a1a1a !important")
    else:
        line = colour or theme.NEUTRAL
        btn.style(f"color:{line} !important;border-color:{line} !important")
    return btn


def _manage_button(on_click: Callable[[], None], tooltip: str) -> None:
    """The ⋯ button at the end of a half of the bar, opening that half's dialog."""
    ui.button(icon="more_horiz", on_click=on_click).props("flat dense round").tooltip(tooltip)


def _participant_chips(state_: _EventState) -> None:
    """The participant chips (click to make active, number badge) plus a Manage button.

    The active participant — the lane a newly-captured event lands on — is highlighted.
    When the dataset has no participants yet, a single "Add participant" prompt stands in.
    """
    categories = state.store.categories()
    ui.label("Participants").classes("text-sm font-medium")
    if not categories:
        ui.button(
            "＋ Add participant", icon="group_add", on_click=lambda: _open_participants(state_)
        ).props("flat dense").tooltip(
            "Define the participants for this dataset (e.g. Mother, Baby)"
        )
        return
    for i, cat in enumerate(categories):
        # "(1) Participant 1": the parenthesised number is the quick-switch key, kept
        # visually apart from the name so it never reads as part of it.
        _chip(
            f"({i + 1}) {cat.name}",
            _category_color(cat.ordinal, cat.color),
            active=cat.name == state_.active_category,
            on_click=lambda n=cat.name: _set_active_category(state_, n),
        )
    _manage_button(lambda: _open_participants(state_), "Manage participants")


def _event_type_chips(state_: _EventState) -> None:
    """The event-category chips: None first, then each category with its Shift+digit badge.

    With a category active, a captured event gets its name, colour and default attributes,
    so nothing needs typing.
    """
    types = state.store.event_types()
    ui.label("Event categories").classes("text-sm font-medium")
    _chip(
        "(⇧0) None",
        None,
        active=state_.active_event_type is None,
        on_click=lambda: _set_active_event_type(state_, None),
    ).tooltip("Capture plain events, with no event category")
    for i, kind in enumerate(types):
        badge = f"(⇧{i + 1}) " if i < 9 else ""  # noqa: PLR2004 - Shift+1..9
        _chip(
            f"{badge}{kind.name}",
            kind.color,
            active=kind.name == state_.active_event_type,
            on_click=lambda n=kind.name: _set_active_event_type(state_, n),
        )
    _manage_button(
        lambda: _open_event_types(state_),
        "Manage event categories (e.g. Smile: a name, colour and attributes)",
    )


def _set_active_category(state_: _EventState, name: str) -> None:
    """Make a category active (where the next captured event lands) and refresh the bar."""
    state_.active_category = name
    _category_bar.refresh()


def _set_active_by_ordinal(state_: _EventState, ordinal: int) -> None:
    """Set the active category by its 0-based lane position (backs the number keys)."""
    categories = state.store.categories()
    if 0 <= ordinal < len(categories):
        _set_active_category(state_, categories[ordinal].name)


def _set_active_event_type(state_: _EventState, name: str | None) -> None:
    """Choose the event category new events are made with (``None`` = plain events)."""
    state_.active_event_type = name
    _category_bar.refresh()


def _set_event_type_by_ordinal(state_: _EventState, ordinal: int) -> None:
    """Choose the event category by its 0-based position; ``-1`` is None (backs Shift+digit)."""
    if ordinal < 0:
        _set_active_event_type(state_, None)
        return
    types = state.store.event_types()
    if ordinal < len(types):
        _set_active_event_type(state_, types[ordinal].name)


def _ensure_active_event_type(state_: _EventState) -> None:
    """Drop the active event category if it no longer exists (back to None)."""
    if state_.active_event_type not in {t.name for t in state.store.event_types()}:
        state_.active_event_type = None


def _types_by_name() -> dict[str, EventType]:
    """The dataset's event categories, keyed by name (for effective colour and attributes)."""
    return {kind.name: kind for kind in state.store.event_types()}


def _manager_body(kind: str) -> Callable[[_EventState], None]:
    """The dialog body for a manager: ``"participants"`` or ``"event_types"``."""
    return _participant_manager if kind == "participants" else _event_type_manager


def _open_manager(state_: _EventState, kind: str) -> None:
    """Open a manager dialog on the next loop turn (outside the click's slot)."""
    ui.timer(0.0, lambda: _manager_dialog(state_, _manager_body(kind)), once=True)


def _open_participants(state_: _EventState) -> None:
    """Open the participants dialog (add / rename / delete / reorder / colour)."""
    _open_manager(state_, "participants")


def _open_event_types(state_: _EventState) -> None:
    """Open the event categories dialog (add / rename / attributes / reorder / colour)."""
    _open_manager(state_, "event_types")


async def _manager_dialog(state_: _EventState, body: Callable[[_EventState], None]) -> None:
    """A dialog around one manager: participants or event categories.

    It is the only place a name is typed: everywhere else a name is picked from the list,
    so names stay consistent (no "Mother" vs "mom" drift). Renaming is safe, since events
    follow the new name; deleting warns when events still use it.

    Args:
        state_: The client's event state.
        body: :func:`_participant_manager` or :func:`_event_type_manager`.
    """
    with ui.dialog() as dialog, ui.card().classes("w-[34rem] max-w-full gap-1"):
        body(state_)
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button("Done", on_click=lambda: dialog.submit(None)).props("flat")

    await dialog
    # Reflect any changes (names, order, colours, active) on the main surface.
    _ensure_active_category(state_)
    _ensure_active_event_type(state_)
    _refresh_work_area(state_)


def _participant_manager(state_: _EventState) -> None:
    """The participants dialog: one row per participant lane, and an Add field."""
    ui.label("Participants").classes("text-lg font-medium")
    ui.label(
        "Shared by every video in this dataset; one timeline lane each, keys 1–9 choose one. "
        "Rename is safe — existing events follow the new name."
    ).classes("text-xs").style(f"color:{theme.NEUTRAL}")
    rows = ui.column().classes("w-full gap-1")

    def rebuild_rows() -> None:
        rows.clear()
        categories = state.store.categories()
        with rows:
            for i, cat in enumerate(categories):
                _manager_row(
                    state_, cat.name, cat.ordinal, cat.color, i, len(categories), rebuild_rows
                )
            if not categories:
                ui.label("No participants yet — add the first below.").classes("text-sm").style(
                    f"color:{theme.NEUTRAL}"
                )

    rebuild_rows()

    with ui.row().classes("w-full items-center gap-2 mt-2 no-wrap"):
        new_in = (
            ui.input(placeholder="New participant (blank = next Participant N)")
            .props("dense")
            .classes("flex-grow")
        )

        def add_new() -> None:
            # A blank name falls back to the next free "Participant N", so "just click Add"
            # still yields a clean, consistent label with no typing (and no collisions).
            existing = [c.name for c in state.store.categories()]
            name = (new_in.value or "").strip() or next_default_category_name(existing)
            state.store.add_category(name)
            new_in.set_value("")
            _ensure_active_category(state_)
            rebuild_rows()

        new_in.on("keydown.enter", lambda: add_new())
        ui.button("Add", icon="add", on_click=add_new).props("unelevated dense")


def _event_type_manager(state_: _EventState) -> None:
    """The event categories dialog: one row per event category, and an Add field."""
    ui.label("Event categories").classes("text-lg font-medium")
    ui.label(
        "Shared by every video in this dataset. A new event takes the chosen category's name, "
        "colour and attributes; Shift+1–9 choose one, Shift+0 none."
    ).classes("text-xs").style(f"color:{theme.NEUTRAL}")
    rows = ui.column().classes("w-full gap-1")
    # Which rows have their attributes open, kept across rebuilds of the list.
    open_attributes: set[str] = set()

    def rebuild_rows() -> None:
        rows.clear()
        types = state.store.event_types()
        with rows:
            for i, kind in enumerate(types):
                _event_type_row(state_, kind, i, len(types), open_attributes, rebuild_rows)
            if not types:
                ui.label("No event categories yet — add one below (e.g. Smile).").classes(
                    "text-sm"
                ).style(f"color:{theme.NEUTRAL}")

    rebuild_rows()

    with ui.row().classes("w-full items-center gap-2 mt-2 no-wrap"):
        new_in = (
            ui.input(placeholder="New event category (blank = next Event N)")
            .props("dense")
            .classes("flex-grow")
        )

        def add_new() -> None:
            types = state.store.event_types()
            existing = [t.name for t in types]
            name = (new_in.value or "").strip() or next_default_category_name(
                existing, prefix="Event"
            )
            # Each new category starts with the next palette colour, so the chips differ.
            colour = _EVENT_COLORS[len(types) % len(_EVENT_COLORS)]
            state.store.add_event_type(name, color=colour)
            new_in.set_value("")
            rebuild_rows()

        new_in.on("keydown.enter", lambda: add_new())
        ui.button("Add", icon="add", on_click=add_new).props("unelevated dense")


def _manager_row(  # noqa: PLR0913 - a row needs its category + position context
    state_: _EventState,
    name: str,
    ordinal: int,
    color: str | None,
    index: int,
    total: int,
    rebuild: Callable[[], None],
) -> None:
    """One editable participant row in the manager: reorder, colour, rename, delete."""
    colour = _category_color(ordinal, color)
    with ui.row().classes("w-full items-center gap-1 no-wrap"):
        _colour_button(
            colour,
            lambda picked: _set_category_colour(name, picked, rebuild),
            tooltip="Change this participant's colour",
        )
        name_in = ui.input(value=name).props("dense").classes("flex-grow")

        def do_rename() -> None:
            new = (name_in.value or "").strip()
            if new and new != name:
                state.store.rename_category(name, new)
                if state_.active_category == name:
                    state_.active_category = new
                rebuild()

        name_in.on("blur", do_rename)
        _move_buttons(index, total, lambda delta: _move_category(name, delta, rebuild))
        ui.button(icon="delete", on_click=lambda: _delete_category(state_, name, rebuild)).props(
            "flat dense round"
        ).tooltip("Delete this participant")


def _event_type_row(  # noqa: PLR0913 - a row needs its category + position context
    state_: _EventState,
    kind: EventType,
    index: int,
    total: int,
    open_attributes: set[str],
    rebuild: Callable[[], None],
) -> None:
    """One editable event-category row: colour, rename, attributes, reorder, delete.

    The attributes button opens the category's default attributes below the row.
    """
    name = kind.name
    with ui.row().classes("w-full items-center gap-1 no-wrap"):
        _colour_button(
            kind.color or theme.NEUTRAL,
            lambda picked: _set_type_colour(name, picked, rebuild),
            tooltip="Change this event category's colour (grey = the participant's colour)",
            reset_label="Use the participant's colour",
        )
        name_in = ui.input(value=name).props("dense").classes("flex-grow")

        def do_rename() -> None:
            new = (name_in.value or "").strip()
            if new and new != name:
                state.store.rename_event_type(name, new)
                if state_.active_event_type == name:
                    state_.active_event_type = new
                if name in open_attributes:
                    open_attributes.discard(name)
                    open_attributes.add(new)
                rebuild()

        name_in.on("blur", do_rename)

        def toggle_attributes() -> None:
            open_attributes.symmetric_difference_update({name})
            rebuild()

        count = len(kind.attributes)
        ui.button(str(count) if count else "", icon="tune", on_click=toggle_attributes).props(
            "flat dense" + ("" if count else " round")
        ).tooltip("Default attributes")
        _move_buttons(index, total, lambda delta: _move_event_type(name, delta, rebuild))
        ui.button(icon="delete", on_click=lambda: _delete_event_type(state_, name, rebuild)).props(
            "flat dense round"
        ).tooltip("Delete this event category (its events keep how they look)")
    if name in open_attributes:
        _type_attribute_editor(kind, rebuild)


def _colour_button(
    colour: str,
    on_pick: Callable[[str | None], None],
    *,
    tooltip: str,
    reset_label: str | None = None,
) -> ui.element:
    """A colour dot that opens a colour picker: a spectrum and hue slider, plus the palette.

    Args:
        colour: The colour the dot shows now (and the picker starts on).
        on_pick: Called with the picked hex colour, or ``None`` for the reset.
        tooltip: The dot's hover text.
        reset_label: When given, the picker gets a button with this text that picks ``None``.

    Returns:
        The dot.
    """
    # A plain div, not a q-btn: Quasar's button padding warps a tiny round button into an
    # ellipse, while a fixed-size div with border-radius:50% is a true circle.
    dot = ui.element("div").style(
        f"background:{colour};width:22px;height:22px;min-width:22px;min-height:22px;"
        "border-radius:50%;border:2px solid #00000055;cursor:pointer;box-sizing:border-box"
    )
    dot.tooltip(tooltip)
    with dot:
        # A menu attached to the dot: it opens on click, and a pick fires on release.
        picker = ui.color_picker(on_pick=lambda e: on_pick(e.color)).set_color(colour)
        picker.q_color.props["palette"] = list(_EVENT_COLORS)
        if reset_label is not None:
            with picker:

                def reset() -> None:
                    picker.close()
                    on_pick(None)

                ui.button(reset_label, icon="format_color_reset", on_click=reset).props(
                    "flat dense no-caps"
                ).classes("w-full")
    return dot


def _move_buttons(index: int, total: int, move: Callable[[int], None]) -> None:
    """The ↑ ↓ buttons of a manager row, disabled at the ends of the list."""
    up = ui.button(icon="arrow_upward", on_click=lambda: move(-1)).props("flat dense round")
    if index == 0:
        up.props(add="disable")
    down = ui.button(icon="arrow_downward", on_click=lambda: move(1)).props("flat dense round")
    if index == total - 1:
        down.props(add="disable")


def _type_attribute_editor(kind: EventType, rebuild: Callable[[], None]) -> None:
    """An event category's default attributes: key/value rows and an Add button."""
    with ui.column().classes("w-full gap-1 pl-8"):
        for key, value in kind.attributes.items():
            _type_attribute_row(kind.name, key, value, rebuild)

        def add() -> None:
            attrs = dict(_type_attributes(kind.name))
            attrs[_free_key(attrs)] = ""
            state.store.set_event_type_attributes(kind.name, attrs)
            rebuild()

        ui.button("Add attribute", icon="add", on_click=add).props("flat dense")


def _type_attribute_row(name: str, key: str, value: str, rebuild: Callable[[], None]) -> None:
    """One key/value row of an event category's default attributes."""
    with ui.row().classes("w-full items-center gap-1 no-wrap"):
        key_in = ui.input(placeholder="key", value=key).props("dense").classes("flex-grow")
        val_in = ui.input(placeholder="value", value=value).props("dense").classes("flex-grow")

        def save() -> None:
            new_key = (key_in.value or "").strip()
            new_value = val_in.value or ""
            if (new_key, new_value) == (key, value):
                return
            attrs = dict(_type_attributes(name))
            attrs.pop(key, None)
            if new_key:
                attrs[new_key] = new_value
            state.store.set_event_type_attributes(name, attrs)
            rebuild()

        def remove() -> None:
            attrs = dict(_type_attributes(name))
            attrs.pop(key, None)
            state.store.set_event_type_attributes(name, attrs)
            rebuild()

        key_in.on("blur", save)
        val_in.on("blur", save)
        ui.button(icon="close", on_click=remove).props("flat dense round")


def _type_attributes(name: str) -> dict[str, str]:
    """An event category's default attributes, read fresh from the store."""
    kind = _types_by_name().get(name)
    return dict(kind.attributes) if kind is not None else {}


def _free_key(attrs: dict[str, str]) -> str:
    """The first of ``key``, ``key2``, ``key3``… not yet in ``attrs`` (for a new attribute)."""
    key, i = "key", 1
    while key in attrs:
        i += 1
        key = f"key{i}"
    return key


def _set_category_colour(name: str, colour: str | None, rebuild: Callable[[], None]) -> None:
    """Store a participant's picked colour (``None`` = its palette colour) and redraw."""
    state.store.set_category_color(name, colour)
    rebuild()


def _set_type_colour(name: str, colour: str | None, rebuild: Callable[[], None]) -> None:
    """Store an event category's picked colour (``None`` = the participant's) and redraw."""
    state.store.set_event_type_color(name, colour)
    rebuild()


def _move_category(name: str, delta: int, rebuild: Callable[[], None]) -> None:
    """Move a category up (-1) or down (+1) in the lane order."""
    names = [c.name for c in state.store.categories()]
    i = names.index(name)
    j = i + delta
    if 0 <= j < len(names):
        names[i], names[j] = names[j], names[i]
        state.store.reorder_categories(names)
        rebuild()


def _move_event_type(name: str, delta: int, rebuild: Callable[[], None]) -> None:
    """Move an event category up (-1) or down (+1), which changes its Shift+digit key."""
    names = [t.name for t in state.store.event_types()]
    i = names.index(name)
    j = i + delta
    if 0 <= j < len(names):
        names[i], names[j] = names[j], names[i]
        state.store.reorder_event_types(names)
        rebuild()


def _delete_category(state_: _EventState, name: str, rebuild: Callable[[], None]) -> None:
    """Delete a category; warn (and require a second click) when it still holds events."""
    count = state.store.category_event_count(name)
    if count == 0:
        state.store.delete_category(name)
        _ensure_active_category(state_)
        rebuild()
        return

    async def confirm() -> None:
        with ui.dialog() as dialog, ui.card().classes("gap-2"):
            ui.label(f"Delete “{name}” and its {count} event(s)?").classes("text-sm font-medium")
            ui.label(
                "This cannot be undone. Rename instead if you only want to fix the name."
            ).classes("text-xs").style(f"color:{theme.NEUTRAL}")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=lambda: dialog.submit(False)).props("flat")
                ui.button("Delete", on_click=lambda: dialog.submit(True)).props(
                    "unelevated color=negative"
                )
        if await dialog:
            state.store.delete_category(name)
            _ensure_active_category(state_)
            rebuild()

    ui.timer(0.0, confirm, once=True)


def _delete_event_type(state_: _EventState, name: str, rebuild: Callable[[], None]) -> None:
    """Delete an event category; ask first when events still use it.

    The events themselves stay: they keep the name, colour and attributes they had, and
    are just no longer linked (see :meth:`ReviewStore.delete_event_type`).
    """

    def delete() -> None:
        state.store.delete_event_type(name)
        _ensure_active_event_type(state_)
        rebuild()

    count = state.store.event_type_event_count(name)
    if count == 0:
        delete()
        return

    async def confirm() -> None:
        with ui.dialog() as dialog, ui.card().classes("gap-2"):
            ui.label(f"Delete the event category “{name}”?").classes("text-sm font-medium")
            ui.label(
                f"Its {count} event(s) stay and keep their name, colour and attributes; "
                "they just no longer follow this category."
            ).classes("text-xs").style(f"color:{theme.NEUTRAL}")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=lambda: dialog.submit(False)).props("flat")
                ui.button("Delete", on_click=lambda: dialog.submit(True)).props(
                    "unelevated color=negative"
                )
        if await dialog:
            delete()

    ui.timer(0.0, confirm, once=True)


def _timeline_block(state_: _EventState) -> None:
    """The timeline: controls, the SVG surface, and the pan scrollbar.

    Each part is its own refreshable so a view change (zoom, pan, page-flip) redraws only the
    timeline — never the task, whose ``<video>`` would be recreated and reset to frame 0.
    """
    _timeline_controls(state_)
    _timeline_surface(state_)
    _timeline_scrollbar(state_)


@client_refreshable
def _timeline_controls(state_: _EventState) -> None:
    """Zoom buttons, the Follow toggle, and the window's range readout."""
    with ui.row().classes("w-full items-center gap-2"):
        ui.label("Timeline").classes("text-sm font-medium")
        ui.button(icon="zoom_out", on_click=lambda: _zoom(state_, 1 / 1.5)).props(
            "flat dense round"
        ).tooltip("Zoom out")
        ui.button(icon="zoom_in", on_click=lambda: _zoom(state_, 1.5)).props(
            "flat dense round"
        ).tooltip("Zoom in")
        if state_.view.zoomed_in:
            follow = ui.button(
                "Follow",
                icon="my_location",
                on_click=lambda: _set_follow(state_, not state_.follow),
            ).props("dense " + ("unelevated" if state_.follow else "outline"))
            follow.tooltip("Keep the zoomed window on the playhead during playback")
            ui.label(_range_label(state_)).classes("text-xs").style(f"color:{theme.NEUTRAL}")


def _range_label(state_: _EventState) -> str:
    """Describe the visible window, e.g. ``×4 · 12.4–19.6 s of 42.0 s``."""
    lo, hi = state_.view.window()
    return (
        f"×{state_.view.zoom:.1f} · {frames_to_seconds(lo, state_.fps):.1f}–"
        f"{frames_to_seconds(hi, state_.fps):.1f} s of {state_.duration:.1f} s"
    )


@client_refreshable
def _timeline_surface(state_: _EventState) -> None:
    """The SVG timeline for the current window."""
    # Lanes come from the DB-scoped categories (one per participant), not per-video tracks, so
    # every video of this dataset shows the same lanes in the same order — including empty ones.
    grouped = _events_by_track(state_.row_key or "")
    types = _types_by_name()
    tracks = [
        timeline.TimelineTrack(
            category.name,
            [
                timeline.TimelineEvent(
                    e.event_id,
                    e.start_frame,
                    e.end_frame,
                    e.label,
                    selected=e.event_id == state_.selected_event,
                    # Its own colour, else its event category's; the lane's is the fallback.
                    color=effective_color(e, types),
                )
                for e in grouped.get(category.name, [])
            ],
            color=category.color,
        )
        for category in state.store.categories()
    ]
    svg_id = f"annie-timeline-{context.client.id}"
    view_start, view_end = state_.view.window()
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
    ui.run_javascript(
        timeline.gesture_script(svg_id, _gesture_event_name(), pannable=state_.view.zoomed_in)
    )
    # Put the red playhead line where the current frame sits within the visible window.
    _sync_playhead(state_, svg_id)


@client_refreshable
def _timeline_scrollbar(state_: _EventState) -> None:
    """The pan scrollbar, present only while zoomed in.

    Rebuilt only when zoom changes; panning moves its thumb through JS so a drag is never
    interrupted by a DOM rebuild.
    """
    if not state_.view.zoomed_in:
        return
    scroll_id = f"annie-timeline-scroll-{context.client.id}"
    left, width = state_.view.thumb()
    ui.html(timeline.scrollbar_html(scroll_id, left, width)).classes("w-full").style("width:100%")
    ui.run_javascript(timeline.scrollbar_script(scroll_id, _gesture_event_name()))


def _refresh_timeline(state_: _EventState, *, rebuild_scrollbar: bool = False) -> None:
    """Redraw the timeline for the current window without touching the video."""
    _timeline_surface.refresh()
    _timeline_controls.refresh()
    if rebuild_scrollbar:
        _timeline_scrollbar.refresh()
    else:
        left, width = state_.view.thumb()
        videoclock.set_scrollbar(f"annie-timeline-scroll-{context.client.id}", left, width)


def _follow_playhead(state_: _EventState) -> bool:
    """Page the window forward when playback leaves it (Follow on). Returns whether it paged."""
    view = state_.view
    if not (view.zoomed_in and state_.follow) or view.contains(state_.playhead_frame):
        return False
    view.page_to(state_.playhead_frame)
    _refresh_timeline(state_)
    return True


def _set_follow(state_: _EventState, on: bool) -> None:
    """Switch Follow; turning it on snaps the window to the playhead."""
    state_.follow = on
    if on and not state_.view.contains(state_.playhead_frame):
        state_.view.page_to(state_.playhead_frame)
    _refresh_timeline(state_)


@client_refreshable
def _editor(state_: _EventState) -> None:
    """The selected event's editor: participant, category, start/end, colour + note, attributes.

    Stacked top to bottom in the side card. The event category is the event's name.
    """
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

        # Participant: picked from the list (never free text) to move the event between lanes.
        categories = state.store.categories()
        if categories:
            ui.select([c.name for c in categories], value=event.track, label="participant").props(
                "dense"
            ).classes("w-full").on_value_change(lambda e: _move_event_to_category(state_, e.value))

        # Event category: what the event is, and so its name. "" stands for None.
        types = _types_by_name()
        options = {"": "None", **{name: name for name in types}}
        if event.event_type and event.event_type not in options:
            options[event.event_type] = event.event_type
        ui.select(options, value=event.event_type or "", label="event category").props(
            "dense"
        ).classes("w-full").on_value_change(lambda e: _set_event_type(state_, e.value or None))

        # Start and end each get their own row: a frame field and a seconds field kept in
        # two-way sync, so either can be typed. Both commit through clamp_event, which keeps
        # start <= end and both inside the video.
        bounds = {"start": event.start_frame, "end": event.end_frame}

        def commit_bounds() -> None:
            start, end = clamp_event(bounds["start"], bounds["end"], state_.num_frames)
            _update(state_, start_frame=start, end_frame=end)

        _bound_row(state_, "Start", bounds, "start", commit_bounds)
        _bound_row(state_, "End", bounds, "end", commit_bounds)

        # Colour and note share a row: the dot shows the colour the event is drawn with.
        lane = next((c for c in categories if c.name == event.track), None)
        lane_colour = _category_color(lane.ordinal, lane.color) if lane else theme.NEUTRAL
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            _colour_button(
                effective_color(event, types, lane_colour) or lane_colour,
                lambda picked: _set_color(state_, picked),
                tooltip="This event's colour",
                reset_label="Use the category's colour",
            )
            note_in = ui.input("note", value=event.note).props("dense").classes("flex-grow")
            note_in.on("blur", lambda: _update(state_, note=note_in.value))

        kind = types.get(event.event_type or "")
        _attribute_editor(state_, event, kind.attributes if kind is not None else {})


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


def _set_color(state_: _EventState, color: str | None) -> None:
    """Set (or clear) the selected event's colour override and redraw the work area."""
    _update(state_, color=color)


def _attribute_editor(state_: _EventState, event: EventRecord, defaults: dict[str, str]) -> None:
    """The add/remove key-value attribute rows for one event.

    Shows the **effective** attributes: the event category's ``defaults`` with the event's
    own values over them. Editing an inherited value stores it on the event (an override);
    removing an override brings the default back.
    """
    ui.label("Attributes").classes("text-xs").style(f"color:{theme.NEUTRAL}")
    with ui.column().classes("w-full gap-1"):
        for key, value in {**defaults, **event.attributes}.items():
            _attribute_row(
                state_, key, value, inherited=key in defaults, own=key in event.attributes
            )
    ui.button("Add attribute", icon="add", on_click=lambda: _add_attribute(state_)).props(
        "flat dense"
    )


def _attribute_row(
    state_: _EventState, key: str, value: str, *, inherited: bool = False, own: bool = True
) -> None:
    """One editable key/value attribute row; edits rewrite the event's own attribute map.

    ``key`` is captured per row (each row is its own function scope), so renaming or removing
    a key acts on the right entry without the default-argument loop-capture trick. An
    attribute the event only inherits has no remove button, since the default stays anyway.
    """
    with ui.row().classes("w-full items-center gap-1 no-wrap"):
        key_in = ui.input(placeholder="key", value=key).props("dense").classes("flex-grow")
        val_in = ui.input(placeholder="value", value=value).props("dense").classes("flex-grow")
        if inherited and not own:
            val_in.props('input-class="text-italic text-grey-7"').tooltip(
                "From the event category; type to change it for this event"
            )

        def _save() -> None:
            if ((key_in.value or "").strip(), val_in.value or "") == (key, value):
                return  # a blur without a change; never turn a default into an override
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
        if own:
            ui.button(icon="close", on_click=_remove).props("flat dense round").tooltip(
                "Back to the event category's value" if inherited else "Remove"
            )


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
    event = state.store.get_event(state_.selected_event or "")
    shown = effective_attributes(event, _types_by_name()) if event is not None else attrs
    attrs[_free_key(shown)] = ""
    _update(state_, attributes=attrs)


def changed_fields(event: EventRecord, fields: dict[str, object]) -> dict[str, object]:
    """The subset of ``fields`` whose value differs from what ``event`` stores."""
    return {name: value for name, value in fields.items() if getattr(event, name) != value}


def _update(state_: _EventState, **fields: object) -> None:
    """Persist a change to the selected event and redraw the work area (not the video).

    Fields equal to the stored values are dropped; when nothing changes (e.g. a field left
    as it was), nothing is written and nothing is redrawn.
    """
    if state_.selected_event is None:
        return
    event = state.store.get_event(state_.selected_event)
    if event is None:
        return
    changes = changed_fields(event, fields)
    if not changes:
        return
    state.store.update_event(state_.selected_event, **changes)
    _refresh_work_area(state_)


def _refresh_work_area(state_: _EventState) -> None:
    """Redraw the editor, the category bar and the timeline, keeping the ``<video>``.

    Rebuilding the whole task would recreate the video element, which the browser resets
    to frame 0; these parts are everything an event edit can change.
    """
    _editor.refresh()
    _category_bar.refresh()
    _refresh_timeline(state_, rebuild_scrollbar=True)


def _set_event_type(state_: _EventState, name: str | None) -> None:
    """Link the selected event to an event category (``None`` unlinks it).

    The event category is the event's name, so its label follows: the category's name, or
    no label for a plain event.
    """
    event = state.store.get_event(state_.selected_event or "")
    if event is None or event.event_type == name:
        return
    _update(state_, event_type=name, label=name or "")


def _move_event_to_category(state_: _EventState, category: str) -> None:
    """Move the selected event onto another participant's lane (via its ``track``)."""
    _update(state_, track=category)


def _delete_event(state_: _EventState) -> None:
    """Delete the selected event and clear the editor."""
    if state_.selected_event is None:
        return
    state.store.delete_event(state_.selected_event)
    state_.selected_event = None
    _refresh_work_area(state_)


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
    """Change the timeline zoom around the playhead (or the window centre if it is off-screen).

    Only the timeline is redrawn; the video is untouched.
    """
    view = state_.view
    anchor = state_.playhead_frame if view.contains(state_.playhead_frame) else None
    view.zoom_by(factor, anchor)
    _refresh_timeline(state_, rebuild_scrollbar=True)


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
    """Close the armed event at the current frame and select it.

    The span runs from ``pending_start`` (or the current frame, if end is pressed without a
    prior start) to the current frame, clamped into the video. The new event lands on the
    **active category** (the participant the user is annotating) and is selected. With an
    **active event category** it is named after it and linked to it; otherwise it is a plain
    event with no label. If no category exists yet, the user is asked to add one first.
    """
    category = state_.active_category or _DEFAULT_TRACK
    if state_.active_category is None and not state.store.categories():
        ui.notify(
            "Add a participant category first (the bar above the timeline).", color=theme.WARNING
        )
        state_.pending_start = None
        _capture_controls.refresh()
        return
    vid_id = videoclock.element_id(context.client.id)
    _finalise_at(state_, category, await videoclock.current_time(vid_id))


def _finalise_at(state_: _EventState, category: str, seconds: float | None) -> None:
    """Close the armed event at ``seconds`` (the browser's video time) on ``category``."""
    frame = seconds_to_frame(float(seconds or 0.0), state_.fps)
    state_.playhead_frame = frame
    start = state_.pending_start if state_.pending_start is not None else frame
    lo, hi = clamp_event(start, frame, state_.num_frames)
    # With an event category chosen, the event is named after it and linked to it (so it
    # takes the category's colour and attributes); a plain event has no label.
    kind = _types_by_name().get(state_.active_event_type or "")
    record = state.store.add_event(
        state_.video_id,
        state_.row_key or "",
        category,
        lo,
        hi,
        label=kind.name if kind is not None else "",
        event_type=kind.name if kind is not None else None,
    )
    state_.pending_start = None
    state_.selected_event = record.event_id
    _capture_controls.refresh()
    _refresh_work_area(state_)


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
        _refresh_work_area(state_)  # the editor and highlight follow; the video keeps playing
        return
    if kind in ("pan", "wheel"):
        _pan(state_, detail)
        return
    # Any non-select gesture is a plain seek to the clicked position.
    frame = _fraction_to_frame(state_, float(detail.get("x", 0.0)))
    _seek(state_, vid_id, frame)


def _pan(state_: _EventState, detail: dict[str, Any]) -> None:
    """Pan the window from a scrollbar drag (``x``: thumb position) or a wheel (``dx``).

    Panning is the user steering the view, so it turns Follow off.
    """
    if detail.get("kind") == "pan":
        state_.view.set_start_fraction(float(detail.get("x", 0.0)))
    else:
        state_.view.pan_by_fraction(float(detail.get("dx", 0.0)))
    state_.follow = False
    _refresh_timeline(state_)


def _export_text(state_: _EventState, scope: str, fmt: str) -> str | bytes | None:
    """Render the current video's or the whole session's events, or ``None`` if there are none.

    JSON and CSV come back as text, XLSX as the workbook's bytes.
    """
    events = (
        state.store.events_for(state_.row_key or "")
        if scope == "video"
        else state.store.all_events()
    )
    if not events:
        return None
    fps_by_video = _fps_by_video(events, state_)
    meta_by_video = _meta_by_video(events)
    types = _types_by_name()
    if fmt == "json":
        return events_to_json_text(events, fps_by_video, meta_by_video, types)
    if fmt == "xlsx":
        return events_to_xlsx_bytes(events, fps_by_video, meta_by_video, types)
    return events_to_csv_text(events, fps_by_video, meta_by_video, types)


@app.get("/annie/export/events/{client_id}/{scope}/{fmt}")
def _export_route(client_id: str, scope: str, fmt: str) -> Response:
    """Serve an event export to the browser, which saves it via the Save dialog.

    ``204`` means there is nothing to export (or an unknown client/format), which the
    browser-side handler reports instead of writing an empty file.
    """
    state_ = _event_states.get(client_id)
    if state_ is None or scope not in ("video", "session") or fmt not in _EXPORT_FORMATS:
        return Response(status_code=204)
    content = _export_text(state_, scope, fmt)
    if content is None:
        return Response(status_code=204)
    return Response(content=content, media_type=download.MIME_TYPES[fmt])


# ── helpers ────────────────────────────────────────────────────────────────────


def _layout_badges(entry: VideoEntry) -> list[str]:
    """The path-derived field values of ``entry`` (nested layouts only), to show beside its id."""
    video = state.registry.video
    if video is None or video.layout is None:
        return []
    return [entry.labels[name] for name in video.layout.field_names() if name in entry.labels]


def _label_badges(entry: VideoEntry) -> list[tuple[str, str]]:
    """The loaded label tags of ``entry`` as ``(text, tooltip)``, like Browse shows.

    Path fields are left out (they are the outline badges) and empty values skipped. A long
    value is shortened in the badge; the tooltip then carries it in full, followed by the
    origin (which file, sheet and row it came from).
    """
    scan = state.scan
    video = state.registry.video
    path_fields = set(video.layout.field_names()) if video and video.layout else set()
    badges: list[tuple[str, str]] = []
    for column in scan.label_columns if scan is not None else []:
        value = entry.labels.get(column, "")
        if column in path_fields or not value:
            continue
        shown = short_name(value)
        hints = [value if shown != value else "", entry.label_origins.get(column, "")]
        badges.append((f"{column}: {shown}", " · ".join(h for h in hints if h)))
    return badges


def _meta_by_video(events: list[EventRecord]) -> dict[str, dict[str, str]]:
    """Per-video metadata for the export: each video's label values (incl. path-derived fields)."""
    scan = state.scan
    if scan is None:
        return {}
    by_id = scan.by_video_id
    return {
        video_id: dict(by_id[video_id].labels)
        for video_id in {e.video_id for e in events}
        if video_id in by_id and by_id[video_id].labels
    }


def _fps_by_video(events: list[EventRecord], state_: _EventState) -> dict[str, float]:
    """Best-effort fps per video id for the export, from the scan manifest.

    The open video's fps is known on ``_EventState``; other videos in a session export are
    looked up from the manifest and probed once. A video whose fps cannot be read exports
    with frame numbers only (seconds ``0``), which the export handles.
    """
    fps: dict[str, float] = {}
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
    """Map an ``[0, 1]`` x fraction of the *visible window* to an absolute frame index."""
    return state_.view.frame_at(fraction)


def _seek(state_: _EventState, vid_id: str, frame: int) -> int:
    """Seek the video to ``frame``, record it as the playhead, and move the timeline line.

    Returns the clamped frame actually sought. An explicit seek to a frame outside the zoomed
    window centres the window on it (whatever Follow says — the user asked to go there);
    otherwise only the cheap red-line move runs. Neither rebuilds the video.
    """
    frame = max(0, min(frame, max(0, state_.num_frames - 1)))
    state_.playhead_frame = frame
    videoclock.seek_frame(vid_id, frame, state_.fps)
    _show_position(state_)
    if state_.view.zoomed_in and not state_.view.contains(frame):
        state_.view.center_on(frame)
        _refresh_timeline(state_)
    else:
        _sync_playhead(state_, f"annie-timeline-{context.client.id}")
    return frame


def _sync_playhead(state_: _EventState, svg_id: str) -> None:
    """Move the red line to the playhead, or hide it when the playhead is off-screen."""
    videoclock.set_playhead_x(svg_id, state_.view.fraction_of(state_.playhead_frame))


def _sync_pending_band(state_: _EventState, svg_id: str) -> None:
    """Resize the amber capture band from the armed start to the live playhead (no rebuild).

    The band is clipped at the window edges (a region, unlike the line, is still partly
    visible). When nothing is armed it collapses to zero width so cancelling hides it.
    """
    if state_.pending_start is None:
        videoclock.set_pending_band(svg_id, 0.0, 0.0)
        return
    view = state_.view
    videoclock.set_pending_band(
        svg_id,
        view.fraction_clamped(state_.pending_start),
        view.fraction_clamped(state_.playhead_frame),
    )


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


def _keyboard(state_: _EventState) -> None:
    """Bind transport + marking keys; a fresh handler each rebuild (like segment review)."""
    vid_id = videoclock.element_id(context.client.id)
    only_while_shown(ui.keyboard(on_key=lambda event: _on_key(state_, vid_id, event)), "annotator")


def _on_key(state_: _EventState, vid_id: str, event: KeyEventArguments) -> None:
    """Handle one key of the Event-annotation work area (transport, marking, choosing)."""
    if not event.action.keydown or event.action.repeat:
        return
    key = event.key
    if event.modifiers.shift and key.code.startswith("Digit"):
        # Shift+1..9 choose an event category, Shift+0 none. Matched on the physical key,
        # since Shift+1 types "!" on a US keyboard and something else on other layouts.
        _set_event_type_by_ordinal(state_, int(key.code.removeprefix("Digit")) - 1)
    elif key == " ":
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
    elif str(key) in "123456789":
        # Choose the active participant by lane position (1 = top). Ignored while a text
        # field is focused, since the input consumes the keydown before it reaches here.
        _set_active_by_ordinal(state_, int(str(key)) - 1)
