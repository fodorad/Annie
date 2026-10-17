"""Browse tab — the scrollable, per-video dataset visualizer (main view).

Browse is a **read-only viewer**: it presents samples and lets the reviewer *select*
which ones to send to the Annotator, but it no longer records supervision (verdicts
and notes moved to the Annotator's Curation task). It is a pure consumer of the cached
scan manifest and only populates once a videos folder is configured. An always-visible
**filter bar** (not part of the scroll) narrows the list by name prefix,
video/audio/vdet/track presence, review verdict, notes, annotator selection, any
label-column values, and an explicit **id list read from a CSV column** (the loaded
file is named on the Filter header while the filter is active) — the verdict/note
facets still filter on curation gathered elsewhere. The View panel can queue the whole
filtered list into the Annotator in one click. Each bordered row is two stacked lines
plus a selection corner:

1. the video id, a "Show at location" icon, and the media/annotation/label **tags**
   (``video`` / ``audio`` / ``#frames`` / ``vdet`` / ``N track`` / ``main`` / labels);
2. an **ORIGINAL** placeholder (click to embed the clip), the annotated **five-frame
   strip**, and a **render** box (burn the full annotated clip and embed it).

The **top-right corner** holds the selection control: a faint ``check_circle`` that
brightens on hover and becomes a solid tick with a teal row border when the video is
queued for the Annotator. Only that corner selects — clicking the reveal button or a
media box never toggles the queue.

Row height is a Settings-tab preference. Frames decode and audio is probed lazily
in background tasks; the body refreshes when the tab is opened or sources change.

Each browser tab gets its own isolated filter state and transform settings via a
per-client :class:`_BrowseState` keyed by :attr:`nicegui.Client.id`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import background_tasks, context, ui

from annie.core import logbook, theme
from annie.core.state import state
from annie.dataset import manipulate
from annie.dataset.filtering import FilterSpec, apply_filters, drop_facets, source_facets
from annie.media.decode import media_available
from annie.media.preview import report_decode_failure
from annie.media.previews import SKIPPED, Strip, Tile
from annie.media.rendering import JobStatus
from annie.pages import annotator, pending
from annie.pages.csv_dialog import select_id_column
from annie.pages.folder_picker import pick_file
from annie.pages.lazy import schedule
from annie.pages.paging import paged
from annie.pages.refresh import client_refreshable
from annie.pages.reveal import is_docker, reveal
from annie.pages.utils import DEBOUNCE, _alive, render_embed_ttl, unembed_after_idle
from annie.pages.viewport import observe_row

if TYPE_CHECKING:
    from collections.abc import Callable

    from annie.core.models import VideoEntry

#: Whether Annie runs inside Docker. Evaluated once at import — the container
#: boundary is fixed for the process lifetime — so Browse rows don't stat
#: ``/.dockerenv`` on every render.
_IS_DOCKER = is_docker()


# ── per-client state ──────────────────────────────────────────────────────────


@dataclass
class _BrowseState:
    """Filter + transform state isolated to one browser tab.

    Attributes:
        spec: The active filter snapshot for this client.
        transforms: Per-label-column transform settings for this client.
        timer_host: The persistent hidden element used as a timer parent at build time.
        view_mode: ``"detailed"`` (full rows) or ``"grid"`` (Quick selection grid).
        jump_slot: The View-panel element that :func:`paged` builds the Jump card into.
    """

    spec: FilterSpec = field(default_factory=FilterSpec)
    transforms: dict[str, manipulate.Transform] = field(default_factory=dict)
    timer_host: ui.element | None = None
    view_mode: str = "detailed"
    jump_slot: ui.element | None = None


#: Per-client state registry; cleaned up on disconnect in :func:`render`.
_browse_state: dict[str, _BrowseState] = {}


def _state() -> _BrowseState:
    """Return the :class:`_BrowseState` for the currently active client."""
    cid = context.client.id
    if cid not in _browse_state:
        _browse_state[cid] = _BrowseState()
    return _browse_state[cid]


def _column_type(column: str) -> manipulate.ColumnType:
    """The data type chosen for a label column (defaults to ``str``)."""
    declared = state.scan.label_column_types.get(column, "str") if state.scan is not None else "str"
    if declared == "int":
        return "int"
    if declared == "float":
        return "float"
    return "str"


def _effective_label(entry: VideoEntry, column: str) -> str | None:
    """A label value after its column transform (Manipulate); ``None`` if absent.

    This is the **filtering** view of a label: it always reflects the transform, which is
    what makes the filter facets (e.g. the two sides of a ``threshold ≥ 0``) meaningful.
    What the row *shows* can differ — see :func:`_display_label`.
    """
    raw = entry.labels.get(column)
    if raw is None:
        return None
    transform = _state().transforms.get(column)
    if transform is None or transform.kind == "none":
        return raw
    return manipulate.apply_transform(raw, _column_type(column), transform)


def _display_label(entry: VideoEntry, column: str) -> str | None:
    """The label value to show on a row's tag; ``None`` if absent.

    Same as :func:`_effective_label` unless the column's transform has
    ``show_original`` set, in which case the raw value is shown while the filter keeps
    matching on the transformed one — so a reviewer can threshold a float column into a
    clean facet and still read the real number on each sample.
    """
    transform = _state().transforms.get(column)
    if transform is not None and transform.show_original:
        return entry.labels.get(column)
    return _effective_label(entry, column)


def _effective_values(column: str) -> list[str]:
    """Sorted distinct transformed values a column takes across the manifest."""
    if state.scan is None:
        return []
    seen = {v for e in state.scan.entries if (v := _effective_label(e, column))}

    def _key(text: str) -> tuple[int, float, str]:
        try:
            return (0, float(text), "")
        except ValueError:
            return (1, 0.0, text)

    return sorted(seen, key=_key)


#: A persistent, never-refreshed element that owns the lazy-decode and render-poll
#: timers. Parenting timers here (instead of inside a row) means a body refresh
#: never deletes a timer's parent slot mid-flight — the callbacks guard against the
#: row they target having been refreshed away.
def _host() -> ui.element:
    """The persistent timer host for the current client.

    Normally created once in :func:`render`; recreated lazily here if a rapid
    disconnect/reconnect (e.g. quick tab-clicking) popped the per-client state
    before a queued :func:`refresh` ran, rather than crashing the background task.
    """
    st = _state()
    if st.timer_host is None:
        st.timer_host = ui.element("div").style("display:none")
    return st.timer_host


def _media_dims() -> tuple[int, int]:
    """Return the ``(width, height)`` in px for every media box (16:9 by height)."""
    height = state.ui.browse_row_height
    return round(height * 16 / 9), height


def _grid_dims() -> tuple[int, int]:
    """Return the ``(width, height)`` in px for a Quick-selection grid box (16:9)."""
    height = state.ui.grid_thumb_height
    return round(height * 16 / 9), height


# ── tags & controls ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class _RowBadges:
    """Lazily-filled badges a row's decode/probe update."""

    frames: ui.element | None = None
    audio_slot: ui.element | None = None


