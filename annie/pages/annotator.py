"""Annotator tab — the supervision surface, one task at a time.

The Annotator is where the reviewer *inputs* supervision (Browse is a read-only
viewer). Which tasks it offers is driven by the sources present on the Dataset tab
(see :func:`annie.dataset.sources.task_readiness`): a **task switch** at the top lists
only the *ready* tasks, and switching one rebuilds the work area below. Every task
persists to the same session review database. The tasks are:

* **Protagonist review** (needs a protagonist CSV) — the original per-video track
  correction, described below;
* **Curation** (needs only videos) — like/dislike plus a note per queued video, the
  supervision that used to live on Browse rows;
* **Segment review** (needs a segmentation CSV) — accept/drop over the clips of long
  videos, one clip at a time: its passthrough tags on top, a lazy-embed ORIGINAL video,
  then one row per competing start/end band (aligned span facts, span frames, and an
  on-demand clip cut of that exact span). A top toolbar carries progress, the shortcut
  legend, and Export; Accept / Undecided / Drop and the step controls close the card, and
  a decision paints it with a coloured border and a lighter wash of the same hue.

The tab is greyed out until at least one video is queued *or* a queue-free task (segment
review) is ready. The protagonist task shows the queued videos in bordered rows; for
each video:

* an **ORIGINAL** placeholder embeds and plays the clip on click (like Browse), so it is
  obvious a video starts — while the **five-frame strip** shows the current protagonist
  track in green;
* picking a different track in the **Protagonist** selector re-renders the strip
  immediately and **saves the choice to this session's review database** — there is
  no Save button; selection *is* the save. The source ``_manual`` CSV is *not*
  touched here — that is what the **Export corrected CSV** button (in the jump toolbar)
  is for;
* the option matching the value in the source CSV is marked ``(default)`` so it is
  obvious which track the heuristic chose;
* a **render** box burns the full clip with the chosen track green so the choice can
  be double-checked across the whole video.
* the row's **X** button drops it from the queue once reviewed, so an emptying tab
  means the backlog is done, and **Clear all** beside the jump box does the same for
  every row at once. The saved corrections survive either way — only the ``annotate``
  flag is cleared.

Rows are revealed a page at a time (see :mod:`annie.pages.paging`), which also keeps
the tab from decoding five frames for every queued video the moment it opens.

**Export corrected CSV** (in the jump toolbar) writes the session's protagonist
corrections to the ``_manual`` CSV beside the heuristic source (see
:func:`annie.dataset.corrections`).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from nicegui import background_tasks, context, run, ui

from annie.core import logbook, theme
from annie.core.models import NO_ACTIVE_TRACK
from annie.core.state import state
from annie.dataset import corrections
from annie.dataset.segments import (
    export_decision_sets,
    load_segment_clips,
    next_undecided_index,
)
from annie.dataset.sources import TASK_LABELS, TaskKind, task_readiness
from annie.media.clipping import cut_clip
from annie.media.decode import media_available
from annie.media.preview import build_band_strip, build_preview, to_data_uri
from annie.media.rendering import JobStatus
from annie.pages.lazy import schedule
from annie.pages.paging import paged
from annie.pages.utils import _alive, notify_detached, render_embed_ttl, unembed_after_idle
from annie.pages.viewport import observe_row
from annie.parsers.participants import load_participants

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from nicegui.events import KeyEventArguments

    from annie.core.models import VideoEntry
    from annie.dataset.storage import Decision, Verdict

#: The Annotator tab element, so its enabled/disabled state can be toggled.
_tab: ui.tab | None = None
#: Shared CSS for the grey, centred media placeholder box on each Annotator row.
_BOX = "border-radius:8px;background:#e5e7eb;display:flex;align-items:center;justify-content:center"

#: Per-client timer hosts, keyed by client id; cleaned up on disconnect in :func:`render`.
_timer_hosts: dict[str, ui.element] = {}

#: The task each client is currently working, keyed by client id. Defaults to the
#: first ready task when the body builds; cleaned up on disconnect in :func:`render`.
_active_task: dict[str, TaskKind] = {}


@dataclass
class _SegmentState:
    """Per-client Segment-review position: the loaded clips and the current index.

    ``loaded_from`` records the source the ``clips`` were read from — tracked
    separately from ``clips`` being non-empty so a source that legitimately yields
    zero clips is not re-read from disk on every refresh.
    """

    source_path: Path | None = None
    loaded_from: Path | None = None
    clips: list = field(default_factory=list)
    index: int = 0


#: Per-client Segment-review state, keyed by client id; cleaned up on disconnect.
_segment_states: dict[str, _SegmentState] = {}


def _ready_tasks() -> list[TaskKind]:
    """The tasks whose required sources are present (offered in the task switch)."""
    return [r.task for r in task_readiness(state.registry) if r.ready]


def _current_task() -> TaskKind | None:
    """The client's selected task, defaulting to the first ready one (or ``None``)."""
    ready = _ready_tasks()
    if not ready:
        return None
    cid = context.client.id
    chosen = _active_task.get(cid)
    if chosen not in ready:
        chosen = ready[0]
        _active_task[cid] = chosen
    return chosen


def _select_task(task: TaskKind) -> None:
    """Switch the client's active task and rebuild the body."""
    _active_task[context.client.id] = task
    _content.refresh()


#: Image/video fill style for a responsive media box (letterbox, never crop a face).
_IMG = "width:100%;height:100%;object-fit:contain"


def set_tab(tab: ui.tab) -> None:
    """Register the tab element so availability can be toggled from elsewhere."""
    global _tab
    _tab = tab


def _host() -> ui.element:
    """The persistent timer host for the current client.

    Normally created once in :func:`render`; recreated lazily here if a rapid
    disconnect/reconnect (e.g. quick tab-clicking) popped the per-client state
    before a queued :func:`refresh` ran, rather than crashing the background task.
    """
    cid = context.client.id
    host = _timer_hosts.get(cid)
    if host is None:
        host = ui.element("div").style("display:none")
        _timer_hosts[cid] = host
    return host


