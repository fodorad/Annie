"""Annotator tab — focused protagonist review for the queued videos.

The tab is greyed out until at least one video is checked "Add to Annotator" on
the Browse tab. Opening it shows only those queued videos, in responsive rows. For
each video:

* the thumbnail and five-frame strip show the current protagonist track in green
  (click the thumbnail to play the original clip);
* picking a different track in the **Protagonist** selector re-renders the strip
  immediately and **auto-saves** the choice to the ``_manual`` CSV — there is no
  Save button; selection *is* the save;
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

The corrected datasource can be exported to a standalone CSV at any time.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from nicegui import background_tasks, context, run, ui

from annie.core import logbook, theme
from annie.core.models import NO_ACTIVE_TRACK
from annie.core.state import state
from annie.dataset import corrections
from annie.media.decode import media_available
from annie.media.preview import build_preview, to_data_uri
from annie.media.rendering import JobStatus
from annie.pages.lazy import schedule
from annie.pages.paging import paged
from annie.pages.utils import _alive, notify_detached, unembed_after_idle
from annie.pages.viewport import observe_row
from annie.parsers.participants import load_participants

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from PIL.Image import Image

    from annie.core.models import VideoEntry

#: The Annotator tab element, so its enabled/disabled state can be toggled.
_tab: ui.tab | None = None
#: Shared CSS for the grey, centred media placeholder box on each Annotator row.
_BOX = "border-radius:8px;background:#e5e7eb;display:flex;align-items:center;justify-content:center"

#: Per-client timer hosts, keyed by client id; cleaned up on disconnect in :func:`render`.
_timer_hosts: dict[str, ui.element] = {}

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


def sync_tab() -> None:
    """Enable the tab only when at least one video is queued (safe at build time)."""
    if _tab is not None:
        if _queued_entries():
            _tab.props(remove="disable")
        else:
            _tab.props(add="disable")


def update_availability() -> None:
    """Sync the tab state and rebuild the body (runtime; needs the event loop)."""
    sync_tab()
    _content.refresh()


def _box_style() -> str:
    """Style for one responsive media box: flexes to share the row width.

    The boxes (thumbnail + five frames + render) split the available width equally
    and shrink to fit the viewport, so a full row never overflows horizontally. The
    Settings row-height acts as a **max height** cap on very wide screens.
    """
    cap = state.ui.annotator_row_height
    return f"flex:1 1 0;min-width:0;aspect-ratio:16/9;max-height:{cap}px;{_BOX}"


def _protagonist_source() -> tuple[Path, str, str] | None:
    """Return the protagonist ``(path, key_column, value_column)``, if configured."""
    source = state.registry.protagonist
    if source is None or source.key_column is None or not source.value_columns:
        return None
    return source.path, source.key_column, source.value_columns[0]


def _export() -> None:
    """Export the resolved protagonist datasource next to its source file."""
    resolved = _protagonist_source()
    if resolved is None:
        ui.notify("Add a protagonist CSV on the Dataset tab first.", color=theme.WARNING)
        return
    path, key_column, value_column = resolved
    out = path.with_name(f"{path.stem}_resolved{path.suffix}")
    corrections.export_corrected(out, path, key_column, value_column)
    ui.notify(f"Exported corrected protagonist CSV to {out}", color=theme.PRIMARY)


def _persist(video_id: str, track_id: int) -> None:
    """Auto-save a protagonist choice to the ``_manual`` CSV, if a source exists."""
    resolved = _protagonist_source()
    if resolved is None:
        ui.notify("Add a protagonist CSV on the Dataset tab to save changes.", color=theme.WARNING)
        return
    path, key_column, value_column = resolved
    corrections.set_active_track(video_id, track_id, path, key_column, value_column)


async def _populate(entry: VideoEntry, thumb: ui.element, strip: list[ui.element]) -> None:
    """Decode and draw the strip for ``entry`` (with its current active track)."""
    try:
        await thumb.client.connected()  # the task may start before the socket connects
    except Exception:  # noqa: BLE001 - client never connected / already gone
        return
    try:
        result = await run.io_bound(build_preview, entry)
    except Exception:  # noqa: BLE001 - a bad/missing file must not break the row
        if not _alive(thumb):
            return
        try:
            thumb.clear()
            with thumb:
                ui.icon("broken_image", color=theme.DANGER)
        except RuntimeError:
            pass
        return
    if result is None:
        return  # NiceGUI's io_bound yields None while the app is shutting down
    thumbnail, frames, _ = result
    if not _alive(thumb):
        return  # the page was reloaded/closed during the decode
    box = _media_box()
    try:
        _draw_thumbnail(entry, thumb, thumbnail, box)
        for slot, frame in zip(strip, frames, strict=False):
            slot.clear()
            with slot:
                ui.image(to_data_uri(frame, box)).style(_IMG)
    except RuntimeError:
        return  # the row was refreshed away mid-decode


def _media_box() -> tuple[int, int]:
    """The ``(width, height)`` an Annotator media box occupies at its 16:9 cap."""
    height = state.ui.annotator_row_height
    return round(height * 16 / 9), height


def _draw_thumbnail(
    entry: VideoEntry, thumb: ui.element, image: Image, box: tuple[int, int]
) -> None:
    """Fill ``thumb`` with the clickable static thumbnail (the cheap placeholder)."""
    thumb.clear()
    with thumb:
        img = ui.image(to_data_uri(image, box)).style(f"{_IMG};cursor:pointer")
        img.on("click", lambda _: _play_original(entry, thumb, image, box))


def _play_original(
    entry: VideoEntry, thumb: ui.element, image: Image, box: tuple[int, int]
) -> None:
    """Swap a populated thumbnail for the embedded original clip.

    The embed reverts to the thumbnail once idle, so playing many clips while
    reviewing does not accumulate buffered video in the tab.
    """
    if entry.video_path is None:
        return
    if not entry.video_path.exists():
        logbook.report(f"Video file not found: {entry.video_path}")
        ui.notify(f"File not found: {entry.video_path.name}", color=theme.DANGER)
        return
    try:
        thumb.clear()
        with thumb:
            ui.video(entry.video_path, autoplay=True).style(_IMG)
    except RuntimeError:
        return
    unembed_after_idle(thumb, lambda: _draw_thumbnail(entry, thumb, image, box))


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
                unembed_after_idle(box, restore)
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
            thumb = ui.element("div").style(_box_style())
            with thumb:
                ui.icon("hourglass_empty" if can_decode else "videocam_off", color=theme.NEUTRAL)
            strip: list[ui.element] = [ui.element("div").style(_box_style()) for _ in range(5)]
            render_box = ui.column().classes("items-center justify-center").style(_box_style())

        def repopulate() -> None:
            preview_entry = replace(entry, active_track_id=selected["track"])
            schedule(_host(), lambda: _populate(preview_entry, thumb, strip))

        def unload_media() -> None:
            """Drop the decoded frames, restoring the placeholders the row started as."""
            for slot in strip:
                if _alive(slot):
                    slot.clear()
            if _alive(thumb):
                thumb.clear()
                with thumb:
                    ui.icon("hourglass_empty", color=theme.NEUTRAL)

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
                _persist(entry.video_id, value)  # auto-save; no Save button
                repopulate()  # redraw the strip with the new green track
                reset_render()  # the rendered clip is now stale

            picker.on_value_change(lambda e: on_pick(e.value))

        if not entry.has_track:
            ui.label("No tracks for this video.").classes("text-xs").style(f"color:{theme.NEUTRAL}")

        if can_decode and entry.has_video:
            observe_row(load=repopulate, unload=unload_media)

    if can_decode and entry.has_video:
        repopulate()


@ui.refreshable
def _content() -> None:
    """Build the Annotator body from the queued videos."""
    with ui.column().classes("w-full gap-3"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Annotator").classes("text-xl font-medium")
            ui.button("Export corrected CSV", icon="download", on_click=_export).props("flat")

        entries = _queued_entries()
        if not entries:
            ui.label(
                "No videos queued. Check 'Add to Annotator' on Browse rows to review them here."
            ).style(f"color:{theme.NEUTRAL}")
            return
        if not media_available():
            ui.label("Install the 'media' extra to see and re-render frames.").classes(
                "text-xs"
            ).style(f"color:{theme.WARNING}")

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
            actions=_clear_all_button,
        )


def render() -> None:
    """Build the Annotator tab body; register per-client timer host."""
    client = context.client
    _timer_hosts[client.id] = ui.element("div").style("display:none")
    client.on_disconnect(lambda: _timer_hosts.pop(client.id, None))
    _content()


def refresh() -> None:
    """Rebuild the Annotator body (after queueing changes or a tab open)."""
    _content.refresh()