def _settle_audio(slot: ui.element, present: bool | None) -> None:
    """Settle the audio badge: ``audio`` / ``no audio``, or ``audio: ?`` if unknown."""
    if present is None:
        pending.settle_badge(slot, "audio: ?")
    elif present:
        pending.settle_badge(slot, "audio", theme.AUDIO_TAG_COLOR)
    else:
        pending.settle_badge(slot, "no audio")


def _settle_unknown(badges: _RowBadges) -> None:
    """Settle both lazy badges as unknown (``?``): nothing will fill them."""
    if badges.frames is not None:
        pending.settle_badge(badges.frames, "#frames: ?")
    if badges.audio_slot is not None:
        _settle_audio(badges.audio_slot, None)


def _tags(entry: VideoEntry) -> _RowBadges:
    """Draw the media/annotation/label chips; return the lazily-filled badges.

    ``video`` is known from the scan; ``#frames`` fills after decode and the ``audio``
    chip after the audio probe (see :func:`_populate`). Until then each spins (see
    :mod:`annie.pages.pending`); a value already cached shows immediately.
    """
    badges = _RowBadges()
    if entry.has_video:
        badges.audio_slot = pending.pending_badge("audio")
        cached = state.audio_cache.get(entry.key)
        if cached is not None:
            _settle_audio(badges.audio_slot, cached)
        ui.badge("video", color=theme.VIDEO_TAG_COLOR)
        badges.frames = pending.pending_badge("#frames")
        frames = state.frames_cache.get(entry.key)
        if frames is not None:
            pending.settle_badge(badges.frames, f"#frames: {frames}")
    if entry.has_vdet:
        ui.badge("vdet", color=theme.VDET_COLOR)
    if entry.has_track:
        ui.badge(f"{len(entry.track_ids)} track", color=theme.TRACK_COLOR)
    if entry.has_active_track:
        ui.badge(f"main: track{entry.active_track_id}", color=theme.SUCCESS)
    columns = state.scan.label_columns if state.scan is not None else []
    for column in columns:
        value = _display_label(entry, column)
        if value:
            badge = ui.badge(f"{column}: {value}", color=theme.LABEL_COLOR)
            origin = entry.label_origins.get(column)
            if origin:  # which file, sheet and row the value came from
                badge.tooltip(origin)
    return badges