def _queued_entries() -> list[VideoEntry]:
    """Return the manifest entries the user queued for annotation."""
    if state.scan is None:
        return []
    keys = state.store.annotator_keys()
    return [e for e in state.scan.entries if e.key in keys]


def _tab_available() -> bool:
    """Whether the Annotator has anything to offer: a queued video or a ready task.

    Queue-based tasks (protagonist, curation) need at least one queued video; the
    Segment-review task instead needs a segmentation source, which makes it *ready*
    without any queue. Either is enough to open the tab.
    """
    if _queued_entries():
        return True
    return any(t is TaskKind.SEGMENT_REVIEW for t in _ready_tasks())


def sync_tab() -> None:
    """Enable the tab when a video is queued or a queue-free task is ready."""
    if _tab is not None:
        if _tab_available():
            _tab.props(remove="disable")
        else:
            _tab.props(add="disable")


def update_availability() -> None:
    """Sync the tab state and rebuild the body (runtime; needs the event loop)."""
    sync_tab()
    _content.refresh()


def _box_style(scale: float = 1.0) -> str:
    """Style for one responsive media box: flexes to share the row width.

    The boxes (original + five frames + render) split the available width equally and
    shrink to fit the viewport, so a full row never overflows horizontally. The Settings
    row-height acts as a **max height** cap on very wide screens; ``scale`` shrinks that
    cap (e.g. the Curation task uses ``0.8`` to leave room for its review controls).

    Args:
        scale: Fraction of the configured row height to cap the box at.
    """
    cap = round(state.ui.annotator_row_height * scale)
    return f"flex:1 1 0;min-width:0;aspect-ratio:16/9;max-height:{cap}px;{_BOX}"


def _original_placeholder(entry: VideoEntry, scale: float = 1.0) -> None:
    """Draw a Browse-style ORIGINAL box: a play button that embeds the clip on click.

    Unlike an auto-drawn first frame, this signals clearly that a click starts a video.
    The embed reverts to the button once idle so many plays don't accumulate buffers.

    Args:
        entry: The video the box previews.
        scale: Row-height fraction for the box (see :func:`_box_style`).
    """
    box = ui.column().classes("items-center justify-center").style(_box_style(scale))
    if entry.video_path is None:
        with box:
            ui.icon("videocam_off", color=theme.NEUTRAL).tooltip("audio only — no video frames")
        return
    video_path = entry.video_path

    def reset() -> None:
        box.clear()
        with box:
            ui.button("ORIGINAL", icon="play_circle", on_click=play).props("flat dense")

    def play() -> None:
        if not video_path.exists():
            logbook.report(f"Video file not found: {video_path}")
            ui.notify(f"File not found: {video_path.name}", color=theme.DANGER)
            return
        box.clear()
        with box:
            ui.video(video_path, autoplay=True).style(_IMG)
        unembed_after_idle(box, reset)

    reset()


def _protagonist_source() -> tuple[Path, str, str] | None:
    """Return the protagonist ``(path, key_column, value_column)``, if configured."""
    source = state.registry.protagonist
    if source is None or source.key_column is None or not source.value_columns:
        return None
    return source.path, source.key_column, source.value_columns[0]


def _export() -> None:
    """Export this session's protagonist corrections as the ``_manual`` CSV.

    Corrections live in the session database; this writes them to the manual CSV
    next to the protagonist (heuristic) source, on demand.
    """
    resolved = _protagonist_source()
    if resolved is None:
        ui.notify("Add a protagonist CSV on the Dataset tab first.", color=theme.WARNING)
        return
    path, key_column, value_column = resolved
    overrides = state.store.active_tracks()  # row_key (== video id) -> track id
    if not overrides:
        ui.notify("No protagonist corrections to export yet.", color=theme.WARNING)
        return
    out = corrections.manual_sibling(path)
    corrections.export_active_tracks(out, overrides, key_column, value_column)
    ui.notify(f"Exported {len(overrides)} correction(s) to {out}", color=theme.PRIMARY)


def _persist(entry: VideoEntry, track_id: int) -> None:
    """Save a protagonist choice to the session DB (never autosaves the manual CSV)."""
    state.store.set_active_track(entry.key, entry.video_id, None, track_id)


def _media_box(scale: float = 1.0) -> tuple[int, int]:
    """The ``(width, height)`` an Annotator media box occupies at its 16:9 cap.

    Args:
        scale: Row-height fraction (see :func:`_box_style`).
    """
    height = round(state.ui.annotator_row_height * scale)
    return round(height * 16 / 9), height


async def _populate_strip(entry: VideoEntry, strip: list[ui.element], scale: float = 1.0) -> None:
    """Decode and draw the five annotated strip frames for ``entry`` (no thumbnail).

    The ORIGINAL box is a separate lazy-embed placeholder now, so the strip only fills
    the five frame slots (vdet blue, active track green).

    Args:
        entry: The video to preview (its ``active_track_id`` drives the green overlay).
        strip: The five frame slots to fill.
        scale: Row-height fraction for sizing the embedded frames.
    """
    if not strip:
        return
    try:
        await strip[0].client.connected()
    except Exception:  # noqa: BLE001 - client never connected / already gone
        return
    try:
        result = await run.io_bound(build_preview, entry)
    except Exception:  # noqa: BLE001 - a bad/missing file must not break the row
        return
    if result is None:
        return  # NiceGUI's io_bound yields None while the app is shutting down
    _thumbnail, frames, _ = result
    box = _media_box(scale)
    for slot, frame in zip(strip, frames, strict=False):
        if not _alive(slot):
            continue
        try:
            slot.clear()
            with slot:
                ui.image(to_data_uri(frame, box)).style(_IMG)
        except RuntimeError:
            return  # the row was refreshed away mid-decode


async def _watch_render(job_id: str, box: ui.element, restore: Callable[[], None]) -> None:
    """Poll an annotator render job and embed the clip when it's done.

    ``restore`` rebuilds the idle render button; it runs once the embedded clip has
    sat idle, so a burst of renders does not pin their clips in the tab.
    """
    while True:
        job = state.renderer.get(job_id)
        if job is None:
            return
        if not _alive(box):
            return  # page reloaded/closed; stop watching
        try:
            if job.status is JobStatus.DONE and job.output_path is not None:
                box.clear()
                with box:
                    ui.video(job.output_path, autoplay=True).style(_IMG)
                unembed_after_idle(box, restore, ttl=render_embed_ttl())
                return
            if job.status is JobStatus.FAILED:
                box.clear()
                with box:
                    ui.icon("error", color=theme.DANGER).tooltip(job.error or "render failed")
                return
        except RuntimeError:
            return
        await asyncio.sleep(0.4)


