"""Annotator tab — focused main-character review for the queued videos.

The tab is greyed out until at least one video is checked "Add to Annotator" on
the Browse tab. Opening it shows only those queued videos, in responsive rows. For
each video:

* the thumbnail and five-frame strip show the current main-character track in green
  (click the thumbnail to play the original clip);
* picking a different track in the **Main character** selector re-renders the strip
  immediately and **auto-saves** the choice to the ``_manual`` CSV — there is no
  Save button; selection *is* the save;
* the option matching the value in the source CSV is marked ``(default)`` so it is
  obvious which track the heuristic chose;
* a **render** box burns the full clip with the chosen track green so the choice can
  be double-checked across the whole video.

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
from annie.pages.utils import _alive
from annie.parsers.participants import load_participants

if TYPE_CHECKING:
    from pathlib import Path

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
    """The persistent timer host for the current client (created in :func:`render`)."""
    host = _timer_hosts.get(context.client.id)
    assert host is not None, "annotator.render() must run before scheduling timers"
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


def _mc_source() -> tuple[Path, str, str] | None:
    """Return the main-character ``(path, key_column, value_column)``, if configured."""
    source = state.registry.main_character
    if source is None or source.key_column is None or not source.value_columns:
        return None
    return source.path, source.key_column, source.value_columns[0]


def _export() -> None:
    """Export the resolved main-character datasource next to its source file."""
    resolved = _mc_source()
    if resolved is None:
        ui.notify("Add a main-character CSV on the Dataset tab first.", color=theme.WARNING)
        return
    path, key_column, value_column = resolved
    out = path.with_name(f"{path.stem}_resolved{path.suffix}")
    corrections.export_corrected(out, path, key_column, value_column)
    ui.notify(f"Exported corrected main-character CSV to {out}", color=theme.PRIMARY)


def _persist(video_id: str, track_id: int) -> None:
    """Auto-save a main-character choice to the ``_manual`` CSV, if a source exists."""
    resolved = _mc_source()
    if resolved is None:
        ui.notify(
            "Add a main-character CSV on the Dataset tab to save changes.", color=theme.WARNING
        )
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
        thumbnail, frames, _ = await run.io_bound(build_preview, entry)
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
    if not _alive(thumb):
        return  # the page was reloaded/closed during the decode
    try:
        thumb.clear()
        with thumb:
            img = ui.image(to_data_uri(thumbnail)).style(f"{_IMG};cursor:pointer")
            img.on("click", lambda _: _play_original(entry, thumb))
        for slot, frame in zip(strip, frames, strict=False):
            slot.clear()
            with slot:
                ui.image(to_data_uri(frame)).style(_IMG)
    except RuntimeError:
        return  # the row was refreshed away mid-decode


def _play_original(entry: VideoEntry, thumb: ui.element) -> None:
    """Swap a populated thumbnail for the embedded original clip."""
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
        pass


async def _watch_render(job_id: str, box: ui.element) -> None:
    """Poll an annotator render job and embed the clip when it's done."""
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

    The main-character CSV can name a track id that has no track file on disk (or a
    different one than the heuristic), which must still be a valid, visible option —
    otherwise ``ui.select`` raises ``ValueError: Invalid value``.
    """
    ids = [NO_ACTIVE_TRACK, *entry.track_ids]
    for extra in (entry.active_track_id, default_tid):
        if extra not in ids:
            ids.append(extra)
    return {tid: _option_label(tid, default_tid, entry.track_ids) for tid in ids}


def _row_card(entry: VideoEntry, *, can_decode: bool, default_tid: int) -> None:
    """Render one Annotator row with an auto-saving main-character selector."""
    selected = {"track": entry.active_track_id}

    with ui.card().classes("w-full"):
        ui.label(entry.label).classes("font-medium break-all")
        with ui.row().classes("w-full items-stretch gap-2 no-wrap"):
            thumb = ui.element("div").style(_box_style())
            with thumb:
                ui.icon("hourglass_empty" if can_decode else "videocam_off", color=theme.NEUTRAL)
            strip: list[ui.element] = [ui.element("div").style(_box_style()) for _ in range(5)]
            render_box = ui.column().classes("items-center justify-center").style(_box_style())

        def repopulate() -> None:
            preview_entry = replace(entry, active_track_id=selected["track"])
            schedule(_host(), lambda: _populate(preview_entry, thumb, strip))

        def do_render() -> None:
            render_box.clear()
            with render_box:
                ui.spinner(size="lg")
            preview_entry = replace(entry, active_track_id=selected["track"])
            job_id = state.renderer.submit(preview_entry)
            background_tasks.create(_watch_render(job_id, render_box), name="annie-anno-render")

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
                ui.select(options, value=entry.active_track_id, label="Main character")
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
        resolved = _mc_source()
        defaults = load_participants(*resolved) if resolved is not None else {}

        can_decode = media_available()
        for entry in entries:
            _row_card(
                entry,
                can_decode=can_decode,
                default_tid=defaults.get(entry.video_id, NO_ACTIVE_TRACK),
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