def _selection_control(entry: VideoEntry, card: ui.card) -> None:
    """Add a top-right **selection corner** that queues the row for the Annotator.

    Browse is a read-only viewer: the only state it writes is the *selection* — whether
    a video is queued for the Annotator. Selection lives in a small top-right corner
    (not the whole row), so clicking the reveal button or a media box never toggles it.
    The corner shows a **faint** ``check_circle`` as an affordance ("click here to
    select"), brightens on hover, and becomes a solid tick with a teal row border when
    selected.

    Args:
        entry: The video the row represents.
        card: The row's card element, whose border reflects the selected state.
    """
    review = state.review_state(entry.key)
    selected = {"value": review.in_annotator}

    corner = (
        ui.element("div")
        .classes("cursor-pointer")
        .style(
            "position:absolute;top:0;right:0;width:52px;height:52px;z-index:6;"
            "display:flex;align-items:flex-start;justify-content:flex-end;padding:6px"
        )
        .tooltip("Select this video for the Annotator")
    )
    with corner:
        check = ui.icon("check_circle").style("font-size:26px;border-radius:50%")

    def paint() -> None:
        if selected["value"]:
            card.style(f"border:2px solid {theme.SUCCESS};background:{_GRID_TINT}")
            check.style("font-size:26px;border-radius:50%;color:" + theme.SUCCESS + ";opacity:1")
        else:
            card.style(f"border:{theme.ROW_BORDER};background:transparent")
            # Faint by default; the hover rule below lifts the opacity as an affordance.
            check.style(f"font-size:26px;border-radius:50%;color:{theme.NEUTRAL};opacity:0.28")

    def toggle() -> None:
        selected["value"] = not selected["value"]
        state.store.set_annotate(entry.key, entry.video_id, None, selected["value"])
        annotator.update_availability()
        paint()

    def hover(opacity: str) -> None:
        # Only the faint (unselected) tick brightens on hover; a selected tick stays solid.
        if not selected["value"]:
            check.style(f"opacity:{opacity}")

    corner.on("mouseenter", lambda: hover("0.7"))
    corner.on("mouseleave", lambda: hover("0.28"))
    corner.on("click", lambda: toggle())
    paint()


def _reveal_target(entry: VideoEntry) -> Path | None:
    """Pick the most relevant on-disk file to reveal (video ▸ vdet ▸ first track)."""
    if entry.video_path is not None:
        return entry.video_path
    if entry.vdet_path is not None:
        return entry.vdet_path
    return entry.track_paths[0] if entry.track_paths else None


def _reveal_button(entry: VideoEntry) -> None:
    """Draw the icon-only "Show at location" button (reveals the file in the OS)."""
    target = _reveal_target(entry)
    # ``@click.stop`` keeps a reveal click from bubbling to the header's selection toggle.
    button = ui.button(icon="folder_open").props("flat dense round @click.stop")
    with button:
        label = "Copy path to clipboard" if _IS_DOCKER else "Show at location"
        ui.tooltip(label).props("delay=600")
    if target is None:
        button.disable()
        return

    def show() -> None:
        if _IS_DOCKER:
            ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(str(target))})")
            ui.notify(f"Path copied: {target}", icon="content_copy")
        else:
            try:
                reveal(target)
            except Exception as exc:  # noqa: BLE001 - surface any OS error to the user
                ui.notify(f"Could not reveal file: {exc}", color=theme.DANGER)

    button.on_click(show)


# ── media slots ──────────────────────────────────────────────────────────────


def _original_box(entry: VideoEntry) -> None:
    """Draw the ORIGINAL placeholder (like the render box): click to embed the clip."""
    width, height = _media_dims()
    box = (
        ui.column()
        .classes("items-center justify-center")
        .style(f"width:{width}px;height:{height}px;{pending.MEDIA_BOX}")
    )
    if entry.video_path is None:
        with box:
            ui.icon("videocam_off", color=theme.NEUTRAL).tooltip("video file not found")
        return
    video_path = entry.video_path

    def reset() -> None:
        """Restore the cheap ORIGINAL placeholder, dropping any embedded clip."""
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
            ui.video(video_path, autoplay=True).style(f"width:{width}px;height:{height}px")
        unembed_after_idle(box, reset)

    reset()


def _strip() -> list[ui.element]:
    """Create the five fixed strip slots and return them for later population."""
    width, height = _media_dims()
    slots: list[ui.element] = []
    with ui.row().classes("gap-1 no-wrap"):
        for _ in range(5):
            slots.append(pending.media_box(width, height))
    return slots


def _render_box(entry: VideoEntry) -> None:
    """Draw the render box: idle → spinner → embedded annotated clip on completion."""
    width, height = _media_dims()
    box = (
        ui.column()
        .classes("items-center justify-center")
        .style(f"width:{width}px;height:{height}px;{pending.MEDIA_BOX}")
    )

    def reset() -> None:
        """Restore the idle render button, dropping any embedded clip."""
        box.clear()
        with box:
            if entry.has_video:
                ui.button("render", icon="movie", on_click=start_render).props("flat dense")
            else:
                ui.icon("movie_filter", color=theme.NEUTRAL).tooltip("no video to render")

    def start_render() -> None:
        pending.pending(box, size="lg")
        job_id = state.renderer.submit(entry)
        background_tasks.create(
            _watch_render(job_id, box, width, height, reset), name="annie-render"
        )

    reset()


async def _watch_render(
    job_id: str, box: ui.element, width: int, height: int, restore: Callable[[], None]
) -> None:
    """Poll a render job from a background task and embed the clip when it's done.

    Guards every UI mutation so a body refresh mid-render stops the watcher cleanly
    instead of raising from a deleted slot. ``restore`` rebuilds the idle button once
    the embedded clip has sat idle, so rendered clips do not pile up in the tab.
    """
    while True:
        job = state.renderer.get(job_id)
        if job is None:
            return
        if not _alive(box):
            return  # page reloaded/closed; stop watching
        try:
            if job.status is JobStatus.DONE and job.output_path is not None:
                output = job.output_path
                pending.ready(
                    box,
                    lambda: ui.video(output, autoplay=True).style(
                        f"width:{width}px;height:{height}px"
                    ),
                )
                unembed_after_idle(box, restore, ttl=render_embed_ttl())
                return
            if job.status is JobStatus.FAILED:
                pending.failed(box, job.error or "render failed")
                return
        except RuntimeError:
            return  # the row was refreshed away; stop watching
        await asyncio.sleep(0.4)