def _option_label(track_id: int, default_tid: int, track_ids: list[int]) -> str:
    """Human label for a track option, marking the source/missing/default state."""
    if track_id == NO_ACTIVE_TRACK:
        name = "none"
    elif track_id in track_ids:
        name = f"track{track_id}"
    else:
        name = f"track{track_id} (no track file)"  # the CSV references an absent track
    return f"{name} (default)" if track_id == default_tid else name


def _track_options(entry: VideoEntry, default_tid: int) -> dict[int, str]:
    """Build the selectable track options, always including the active/default ids.

    The protagonist CSV can name a track id that has no track file on disk (or a
    different one than the heuristic), which must still be a valid, visible option —
    otherwise ``ui.select`` raises ``ValueError: Invalid value``.
    """
    ids = [NO_ACTIVE_TRACK, *entry.track_ids]
    for extra in (entry.active_track_id, default_tid):
        if extra not in ids:
            ids.append(extra)
    return {tid: _option_label(tid, default_tid, entry.track_ids) for tid in ids}


def _dequeue(entry: VideoEntry, card: ui.card) -> None:
    """Drop a finished video from the queue, keeping its saved correction.

    Only the ``annotate`` flag is cleared; the protagonist choice lives in the
    ``_manual`` CSV and still shows up in "Export corrected CSV".

    The rendered card is deleted in place rather than refreshing the body, which
    would re-page the list back to the top and lose the reviewer's scroll position.
    The surviving rows keep their numbers: a row id is the sample's position in the
    dataset, so removing a neighbour never renumbers it.

    The toast is raised **before** the card goes away: this runs inside the click
    handler's slot, which NiceGUI resolves through the button's parent row. Deleting
    the card drops the last reference to that row, so any later ``ui.notify`` fails
    to find a client and raises "the parent element this slot belongs to has been
    deleted".
    """
    state.store.set_annotate(entry.key, entry.video_id, None, value=False)
    ui.notify(f"Removed {entry.label} from the queue", color=theme.PRIMARY)
    if _queued_entries():
        card.delete()
        sync_tab()
    else:  # the queue just emptied — rebuild into the empty state and grey the tab
        update_availability()


async def _clear_all() -> None:
    """Empty the queue in one go — the bulk form of clicking every row's X.

    Only the ``annotate`` flags are cleared, so every protagonist correction stays in
    the ``_manual`` CSV and still lands in "Export corrected CSV". Rebuilding a queue
    means re-checking boxes on Browse, so the action is confirmed first.
    """
    entries = _queued_entries()
    if not entries:
        return

    with ui.dialog() as dialog, ui.card().classes("w-96 gap-3"):
        ui.label("Clear the Annotator queue?").classes("text-lg font-medium")
        ui.label(
            f"Removes all {len(entries)} rows. The protagonist tracks you picked are "
            "kept and still export — only the queue is emptied."
        ).classes("text-sm").style(f"color:{theme.NEUTRAL}")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=lambda: dialog.submit(False)).props("flat")
            ui.button(
                "Clear all", icon="playlist_remove", on_click=lambda: dialog.submit(True)
            ).props("unelevated")

    if not await dialog:
        return

    # Captured before the rebuild below deletes the slot this handler is running in.
    client = context.client
    for entry in entries:
        state.store.set_annotate(entry.key, entry.video_id, None, value=False)
    update_availability()
    notify_detached(client, f"Cleared {len(entries)} rows from the queue", color=theme.PRIMARY)


def _clear_all_button() -> None:
    """The "Clear all" control that sits beside the jump box."""
    ui.button("Clear all", icon="playlist_remove", on_click=_clear_all).props("flat dense").tooltip(
        "Remove every row from the queue (the saved corrections are kept)"
    )


def _row_card(entry: VideoEntry, *, can_decode: bool, default_tid: int) -> None:
    """Render one Annotator row with an auto-saving protagonist selector.

    The thumbnail and strip are dropped once the row has been scrolled well past (see
    :mod:`annie.pages.viewport`) and decoded again — at the currently selected track —
    when it returns.
    """
    selected = {"track": entry.active_track_id}

    with ui.card().classes("w-full relative") as card:
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.badge(f"#{entry.row_id}", color=theme.NEUTRAL).tooltip(
                "This sample's number in the dataset"
            )
            ui.label(entry.label).classes("font-medium break-all flex-grow")
            ui.button(icon="close", on_click=lambda: _dequeue(entry, card)).props(
                "flat round dense"
            ).tooltip("Remove from the queue (the saved correction is kept)")
        with ui.row().classes("w-full items-stretch gap-2 no-wrap"):
            _original_placeholder(entry)
            strip: list[ui.element] = [ui.element("div").style(_box_style()) for _ in range(5)]
            render_box = ui.column().classes("items-center justify-center").style(_box_style())

        def repopulate() -> None:
            preview_entry = replace(entry, active_track_id=selected["track"])
            schedule(_host(), lambda: _populate_strip(preview_entry, strip))

        def unload_media() -> None:
            """Drop the decoded frames, restoring the placeholders the row started as."""
            for slot in strip:
                if _alive(slot):
                    slot.clear()

        def do_render() -> None:
            render_box.clear()
            with render_box:
                ui.spinner(size="lg")
            preview_entry = replace(entry, active_track_id=selected["track"])
            job_id = state.renderer.submit(preview_entry)
            background_tasks.create(
                _watch_render(job_id, render_box, reset_render), name="annie-anno-render"
            )

        def reset_render() -> None:
            render_box.clear()
            with render_box:
                if can_decode and entry.has_video:
                    ui.button("render", icon="movie", on_click=do_render).props("flat dense")
                else:
                    ui.icon("movie_filter", color=theme.NEUTRAL).tooltip("no video to render")

        reset_render()

        with ui.row().classes("items-center gap-3"):
            options = _track_options(entry, default_tid)
            picker = (
                ui.select(options, value=entry.active_track_id, label="Protagonist")
                .props("dense outlined")
                .classes("min-w-[12rem]")
            )

            def on_pick(value: int) -> None:
                selected["track"] = value
                entry.active_track_id = value
                _persist(entry, value)  # save to the session DB; Export writes the CSV
                repopulate()  # redraw the strip with the new green track
                reset_render()  # the rendered clip is now stale

            picker.on_value_change(lambda e: on_pick(e.value))

        if not entry.has_track:
            ui.label("No tracks for this video.").classes("text-xs").style(f"color:{theme.NEUTRAL}")

        if can_decode and entry.has_video:
            observe_row(load=repopulate, unload=unload_media)

    if can_decode and entry.has_video:
        repopulate()


#: Curation media boxes are 20% shorter than the protagonist row, leaving vertical room
#: for the like/dislike/note controls beneath them.
_CURATION_SCALE = 0.8

#: Height of the Segment-review source video, in px (width follows from 16:9). One clip is
#: on screen at a time, so the box is centred at a fixed height rather than scaling with
#: the row-height preference — which is tuned for a scrolling list of rows.
_SEGMENT_ORIGINAL_HEIGHT = 180

#: Share of a band row's width taken by the aligned name/start/end/duration cells; the
#: five span frames and the clip box divide the remaining ~80%.
_BAND_FACTS_WIDTH = "20%"

#: Gap between the aligned span facts and the first span frame — wide enough that the
#: numbers read as their own column instead of crowding the filmstrip.
_BAND_FACTS_GAP = "3em"

#: Frames sampled across a band's span (the strip beside the clip box).
_BAND_STRIP_COUNT = 5

#: Media elements in a band row: the span frames plus the clip box. The strip and the clip
#: box divide the row's width in this proportion, so every element comes out one size.
_BAND_MEDIA_COUNT = _BAND_STRIP_COUNT + 1

#: Ceiling for a band-row media element's height, in px. The *actual* height is whatever
#: the six elements' equal share of the row width works out to at 16:9 — this only stops
#: them growing on a very wide monitor.
_BAND_MEDIA_MAX_HEIGHT = 150

#: Layout for one band-row media element (a span frame or the clip box).
#:
#: The six divide the row's free width equally (``flex:1 1 0`` over a ``min-width:0``
#: parent, so they may shrink below their intrinsic size) and ``aspect-ratio`` derives the
#: height from that width — which is what keeps the strip inside the viewport with no
#: horizontal scrollbar at any window size. ``min-width:0`` on the element itself is what
#: makes the shrink legal: without it an image's intrinsic width becomes a floor and the
#: row overflows.
_BAND_MEDIA_STYLE = (
    f"flex:1 1 0;min-width:0;aspect-ratio:16/9;max-height:{_BAND_MEDIA_MAX_HEIGHT}px;"
    "object-fit:contain"
)

#: The ``(width, height)`` a band-row frame is encoded at. Pinned to the *maximum*
#: display size (:data:`_BAND_MEDIA_MAX_HEIGHT` at 16:9) rather than the actual laid-out
#: size, which only the browser knows — so a frame is never upscaled, just letterboxed
#: down by ``object-fit`` when the row is narrower.
_BAND_MEDIA_BOX = (round(_BAND_MEDIA_MAX_HEIGHT * 16 / 9), _BAND_MEDIA_MAX_HEIGHT)


def _curation_row(entry: VideoEntry) -> None:
    """One curation row: Browse-style media, then like/dislike verdict and a note.

    Mirrors a Browse row (ORIGINAL lazy-embed, five-frame strip, render box) but sized
    20% smaller so the review controls fit beneath. This is the supervision that used to
    live on Browse rows; it now belongs to the Annotator's Curation task. Every video is
    liked by default (no stored row), so like starts active until the reviewer disagrees.
    """
    review = state.review_state(entry.key)
    verdict = {"value": review.verdict}
    can_decode = media_available()

    with ui.card().classes("w-full gap-2").style(f"border:{theme.ROW_BORDER}"):
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.badge(f"#{entry.row_id}", color=theme.NEUTRAL)
            ui.label(entry.label).classes("font-medium break-all flex-grow")

        with ui.row().classes("w-full items-stretch gap-2 no-wrap"):
            _original_placeholder(entry, _CURATION_SCALE)
            strip: list[ui.element] = [
                ui.element("div").style(_box_style(_CURATION_SCALE)) for _ in range(5)
            ]
            _curation_render_box(entry, can_decode)

        with ui.row().classes("w-full items-center gap-3 wrap"):
            like = ui.button(icon="thumb_up").props("flat dense")
            dislike = ui.button(icon="thumb_down").props("flat dense")

            def paint() -> None:
                liked = verdict["value"] != "bad"  # "good" (the default) reads as liked
                like.props(f"color={'positive' if liked else 'grey-5'}")
                dislike.props(f"color={'negative' if not liked else 'grey-5'}")

            def set_verdict(value: Verdict) -> None:
                verdict["value"] = value
                state.store.set_verdict(entry.key, entry.video_id, None, value)
                paint()

            like.on_click(lambda: set_verdict("good"))
            dislike.on_click(lambda: set_verdict("bad"))
            paint()

            note = ui.input("note", value=review.note).props("dense").classes("flex-grow")
            note.on(
                "blur", lambda _: state.store.set_note(entry.key, entry.video_id, None, note.value)
            )

        def unload_strip() -> None:
            for slot in strip:
                if _alive(slot):
                    slot.clear()

        if can_decode and entry.has_video:
            observe_row(
                load=lambda: schedule(
                    _host(), lambda: _populate_strip(entry, strip, _CURATION_SCALE)
                ),
                unload=unload_strip,
            )
    if can_decode and entry.has_video:
        schedule(_host(), lambda: _populate_strip(entry, strip, _CURATION_SCALE))