async def _populate(entry: VideoEntry, strip: list[ui.element], badges: _RowBadges) -> None:
    """Decode the strip, fill the frame count, and probe the audio stream.

    Guards every UI mutation: if the row was refreshed away while the work was in
    flight, the target slots are gone and NiceGUI raises ``RuntimeError`` — which we
    swallow rather than letting it surface as a background-task error.
    """
    anchor = strip[0] if strip else None
    if anchor is not None:
        try:
            await anchor.client.connected()  # the task may start before the socket connects
        except Exception:  # noqa: BLE001 - client never connected / already gone
            return

    sw, sh = _media_dims()
    for slot in strip:
        pending.pending(slot)
    try:
        result = await state.previews.strip(
            entry, (sw, sh), wanted=lambda: anchor is None or _alive(anchor)
        )
    except Exception as exc:  # noqa: BLE001 - a bad/missing file must not break the row
        reason = report_decode_failure(entry.video_path, exc)
        if badges.frames is not None:
            pending.settle_badge(badges.frames, "#frames: ?")
        for slot in strip:
            pending.failed(slot, reason)
        await _probe_audio(entry, badges)
        return
    if not isinstance(result, Strip):
        return  # the row was refreshed away before its turn
    state.frames_cache[entry.key] = result.num_frames
    if badges.frames is not None:
        pending.settle_badge(badges.frames, f"#frames: {result.num_frames}")
    for slot, uri in zip(strip, result.uris, strict=False):
        pending.ready(slot, lambda u=uri: ui.image(u).style(f"width:{sw}px;height:{sh}px"))

    await _probe_audio(entry, badges)


async def _probe_audio(entry: VideoEntry, badges: _RowBadges) -> None:
    """Probe (and cache) whether the video has an audio stream; fill its chip slot."""
    if entry.video_path is None:
        return
    present = state.audio_cache.get(entry.key)
    if present is None:
        slot = badges.audio_slot
        try:
            probed = await state.previews.audio(
                entry.video_path, wanted=lambda: slot is None or _alive(slot)
            )
        except Exception:  # noqa: BLE001 - probe failure must not break the row
            probed = None
        if probed is SKIPPED:
            return
        present = probed if isinstance(probed, bool) else None
        if present is not None:
            state.audio_cache[entry.key] = present
    if badges.audio_slot is not None:
        _settle_audio(badges.audio_slot, present)


def _row_card(entry: VideoEntry, *, can_decode: bool) -> None:
    """Render one per-video row as three stacked lines: name+tags, media, controls.

    The strip frames are dropped once the row has been scrolled well past (see
    :mod:`annie.pages.viewport`) and decoded again when it returns; the slots keep
    their fixed size either way, so the page never reflows underneath the reviewer.
    """
    with ui.card().classes("w-full gap-2 relative").style(f"border:{theme.ROW_BORDER}") as card:
        # 1) row number, name, 1em, the reveal icon, 1em, then the tags. Selection is the
        #    top-right corner only (see _selection_control), so nothing on this line — the
        #    reveal button, the media boxes below — toggles the queue by accident.
        with ui.row().classes("items-center gap-0 wrap"):
            ui.badge(f"#{entry.row_id}", color=theme.NEUTRAL).classes("mr-2").tooltip(
                "This sample's number in the dataset — type it into 'Jump to row'"
            )
            ui.label(entry.label).classes("font-medium break-all")
            ui.element("div").style("min-width:1em")
            _reveal_button(entry)
            ui.element("div").style("min-width:1em")
            with ui.row().classes("items-center gap-1 wrap"):
                badges = _tags(entry)

        # 2) ORIGINAL placeholder, the five-frame strip, the rendered-clip box
        with (
            ui.element("div").style("width:100%;overflow-x:auto"),
            ui.row().classes("items-start gap-3 no-wrap"),
        ):
            _original_box(entry)
            strip = _strip()
            _render_box(entry)

        # 3) the top-right selection corner (faint tick → solid + teal border when queued)
        _selection_control(entry, card)

        if can_decode and entry.has_video:
            observe_row(
                load=lambda: schedule(_host(), lambda: _populate(entry, strip, badges)),
                unload=lambda: _clear_strip(strip),
            )

    if can_decode and entry.has_video:
        schedule(_host(), lambda: _populate(entry, strip, badges))
    else:
        _settle_unknown(badges)


def _clear_strip(strip: list[ui.element]) -> None:
    """Drop the decoded frames, leaving the grey placeholder slots they started as."""
    for slot in strip:
        if _alive(slot):
            slot.clear()


# ── quick-selection grid ─────────────────────────────────────────────────────


#: Translucent tint (SUCCESS teal) laid over a selected grid box.
_GRID_TINT = "rgba(42,157,143,0.22)"