def _curation_render_box(entry: VideoEntry, can_decode: bool) -> None:
    """A render box for a curation row (annotated clip on demand), sized at 80%."""
    box = ui.column().classes("items-center justify-center").style(_box_style(_CURATION_SCALE))

    def reset() -> None:
        box.clear()
        with box:
            if can_decode and entry.has_video:
                ui.button("render", icon="movie", on_click=do_render).props("flat dense")
            else:
                ui.icon("movie_filter", color=theme.NEUTRAL).tooltip("no video to render")

    def do_render() -> None:
        box.clear()
        with box:
            ui.spinner(size="lg")
        job_id = state.renderer.submit(entry)
        background_tasks.create(_watch_render(job_id, box, reset), name="annie-curation-render")

    reset()


def _curation_task() -> None:
    """The keep/drop-plus-note curation work area over the queued videos."""
    entries = _queued_entries()
    if not entries:
        ui.label("No videos queued. Select rows on the Browse tab to curate them here.").style(
            f"color:{theme.NEUTRAL}"
        )
        return
    paged(
        entries,
        _curation_row,
        row_id=lambda entry: entry.row_id,
        total_rows=len(state.scan.entries) if state.scan is not None else len(entries),
        actions=_clear_all_button,
    )


def _segment_state() -> _SegmentState:
    """The current client's Segment-review state, created on first use."""
    return _segment_states.setdefault(context.client.id, _SegmentState())


def _load_segment_clips_if_needed(state_: _SegmentState) -> None:
    """(Re)load clips from the segmentation source when it changed since last time."""
    source = next(iter(state.registry.segmentation_sources), None)
    if source is None:
        state_.source_path = None
        state_.loaded_from = None
        state_.clips = []
        return
    if state_.loaded_from == source.path:
        return  # already loaded from this source (even if it yielded zero clips)
    state_.source_path = source.path
    state_.clips = load_segment_clips(source)
    state_.loaded_from = source.path
    state_.index = 0


def _segment_export() -> None:
    """Write the accepted and dropped clips to two CSVs beside the segmentation source."""
    state_ = _segment_state()
    if not state_.clips or state_.source_path is None:
        ui.notify("Load a segmentation CSV on the Dataset tab first.", color=theme.WARNING)
        return
    decisions = state.store.decisions()
    if not any(c.key in decisions for c in state_.clips):
        ui.notify("No accept/drop decisions to export yet.", color=theme.WARNING)
        return
    stem = state_.source_path.stem
    accepted_path = state_.source_path.with_name(f"{stem}_accepted.csv")
    dropped_path = state_.source_path.with_name(f"{stem}_dropped.csv")
    accepted, dropped = export_decision_sets(state_.clips, decisions, accepted_path, dropped_path)
    n_acc = sum(1 for c in state_.clips if decisions.get(c.key) == "accept")
    n_drop = sum(1 for c in state_.clips if decisions.get(c.key) == "drop")
    ui.notify(
        f"Exported {n_acc} accepted → {accepted.name}, {n_drop} dropped → {dropped.name}",
        color=theme.PRIMARY,
    )


def _decide(key: str, video_id: str, decision: Decision) -> None:
    """Persist an accept/drop for the current clip and rebuild (no auto-advance).

    Unlike the old flow this stays on the clip: the reviewer sees the decision highlight
    (green/red border) confirm on the sample they were judging, then moves on with the
    arrows or the "jump to next undecided" control.
    """
    state.store.set_decision(key, video_id, decision)
    _segment_review_task.refresh()


def _undecide(key: str) -> None:
    """Clear the current clip's accept/drop, returning it to the undecided pool."""
    state.store.clear_decision(key)
    _segment_review_task.refresh()


def _jump_to_undecided(state_: _SegmentState) -> None:
    """Move the cursor to the next clip with no decision yet (wrapping); rebuild.

    Does nothing when every clip is decided — the space key and jump button are inert in
    that state, matching the disabled button the toolbar draws.

    This reads the decisions fresh rather than taking the rebuild's map: it runs from a
    click/key handler *after* that rebuild, by which point the reviewer may have decided
    the very clip the stale map still lists as undecided.
    """
    target = next_undecided_index(state_.clips, state.store.decisions(), state_.index)
    if target is not None:
        state_.index = target
        _segment_review_task.refresh()


@ui.refreshable
def _segment_review_task() -> None:
    """The accept/drop Segment-review work area: one clip at a time, resumable.

    The clips come from the segmentation source; the current index walks through them.
    A single top toolbar carries the decision progress bar, the keyboard-shortcut legend,
    and Export in one line. Each clip shows its passthrough tags at the top (so it reads
    big), a lazy-embed ORIGINAL video, then one grouped row per band — a span label, its
    five span frames, and a "clip" placeholder that cuts the exact span on demand. The
    Accept/Drop buttons sit centred at the bottom; a decision saves to the review DB (so a
    half-finished pass resumes on return) and paints the row border green (accept) or red
    (drop).
    """
    state_ = _segment_state()
    _load_segment_clips_if_needed(state_)
    clips = state_.clips
    if not clips:
        ui.label(
            "No clips loaded. Add a segmentation CSV on the Dataset tab (role "
            "'Segmentation') to review its clips here."
        ).style(f"color:{theme.NEUTRAL}")
        return

    decisions = state.store.decisions()
    state_.index = max(0, min(state_.index, len(clips) - 1))
    clip = clips[state_.index]
    n_decided = sum(1 for c in clips if c.key in decisions)
    has_undecided = n_decided < len(clips)

    _segment_toolbar(state_, n_decided, has_undecided=has_undecided)
    _segment_keyboard(state_, clip, has_undecided=has_undecided)

    current = decisions.get(clip.key)
    with ui.card().classes("w-full gap-2").style(_decision_card_style(current)):
        with ui.row().classes("w-full items-center gap-2 wrap"):
            ui.badge(f"{state_.index + 1} / {len(clips)}", color=theme.NEUTRAL)
            ui.label(clip.video_id).classes("font-medium break-all")
            if current is not None:
                ui.badge(
                    current,
                    color=theme.DECISION_ACCEPT if current == "accept" else theme.DECISION_DROP,
                )

        _segment_tags(clip)  # passthrough annotations up top, like a Browse row
        _segment_original(clip)
        _segment_bands(clip)
        _segment_decision_bar(state_, clip, decisions)