def _grid_box(entry: VideoEntry, *, can_decode: bool) -> None:
    """Render one video as a dense grid box: middle frame, row# badge, select state.

    Clicking the box toggles "Add to Annotator" exactly like the row control does,
    and its selected state is painted clearly: an inset ring, a tint, and a check
    badge. The state is read fresh from the store on every (re)build, so returning
    from the Annotator — where a video may have been dequeued — shows only the videos
    still queued as selected. The middle frame decodes lazily and is dropped on a
    short off-screen delay, decoding again when the box scrolls back; the box keeps
    its fixed size, so nothing reflows.
    """
    width, height = _grid_dims()
    review = state.review_state(entry.key)
    selected = {"value": review.in_annotator}

    box = (
        ui.element("div")
        .classes("relative cursor-pointer")
        .style(f"width:{width}px;height:{height}px;{pending.MEDIA_BOX}")
    )
    with box:
        slot = ui.element("div").style(
            "position:absolute;inset:0;display:flex;align-items:center;justify-content:center"
        )
        if not entry.has_video:
            with slot:
                ui.icon("videocam_off", color=theme.NEUTRAL).tooltip("video file not found")
        # The selection ring/tint sits above the frame so it is never hidden by it.
        overlay = ui.element("div").style(
            "position:absolute;inset:0;border-radius:8px;pointer-events:none;z-index:3"
        )
        check = ui.icon("check_circle", color=theme.SUCCESS).style(
            "position:absolute;top:2px;right:2px;z-index:5;font-size:22px;"
            "background:white;border-radius:50%"
        )
        ui.badge(f"#{entry.row_id}", color=theme.NEUTRAL).style(
            "position:absolute;top:2px;left:2px;z-index:5;opacity:0.85"
        )
        # The video id, mirrored bottom-left; truncated to the box, full name on hover.
        ui.badge(entry.label, color=theme.NEUTRAL).style(
            "position:absolute;bottom:2px;left:2px;z-index:5;opacity:0.85;"
            "max-width:calc(100% - 4px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
        )
        ui.tooltip(entry.label).props("delay=400")

        def paint() -> None:
            if selected["value"]:
                overlay.style(f"box-shadow:inset 0 0 0 4px {theme.SUCCESS};background:{_GRID_TINT}")
                check.set_visibility(True)
            else:
                overlay.style("box-shadow:none;background:transparent")
                check.set_visibility(False)

        def toggle() -> None:
            selected["value"] = not selected["value"]
            state.store.set_annotate(entry.key, entry.video_id, None, selected["value"])
            annotator.update_availability()
            paint()

        box.on("click", lambda: toggle())
        paint()

        if can_decode and entry.has_video:
            observe_row(
                load=lambda: schedule(_host(), lambda: _populate_grid(entry, slot)),
                unload=lambda: _clear_grid(slot),
                delay=state.ui.grid_unload_after_seconds,
            )

    if can_decode and entry.has_video:
        schedule(_host(), lambda: _populate_grid(entry, slot))


async def _populate_grid(entry: VideoEntry, slot: ui.element) -> None:
    """Decode the middle annotated frame into a grid box (guarded like _populate)."""
    if not _alive(slot):
        return
    try:
        await slot.client.connected()  # the task may start before the socket connects
    except Exception:  # noqa: BLE001 - client never connected / already gone
        return

    width, height = _grid_dims()
    pending.pending(slot)
    try:
        result = await state.previews.tile(entry, (width, height), wanted=lambda: _alive(slot))
    except Exception as exc:  # noqa: BLE001 - a bad/missing file must not break the box
        pending.failed(slot, report_decode_failure(entry.video_path, exc))
        return
    if not isinstance(result, Tile):
        return  # the box was refreshed away before its turn
    state.frames_cache[entry.key] = result.num_frames
    pending.ready(slot, lambda: ui.image(result.uri).style(f"width:{width}px;height:{height}px"))


def _clear_grid(slot: ui.element) -> None:
    """Drop a grid box's decoded frame, revealing the grey placeholder box beneath."""
    if _alive(slot):
        slot.clear()


# ── filter bar ───────────────────────────────────────────────────────────────


def _frames_preset() -> str:
    """Derive the frame-filter preset key from the current spec (for persistence)."""
    spec = _state().spec
    if spec.frames == "lt":
        return "lt25" if spec.frames_threshold == 25 else "ltx"
    if spec.frames == "gt":
        return "gt250" if spec.frames_threshold == 250 else "gtx"
    return "any"


def _frames_filter(on_change: Callable[[], None]) -> None:
    """Build the ``# frames`` facet: presets plus a typed threshold ``X``.

    Empty means off, matching every other facet box; the presets are the only choices.
    """
    spec = _state().spec
    options = {"lt25": "< 25", "gt250": "> 250", "ltx": "< X", "gtx": "> X"}
    current = _frames_preset()
    preset = (
        ui.select(
            options,
            value=None if current == "any" else current,
            label="# frames",
            clearable=True,
        )
        .props("dense outlined")
        .classes("min-w-[9rem]")
    )
    x_box = (
        ui.number("X", value=spec.frames_threshold or 100, min=0, step=1)
        .props(DEBOUNCE)
        .classes("w-24")
    )

    def apply() -> None:
        mode = preset.value
        x = int(x_box.value or 0)
        s = _state().spec
        if mode == "lt25":
            s.frames, s.frames_threshold = "lt", 25
        elif mode == "gt250":
            s.frames, s.frames_threshold = "gt", 250
        elif mode == "ltx":
            s.frames, s.frames_threshold = "lt", x
        elif mode == "gtx":
            s.frames, s.frames_threshold = "gt", x
        else:  # cleared → the facet is off
            s.frames = "any"
        x_box.set_visibility(mode in ("ltx", "gtx"))
        on_change()

    preset.on_value_change(lambda _e: apply())
    x_box.on_value_change(lambda _e: apply())
    x_box.set_visibility(preset.value in ("ltx", "gtx"))