def _decision_card_style(current: Decision | None) -> str:
    """The clip card's border + fill for its decision state.

    Mirrors the Browse selection highlight — a saturated border with a *lighter* wash of
    the same hue inside — so a decided clip reads at a glance from across the room rather
    than needing the badge to be found.

    Args:
        current: The clip's stored decision, or ``None`` when undecided.

    Returns:
        A CSS declaration string for the card.
    """
    if current == "accept":
        return f"border:2px solid {theme.DECISION_ACCEPT};background:{theme.DECISION_ACCEPT_TINT}"
    if current == "drop":
        return f"border:2px solid {theme.DECISION_DROP};background:{theme.DECISION_DROP_TINT}"
    return f"border:{theme.ROW_BORDER};background:transparent"


def _segment_decision_bar(
    state_: _SegmentState,
    clip,  # noqa: ANN001 - SegmentClip
    decisions: Mapping[str, Decision],
) -> None:
    """The clip's final-decision row: Accept / Undecided / Drop, plus the step controls.

    The three verdicts sit centred — the affordance the whole card builds up to — with
    extra space above them so they read as the closing action rather than another band.
    "Undecided" clears any stored verdict, putting the clip back in the pool the progress
    bar counts. The previous/jump/next controls sit bottom-right, out of the decision's
    way but where the hand already is.
    """
    with ui.row().classes("w-full items-center gap-3 no-wrap mt-6"):
        ui.element("div").classes("flex-grow")  # centres the verdicts against the nav below
        with ui.row().classes("items-center gap-3"):
            ui.button(
                "Accept", icon="check", on_click=lambda: _decide(clip.key, clip.video_id, "accept")
            ).props("unelevated").style(
                f"background:{theme.DECISION_ACCEPT} !important;color:#fff !important"
            ).tooltip("Keep this clip [a]")
            ui.button("Undecided", icon="remove", on_click=lambda: _undecide(clip.key)).props(
                "unelevated"
            ).style(
                f"background:{theme.DECISION_UNDECIDED} !important;color:#fff !important"
            ).tooltip("Clear this clip's verdict and put it back in the undecided pool [s]")
            ui.button(
                "Drop", icon="close", on_click=lambda: _decide(clip.key, clip.video_id, "drop")
            ).props("unelevated").style(
                f"background:{theme.DECISION_DROP} !important;color:#fff !important"
            ).tooltip("Reject this clip [d]")
        with ui.row().classes("items-center gap-1 flex-grow justify-end"):
            _segment_nav_buttons(state_, decisions)


def _segment_toolbar(state_: _SegmentState, n_decided: int, *, has_undecided: bool) -> None:
    """The single top toolbar: progress bar, shortcut legend, and Export, on one line.

    Open by default (no collapsible), so the reviewer never has to expand a panel
    mid-pass. The step controls are *not* here — they live at the clip card's bottom-right
    (see :func:`_segment_decision_bar`), beside the verdict buttons the hand is already on.
    """
    total = len(state_.clips)
    with ui.card().classes("w-full gap-2"), ui.row().classes("w-full items-center gap-3 wrap"):
        with ui.column().classes("gap-0 min-w-[12rem] flex-grow"):
            ui.label(f"{n_decided} / {total} clips decided").classes("text-sm")
            ui.linear_progress(value=n_decided / total if total else 0.0, show_value=False)

        ui.label(
            "[←]/[→] step · [space] next undecided · [a] accept · [s] undecided · [d] drop"
        ).classes("text-xs").style(f"color:{theme.NEUTRAL}")
        if not has_undecided:
            ui.badge("all decided", color=theme.DECISION_ACCEPT)

        ui.button("Export", icon="download", on_click=_segment_export).props("flat dense").tooltip(
            "Write the accepted and dropped clips to two CSVs beside the segmentation source"
        )


def _segment_nav_buttons(state_: _SegmentState, decisions: Mapping[str, Decision]) -> None:
    """The previous / jump-to-undecided / next controls (bottom-right of the clip card).

    The jump button is disabled once every clip is decided, matching the inert ``space``
    key — there is nowhere left to jump to.

    Args:
        state_: The client's Segment-review position.
        decisions: The decision map this rebuild already read, passed down rather than
            re-queried — each :meth:`~annie.dataset.storage.ReviewStore.decisions` call
            opens its own SQLite connection, and a rebuild happens per keystroke.
    """
    can_jump = next_undecided_index(state_.clips, decisions, state_.index) is not None
    ui.button(icon="chevron_left", on_click=lambda: _step(state_, -1)).props("flat round").tooltip(
        "Previous clip [←]"
    )
    jump = ui.button(icon="space_bar", on_click=lambda: _jump_to_undecided(state_)).props(
        "flat round"
    )
    jump.tooltip("Jump to the next undecided clip [space]")
    if not can_jump:
        jump.props(add="disable")
    ui.button(icon="chevron_right", on_click=lambda: _step(state_, 1)).props("flat round").tooltip(
        "Next clip [→]"
    )


def _step(state_: _SegmentState, delta: int) -> None:
    """Move the clip cursor by ``delta`` without deciding, and rebuild."""
    state_.index = max(0, min(state_.index + delta, len(state_.clips) - 1))
    _segment_review_task.refresh()


def _segment_keyboard(
    state_: _SegmentState,
    clip,  # noqa: ANN001 - SegmentClip
    *,
    has_undecided: bool,
) -> None:
    """Bind the arrows to step, ``space`` to jump-to-undecided, and a/s/d to the verdicts.

    ``a`` / ``s`` / ``d`` sit adjacent under the left hand in the same order as the
    Accept / Undecided / Drop buttons they fire.

    A fresh handler is created on every rebuild so it always closes over the clip now on
    screen; the keys only fire on ``keydown`` so a held key does not race the refresh. The
    ``ui.keyboard`` lives inside the refreshable, so each rebuild deletes the previous one
    rather than stacking listeners. ``space`` is inert once every clip is decided.
    """

    def on_key(event: KeyEventArguments) -> None:
        if not event.action.keydown or event.action.repeat:
            return
        key = event.key
        if key.arrow_left:
            _step(state_, -1)
        elif key.arrow_right:
            _step(state_, 1)
        elif key == " " and has_undecided:
            _jump_to_undecided(state_)
        elif key == "a":
            _decide(clip.key, clip.video_id, "accept")
        elif key == "s":
            _undecide(clip.key)
        elif key == "d":
            _decide(clip.key, clip.video_id, "drop")

    ui.keyboard(on_key=on_key)