def _video_stems() -> set[str]:
    """The scanned video ids, used to auto-suggest an id column and report overlap."""
    if state.scan is None:
        return set()
    return {e.video_id for e in state.scan.entries}


async def _load_id_csv() -> None:
    """Pick a CSV, choose its id column, and restrict Browse to those ids."""
    chosen = await pick_file()
    if not chosen:
        return
    picked = await select_id_column(chosen, _video_stems())
    if picked is None:
        return
    column, ids = picked
    spec = _state().spec
    spec.id_list = set(ids)
    spec.id_source = Path(chosen).name
    spec.id_column = column
    ui.notify(f"Filtering by {len(spec.id_list)} ids from {spec.id_source}", color=theme.PRIMARY)
    _content.refresh()  # the Filter header caption reflects the active id list


def _clear_id_csv() -> None:
    """Drop the CSV id filter, leaving the other facets untouched."""
    spec = _state().spec
    spec.id_list = None
    spec.id_source = ""
    spec.id_column = ""
    _content.refresh()


def _id_csv_control() -> None:
    """The "Ids from CSV" button, or the active-filter chip once a CSV is loaded."""
    spec = _state().spec
    if spec.id_list is None:
        ui.button("Ids from CSV…", icon="upload_file", on_click=_load_id_csv).props(
            "outline dense"
        ).tooltip("List only the videos whose id appears in a column of a CSV file")
        return
    with ui.row().classes("items-center gap-1"):
        ui.badge(
            f"{spec.id_source} · {spec.id_column} · {len(spec.id_list)} ids",
            color=theme.PRIMARY,
        ).tooltip("Browse lists only these ids — click ✕ to drop the CSV filter")
        ui.button(icon="close", on_click=_clear_id_csv).props("flat dense round size=sm").tooltip(
            "Remove the CSV id filter"
        )


def _facet_select(
    attr: str, label: str, options: dict[str, str], on_change: Callable[[], None]
) -> ui.select:
    """One scalar facet dropdown: empty means *off*, like the label-column selects.

    The facets share a single idiom — an unset box filters nothing, and clearing a box
    turns it back off — rather than offering an explicit "any" option that reads as a
    third choice when it really means "no choice". ``"any"`` remains the *stored* off
    value (see :class:`~annie.dataset.filtering.FilterSpec`), so clearing maps back to it.

    Args:
        attr: The :class:`~annie.dataset.filtering.FilterSpec` attribute to drive.
        label: The floating label for the box.
        options: The real choices, excluding the "off" state.
        on_change: Called after the spec is updated, to re-run the filter.

    Returns:
        The select element, so a caller can attach a tooltip.
    """
    current = getattr(_state().spec, attr)
    select = (
        ui.select(
            options,
            value=None if current == "any" else current,
            label=label,
            clearable=True,
        )
        .props("dense outlined")
        .classes("min-w-[9rem]")
    )
    select.on_value_change(lambda e: (_set(attr, e.value or "any"), on_change()))
    return select


def _source_facets() -> set[str]:
    """The vdet/track-dependent facets the configured sources support."""
    registry = state.registry
    return source_facets(has_vdet=registry.vdet is not None, has_track=registry.track is not None)


@client_refreshable
def _filters() -> None:
    """Build the filter controls (rebuilt when a transform changes the facets)."""
    spec = _state().spec
    with ui.row().classes("items-center gap-3 wrap"):
        ui.icon("filter_alt", color=theme.PRIMARY)

        def on_change() -> None:
            _rows.refresh()

        name = (
            ui.input("name starts with", value=spec.name_prefix)
            .props(f"dense outlined clearable {DEBOUNCE}")
            .classes("min-w-[12rem]")
        )
        name.on_value_change(lambda e: (_set("name_prefix", e.value or ""), on_change()))

        facets = _source_facets()
        if "video" in facets:
            video = _facet_select(
                "video",
                "video file",
                {"has": "has video file", "missing": "no video file"},
                on_change,
            )
            with video:
                ui.tooltip("Whether the video file exists; vdet/track files can lack one").props(
                    "delay=600"
                )

        audio = _facet_select(
            "audio", "audio", {"has": "has audio", "missing": "no audio"}, on_change
        )
        with audio:
            ui.tooltip(
                "ffprobe checks each video for an audio stream as its row is viewed; "
                "rows not viewed yet are unknown and hidden by this filter"
            ).props("delay=600")

        if "vdet" in facets:
            _facet_select("vdet", "vdet", {"has": "has vdet", "missing": "no vdet"}, on_change)
        if "tracks" in facets:
            _facet_select(
                "tracks",
                "tracks",
                {"none": "0 tracks", "one": "1 track", "multi": "2+ tracks"},
                on_change,
            )

        _frames_filter(on_change)

        _facet_select("review", "review", {"liked": "liked", "disliked": "disliked"}, on_change)

        note = ui.checkbox("has note", value=spec.has_note)
        note.on_value_change(lambda e: (_set("has_note", e.value), on_change()))
        anno = ui.checkbox("for annotator", value=spec.in_annotator)
        anno.on_value_change(lambda e: (_set("in_annotator", e.value), on_change()))

        ui.element("div").style("flex-basis:100%;height:0")  # force line break

        columns = state.scan.label_columns if state.scan is not None else []
        for column in columns:
            values = _effective_values(column)
            sel = (
                ui.select(
                    values,
                    multiple=True,
                    value=[v for v in spec.labels.get(column, set()) if v in values],
                    label=column,
                )
                .props("dense outlined")
                .classes("min-w-[10rem]")
            )
            sel.on_value_change(lambda e, c=column: (_set_label(c, e.value), on_change()))

        _id_csv_control()
        ui.button("Clear", icon="clear", on_click=_clear).props("flat")