def _clip_video_path(clip) -> Path | None:  # noqa: ANN001 - SegmentClip
    """Resolve a clip's source video from the scanned manifest, by ``video_id``.

    The clip's ``video_id`` *is* the join to the full video: the segmentation CSV names
    which video a span belongs to, and the videos folder is what says where that video
    lives. ``None`` when the manifest has no such video (e.g. the folder is not scanned
    yet), which the caller renders as a "no source video" placeholder.

    The lookup goes through :attr:`annie.dataset.scanning.ScanResult.by_video_id`, an
    index built once per scan: this runs on every rebuild of a keyboard-driven task, so a
    linear walk of the manifest would scan every entry on each arrow press.
    """
    if state.scan is None:
        return None
    entry = state.scan.by_video_id.get(clip.video_id)
    return entry.video_path if entry is not None else None


def _segment_original(clip) -> None:  # noqa: ANN001 - SegmentClip
    """A lazy-embed ORIGINAL box for the clip's whole source video (Browse-style).

    Capped at :data:`_SEGMENT_ORIGINAL_HEIGHT` and centred in the card: Segment review
    shows *one* clip at a time, so the source video is the card's focal point rather than
    one cell in a scrolling row.
    """
    video_path = _clip_video_path(clip)
    # Not _box_style(): that flexes to share a row, while this box stands alone at a fixed
    # height, centred by the wrapper rather than stretching to the full card width.
    centre = ui.row().classes("w-full justify-center")
    with centre:
        box = (
            ui.column()
            .classes("items-center justify-center")
            .style(f"height:{_SEGMENT_ORIGINAL_HEIGHT}px;aspect-ratio:16/9;max-width:100%;{_BOX}")
        )
    if video_path is None:
        with box:
            ui.icon("videocam_off", color=theme.NEUTRAL).tooltip("no source video found")
        return

    def reset() -> None:
        box.clear()
        with box:
            ui.button("ORIGINAL", icon="play_circle", on_click=play).props("flat dense")

    def play() -> None:
        if not video_path.exists():
            logbook.report(f"Video file not found: {video_path}")
            ui.notify(f"File not found: {video_path.name}", color=theme.DANGER)
            return
        box.clear()
        with box:
            ui.video(video_path, autoplay=True).style(_IMG)
        unembed_after_idle(box, reset)

    reset()


def _segment_bands(clip) -> None:  # noqa: ANN001 - SegmentClip, kept loose to avoid a runtime import
    """Render each band as one row: aligned span facts, then its frames and clip box.

    The competing bands (e.g. a ground-truth span and a WhisperX forced-alignment one) are
    read *against each other*, so their name/start/end/duration are laid out as borderless
    table cells that line up column-wise down the rows — the numbers can be compared by
    eye without hunting. The facts take :data:`_BAND_FACTS_WIDTH` of the row; the six media
    elements follow at their natural 16:9 size (see :data:`_BAND_MEDIA_STYLE`).
    """
    can_decode = media_available()
    video_path = _clip_video_path(clip)
    if clip.bands:
        _band_facts_header()
    for band in clip.bands:
        with ui.row().classes("w-full items-center no-wrap").style(f"gap:{_BAND_FACTS_GAP}"):
            _band_facts(band)
            if can_decode and video_path is not None:
                # min-w-0 lets this shrink below its children's intrinsic width, which is
                # what lets them divide the real viewport width instead of overflowing it.
                with ui.row().classes("items-center gap-1 no-wrap flex-grow min-w-0"):
                    _band_strip(video_path, band)
                    _band_clip_box(video_path, band)
            else:
                ui.label("(install the 'media' extra to preview frames)").classes("text-xs").style(
                    f"color:{theme.NEUTRAL}"
                )


def _band_facts_header() -> None:
    """The column captions above the band rows, aligned to :func:`_band_facts`' cells."""
    with ui.row().classes("w-full items-baseline no-wrap").style(f"gap:{_BAND_FACTS_GAP}"):
        with (
            ui.row()
            .classes("items-baseline no-wrap gap-2")
            .style(f"width:{_BAND_FACTS_WIDTH};flex:0 0 {_BAND_FACTS_WIDTH}")
        ):
            ui.label("band").classes("text-xs").style(f"flex:0 0 28%;color:{theme.NEUTRAL}")
            for caption in ("start", "end", "dur"):
                ui.label(caption).classes("text-xs text-right").style(
                    f"flex:1 1 0;color:{theme.NEUTRAL}"
                )
        ui.element("div").classes("flex-grow")


def _band_facts(band) -> None:  # noqa: ANN001 - ClipBand, see _segment_bands
    """The band's name, start, end, and duration as aligned, borderless table cells."""
    with (
        ui.row()
        .classes("items-baseline no-wrap gap-2")
        .style(f"width:{_BAND_FACTS_WIDTH};flex:0 0 {_BAND_FACTS_WIDTH}")
    ):
        ui.label(band.name).classes("text-sm font-medium").style("flex:0 0 28%")
        for value in (f"{band.start:.2f}", f"{band.end:.2f}", f"{band.end - band.start:.2f}s"):
            ui.label(value).classes("text-xs text-right").style(
                f"flex:1 1 0;font-variant-numeric:tabular-nums;color:{theme.NEUTRAL}"
            )