def _set(attr: str, value: object) -> None:
    """Update one scalar facet on the current client's spec."""
    setattr(_state().spec, attr, value)


def _set_label(column: str, values: list[str]) -> None:
    """Update one label facet on the current client's spec."""
    _state().spec.labels[column] = set(values)


def _clear() -> None:
    """Reset every facet and rebuild the bar + rows."""
    _state().spec = FilterSpec()
    _content.refresh()


# ── manipulate block ─────────────────────────────────────────────────────────


def _manip_row(column: str) -> None:
    """One Manipulate row: column · type · transform · threshold / digits · show original.

    The transform always drives the *filter* facets; the "show original" box only decides
    what the sample rows display, so a column can be filtered on ``≥ 0`` while the rows
    still show the underlying float.
    """
    col_type = _column_type(column)
    current = _state().transforms.get(column, manipulate.Transform())
    with ui.row().classes("items-center gap-2 wrap"):
        ui.label(column).classes("text-sm").style("min-width:8rem")
        ui.badge(col_type, color=theme.NEUTRAL)
        kinds = manipulate.transforms_for(col_type)
        kind_sel = ui.select(
            {k: manipulate.TRANSFORM_LABELS[k] for k in kinds}, value=current.kind
        ).props("dense outlined")
        x_box = ui.number("X", value=current.threshold, step=0.5).props(DEBOUNCE).classes("w-24")
        x_box.set_visibility(current.kind == "threshold")
        digits_box = (
            ui.number("Digits", value=current.digits, min=0, max=10, step=1)
            .props(DEBOUNCE)
            .classes("w-24")
        )
        digits_box.set_visibility(current.kind == "round")
        original_box = (
            ui.checkbox("show original", value=current.show_original)
            .props("dense")
            .tooltip("Show the raw value on the rows; the filter still uses the transform")
        )
        original_box.set_visibility(current.kind != "none")

        def apply() -> None:
            kind = kind_sel.value
            _state().transforms[column] = manipulate.Transform(
                kind=kind,
                threshold=float(x_box.value or 0),
                digits=int(digits_box.value if digits_box.value is not None else 2),
                show_original=bool(original_box.value),
            )
            x_box.set_visibility(kind == "threshold")
            digits_box.set_visibility(kind == "round")
            original_box.set_visibility(kind != "none")
            _state().spec.labels.pop(column, None)  # facet values changed; reset filter
            _filters.refresh()
            _rows.refresh()

        kind_sel.on_value_change(lambda _e: apply())
        x_box.on_value_change(lambda _e: apply())
        digits_box.on_value_change(lambda _e: apply())
        original_box.on_value_change(lambda _e: apply())


#: Quasar accordion group shared by the Manipulate / Filter / View panels, so opening
#: one closes the others — the three are wide control surfaces and stacking them all
#: open pushes the sample rows off screen.
_PANEL_GROUP = "browse-panels"


def _manipulate() -> None:
    """Build the per-column transform controls (collapsed by default).

    A transform reshapes a label column's values into something easier to browse by —
    rounding a float, thresholding it into two sides, reducing it to a sign — and the
    filter facets below are built from the result. Each column can independently keep
    showing its original value on the rows (see :func:`_display_label`).
    """
    columns = state.scan.label_columns if state.scan is not None else []
    if not columns:
        return
    with ui.expansion("Manipulate", icon="tune", value=False, group=_PANEL_GROUP).classes("w-full"):
        ui.label(
            "Transform a label column to build better filter options — e.g. round a "
            "float, or threshold it into two sides. Tick 'show original' to keep the "
            "untransformed value on the rows."
        ).classes("text-xs").style(f"color:{theme.NEUTRAL}")
        for column in columns:
            _manip_row(column)


# ── body ─────────────────────────────────────────────────────────────────────


def _filtered_entries() -> list[VideoEntry]:
    """The manifest entries passing this client's filter, in manifest order."""
    if state.scan is None:
        return []
    return apply_filters(
        state.scan.entries,
        _state().spec,
        state.review_lookup(),  # one query for the whole pass, not one per video
        state.audio_cache.get,
        state.frames_cache.get,
        _effective_label,
    )


def _add_all_to_annotator() -> None:
    """Queue every video the filter currently keeps for the Annotator, in one write."""
    entries = _filtered_entries()
    if not entries:
        ui.notify("No videos match the filters.", color=theme.WARNING)
        return
    written = state.store.set_annotate_many(((e.key, e.video_id) for e in entries), value=True)
    annotator.update_availability()
    _rows.refresh()  # repaint the grid's selection rings / the rows' checkboxes
    ui.notify(f"Added {written} video(s) to the Annotator", color=theme.PRIMARY)


def _view() -> None:
    """Build the View panel (collapsed by default): Jump to row, then the mode toggle.

    The Jump card itself is rendered by :func:`paged` into ``jump_slot`` so it stays
    wired to the live pager; here we only reserve its slot, the Detailed/Grid switch
    that flips :attr:`_BrowseState.view_mode`, and the bulk "Add all to Annotator"
    action, which applies to the *filtered* list — not the whole dataset.
    """
    with ui.expansion("View", icon="visibility", value=False, group=_PANEL_GROUP).classes("w-full"):
        _state().jump_slot = ui.column().classes("w-full gap-0")
        with ui.row().classes("items-center gap-2 wrap"):
            ui.label("Quick view").classes("text-sm").style(f"color:{theme.NEUTRAL}")
            toggle = ui.toggle(
                {"detailed": "Detailed", "grid": "Quick selection"},
                value=_state().view_mode,
            ).props("dense")

            def switch(value: str) -> None:
                _state().view_mode = value
                _rows.refresh()

            toggle.on_value_change(lambda e: switch(e.value))

            ui.button(
                "Add all to Annotator", icon="playlist_add", on_click=_add_all_to_annotator
            ).props("outline dense").tooltip("Queue every video matching the current filter")


@client_refreshable
def _rows() -> None:
    """Build the filtered, paged rows (detailed) or boxes (grid)."""
    if state.scan is None:
        return
    slot = _state().jump_slot
    if slot is not None and _alive(slot):
        slot.clear()  # covers the empty-results path, where paged() never runs
    can_decode = media_available()
    entries = _filtered_entries()
    if not entries:
        ui.label("No videos match the filters.").style(f"color:{theme.NEUTRAL}")
        return

    if _state().view_mode == "grid":
        paged(
            entries,
            lambda entry: _grid_box(entry, can_decode=can_decode),
            row_id=lambda entry: entry.row_id,
            total_rows=len(state.scan.entries),
            container=lambda: ui.row().classes("w-full gap-2").style("flex-wrap:wrap"),
            page_size=state.ui.grid_page_size,
            jump_slot=slot,
        )
        return

    paged(
        entries,
        lambda entry: _row_card(entry, can_decode=can_decode),
        row_id=lambda entry: entry.row_id,
        total_rows=len(state.scan.entries),
        jump_slot=slot,
    )


def _jump_to_top() -> None:
    """A floating bottom-right button that scrolls the page back to the top."""
    with ui.page_sticky(position="bottom-right", x_offset=18, y_offset=18):
        ui.button(
            icon="keyboard_arrow_up",
            on_click=lambda: ui.run_javascript("window.scrollTo({top:0,behavior:'smooth'})"),
        ).props("fab-mini color=primary").tooltip("Back to top")


@client_refreshable
def _content() -> None:
    """Build the Browse body: gate, Manipulate, Filter, rows, and jump-to-top."""
    with ui.column().classes("w-full gap-3"):
        if not state.registry.has_video:
            ui.label("Add a videos folder on the Dataset tab to browse.").style(
                f"color:{theme.NEUTRAL}"
            )
            return
        if not media_available():
            ui.label(
                "Install the 'media' extra to see frame thumbnails and rendered clips."
            ).classes("text-xs").style(f"color:{theme.WARNING}")

        drop_facets(_state().spec, _source_facets())  # a removed source must not keep filtering
        _manipulate()
        # The caption keeps a CSV id filter visible while the panel is collapsed.
        spec = _state().spec
        caption = (
            f"ids from {spec.id_source} ({len(spec.id_list)})" if spec.id_list is not None else None
        )
        with ui.expansion(
            "Filter", icon="filter_alt", value=False, caption=caption, group=_PANEL_GROUP
        ).classes("w-full"):
            _filters()
        _view()
        _rows()
        _jump_to_top()


def render() -> None:
    """Build the Browse tab body; initialise and register per-client state."""
    client = context.client
    st = _BrowseState(timer_host=ui.element("div").style("display:none"))
    _browse_state[client.id] = st
    client.on_disconnect(lambda: _browse_state.pop(client.id, None))
    _content()


def refresh(*, everywhere: bool = False) -> None:
    """Rebuild the Browse body (after a scan, source change, or tab open).

    Args:
        everywhere: Rebuild it in every open tab (the dataset itself changed).
    """
    if everywhere:
        _content.refresh_everywhere()
    else:
        _content.refresh()