def _band_strip(video_path: Path, band) -> None:  # noqa: ANN001 - ClipBand, see _segment_bands
    """Decode and embed one band's span as a horizontal strip of span frames.

    The strip is its own flex row nested beside the clip box, so it claims the width of
    the frames it holds (:data:`_BAND_STRIP_COUNT` of the row's
    :data:`_BAND_MEDIA_COUNT` shares) — otherwise the whole strip would count as *one*
    element against the clip box and its frames would come out a sixth of the size.
    """
    row = (
        ui.row()
        .classes("gap-1 no-wrap items-center min-w-0")
        .style(f"flex:{_BAND_STRIP_COUNT} {_BAND_STRIP_COUNT} 0")
    )

    async def draw() -> None:
        try:
            frames = await run.io_bound(
                build_band_strip, video_path, band.start, band.end, _BAND_STRIP_COUNT
            )
        except Exception as exc:  # noqa: BLE001 - a bad/missing file must not break the row
            logbook.report(f"Could not decode band strip for {video_path}: {exc}")
            return
        if not frames or not _alive(row):
            return
        with row:
            for frame in frames:
                ui.image(to_data_uri(frame, _BAND_MEDIA_BOX)).style(_BAND_MEDIA_STYLE)

    schedule(_host(), draw)


def _band_clip_box(video_path: Path, band) -> None:  # noqa: ANN001 - ClipBand, see _segment_bands
    """A "clip" placeholder that cuts the band's exact span on demand and embeds it.

    Unlike the strip (still frames), this plays the span so the reviewer can judge the cut
    with motion and audio. The cut is done off the event loop and cached, so re-opening a
    band replays instantly. It takes one of the row's :data:`_BAND_MEDIA_COUNT` width
    shares — the same as a single span frame — so the six read as one filmstrip.
    """
    box = ui.column().classes("items-center justify-center").style(f"{_BAND_MEDIA_STYLE};{_BOX}")

    def reset() -> None:
        box.clear()
        with box:
            ui.button("clip", icon="content_cut", on_click=play).props("flat dense")

    async def play() -> None:
        box.clear()
        with box:
            ui.spinner(size="sm")
        try:
            path = await run.io_bound(cut_clip, video_path, band.start, band.end)
        except Exception as exc:  # noqa: BLE001 - a bad span/file must not break the row
            logbook.report(f"Could not cut clip for {video_path} [{band.start},{band.end}]: {exc}")
            if _alive(box):
                box.clear()
                with box:
                    ui.icon("error", color=theme.DANGER).tooltip("could not cut this span")
            return
        if path is None or not _alive(box):
            return
        box.clear()
        with box:
            ui.video(path, autoplay=True).style("width:100%;height:100%;object-fit:contain")
        unembed_after_idle(box, reset)

    reset()


def _segment_tags(clip) -> None:  # noqa: ANN001 - SegmentClip, see _segment_bands
    """Show the clip's passthrough columns as small read-only tags."""
    if not clip.tags:
        return
    with ui.row().classes("w-full items-center gap-2 wrap"):
        for name, value in clip.tags.items():
            if value:
                ui.badge(f"{name}: {value}", color=theme.NEUTRAL).style("white-space:normal")


def _task_selector(current: TaskKind) -> None:
    """Draw the task switch offering only the ready tasks, highlighting ``current``."""
    ready = _ready_tasks()
    with ui.row().classes("items-center gap-2 wrap"):
        ui.label("Task").classes("text-sm font-medium")
        for task in ready:
            active = task is current
            (
                ui.button(TASK_LABELS[task], on_click=lambda t=task: _select_task(t))
                .props("unelevated" if active else "flat")
                .props(f"color={'primary' if active else 'grey-6'}")
            )


def _protagonist_toolbar_actions() -> None:
    """Dataset-wide protagonist actions for the jump toolbar: Clear all + Export CSV.

    Grouping Export beside Clear all (rather than floating it above the rows) frames the
    toolbar as "what I can do across everything I've touched" in this task.
    """
    _clear_all_button()
    ui.button("Export corrected CSV", icon="download", on_click=_export).props("flat dense")


def _protagonist_task() -> None:
    """The protagonist-correction work area over the queued videos (the original task)."""
    entries = _queued_entries()
    if not entries:
        ui.label("No videos queued. Select rows on the Browse tab to review them here.").style(
            f"color:{theme.NEUTRAL}"
        )
        return
    if not media_available():
        ui.label("Install the 'media' extra to see and re-render frames.").classes("text-xs").style(
            f"color:{theme.WARNING}"
        )

    # The source-file (pre-manual) value per video, to mark the "(default)" option.
    resolved = _protagonist_source()
    defaults = load_participants(*resolved) if resolved is not None else {}

    can_decode = media_available()
    paged(
        entries,
        lambda entry: _row_card(
            entry,
            can_decode=can_decode,
            default_tid=defaults.get(entry.video_id, NO_ACTIVE_TRACK),
        ),
        row_id=lambda entry: entry.row_id,
        total_rows=len(state.scan.entries) if state.scan is not None else len(entries),
        actions=_protagonist_toolbar_actions,
    )


@ui.refreshable
def _content() -> None:
    """Build the Annotator body: a task switch over the ready tasks, then the work area.

    The task a client works is driven by the sources present (see
    :func:`annie.dataset.sources.task_readiness`): only *ready* tasks are offered, and
    switching one rebuilds the work area below. Each task owns its own supervision UI
    and persists to the same review database.
    """
    current = _current_task()
    with ui.column().classes("w-full gap-3"):
        if current is None:
            ui.label(
                "No task is ready yet. Add a videos folder — and a protagonist or "
                "segmentation CSV — on the Dataset tab."
            ).style(f"color:{theme.NEUTRAL}")
            return
        _task_selector(current)
        if current is TaskKind.PROTAGONIST:
            _protagonist_task()
        elif current is TaskKind.CURATION:
            _curation_task()
        elif current is TaskKind.SEGMENT_REVIEW:
            _segment_review_task()


def render() -> None:
    """Build the Annotator tab body; register per-client timer host."""
    client = context.client
    _timer_hosts[client.id] = ui.element("div").style("display:none")

    def _cleanup() -> None:
        _timer_hosts.pop(client.id, None)
        _active_task.pop(client.id, None)
        _segment_states.pop(client.id, None)

    client.on_disconnect(_cleanup)
    _content()


def refresh() -> None:
    """Rebuild the Annotator body (after queueing changes or a tab open)."""
    _content.refresh()
