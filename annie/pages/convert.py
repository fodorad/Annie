"""Convert tab — re-encode audio and video to a consistent, validated form.

Two sections: **Audio** (optional) re-encodes audio to a uniform format / sample
rate / channel count; **Video** re-encodes to constant-frame-rate H.264 and
validates each output with torchcodec (decode every frame, exact == approximate
count) so the dataset never trips the broken-seeking class of bug downstream.

Audio/video combination is explicit: mux the matching-stem audio into each video,
or keep videos frame-only; and an audio file with no matching video can become a
black-frame video carrying that audio, or be left as audio-only. A shared progress
panel shows ``X/Y`` with a bar, elapsed time, and an ETA while the background batch
runs.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from nicegui import background_tasks, ui

from annie.core import theme
from annie.core.state import state
from annie.media import convert
from annie.pages.folder_picker import pick_directory

#: Widgets of the shared progress panel, rebuilt per page render.
_bar: ui.linear_progress | None = None
_title: ui.label | None = None  #: current-file / status line
_counts: ui.label | None = None  #: ``X/Y`` processed count
_times: ui.label | None = None  #: elapsed / ETA readout
_failed: ui.label | None = None  #: failure count, shown when non-zero
_cancel: ui.button | None = None  #: cancels the running batch


def _fmt_duration(seconds: float) -> str:
    """Format a duration as ``1h 2m`` / ``2m 3s`` / ``3s``."""
    total = int(seconds)
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def _dir_input(label: str, value: str = "") -> ui.input:
    """A folder text field with a Browse button; returns the input element."""
    with ui.row().classes("w-full items-center gap-2"):
        button = ui.button(icon="folder_open").props("flat round").tooltip(f"Browse for {label}")
        field = ui.input(label, value=value).classes("flex-grow")

    async def choose() -> None:
        chosen = await pick_directory(field.value)
        if chosen:
            field.set_value(chosen)

    button.on_click(choose)
    return field


# ── progress panel ───────────────────────────────────────────────────────────


async def _export_succeeded() -> None:
    """Pick a folder and export the converted ids to ``reencoded_ids.csv``."""
    succeeded = state.converter.progress().succeeded
    if not succeeded:
        ui.notify("No converted files to export yet.", color=theme.WARNING)
        return
    chosen = await pick_directory()
    if not chosen:
        return
    out = convert.export_ids(Path(chosen) / "reencoded_ids.csv", succeeded)
    ui.notify(f"Exported {len(succeeded)} id(s) to {out}", color=theme.PRIMARY)


async def _export_failed() -> None:
    """Pick a folder and export the failed ids + errors to ``failed_ids.csv``."""
    failed = state.converter.progress().failed
    if not failed:
        ui.notify("No failed files to export.", color=theme.WARNING)
        return
    chosen = await pick_directory()
    if not chosen:
        return
    out = convert.export_failures(Path(chosen) / "failed_ids.csv", failed)
    ui.notify(f"Exported {len(failed)} failed id(s) to {out}", color=theme.PRIMARY)


def _build_progress() -> None:
    """Build the shared progress panel and render the current state into it."""
    global _bar, _title, _counts, _times, _failed, _cancel
    with ui.card().classes("w-full gap-1"):
        _title = ui.label().classes("font-medium")
        _bar = ui.linear_progress(value=0.0, show_value=False).props("rounded")
        _counts = ui.label().classes("text-sm")
        _times = ui.label().classes("text-xs").style(f"color:{theme.NEUTRAL}")
        _failed = ui.label().classes("text-xs").style(f"color:{theme.DANGER}")
        with ui.row().classes("items-center gap-2"):
            _cancel = ui.button("Cancel", icon="stop", on_click=state.converter.cancel).props(
                "flat"
            )
            ui.button("Export reencoded ids", icon="download", on_click=_export_succeeded).props(
                "flat"
            )
            ui.button("Export failed ids", icon="download", on_click=_export_failed).props("flat")
    _render_progress()


def _render_progress() -> None:
    """Update the progress widgets from the converter's current snapshot."""
    if _bar is None or _title is None or _counts is None or _times is None:
        return
    if _failed is None or _cancel is None:
        return
    p = state.converter.progress()

    if p.status == "idle":
        _title.set_text("No conversion running.")
        _bar.set_value(0.0)
        _counts.set_text("")
        _times.set_text("")
        _failed.set_text("")
        _cancel.set_visibility(False)
        return

    _title.set_text(f"{p.title} — {p.status}")
    _bar.set_value(p.fraction)
    _counts.set_text(
        f"{p.done}/{p.total}  ({p.fraction * 100:.0f}%)"
        f"   ·   converted {p.succeeded_count}   ·   failed {len(p.failed)}"
        + (f"   ·   {p.current}" if p.current else "")
    )

    parts: list[str] = []
    if p.started_at is not None:
        parts.append(f"started {datetime.fromtimestamp(p.started_at):%H:%M:%S}")
    parts.append(f"elapsed {_fmt_duration(p.elapsed)}")
    eta = p.eta_seconds
    if eta is not None:
        finish = datetime.now() + timedelta(seconds=eta)
        parts.append(f"~{_fmt_duration(eta)} left (≈{finish:%H:%M:%S})")
    _times.set_text("  ·  ".join(parts))

    if p.failed:
        names = ", ".join(name for name, _ in p.failed[:5])
        more = "…" if len(p.failed) > 5 else ""
        _failed.set_text(f"{len(p.failed)} failed: {names}{more}")
    else:
        _failed.set_text("")
    _cancel.set_visibility(p.status == "running")


async def _watch() -> None:
    """Poll the converter and refresh the progress panel until the batch ends."""
    while True:
        try:
            _render_progress()
        except RuntimeError:
            return  # the panel was rebuilt/refreshed away
        if state.converter.progress().status != "running":
            return
        await asyncio.sleep(0.4)


def _start(title: str, items: list, work) -> None:  # noqa: ANN001 - work is a closure
    """Begin a batch (guarding empties / a running job) and launch the watcher."""
    if state.converter.running():
        ui.notify("A conversion is already running.", color=theme.WARNING)
        return
    if not items:
        ui.notify("Nothing to convert — check the input/output folders.", color=theme.WARNING)
        return
    state.converter.start(title, items, work)
    background_tasks.create(_watch(), name="annie-convert-watch")
    ui.notify(f"{title}: converting {len(items)} file(s)…", color=theme.PRIMARY)


# ── sections ─────────────────────────────────────────────────────────────────


def _audio_section() -> ui.input:
    """Build the Audio section; returns its output-folder input (for video muxing)."""
    with ui.card().classes("w-full gap-2"):
        ui.label("Audio").classes("text-lg font-medium")
        ui.label("Optional — re-encode audio to a uniform format, rate, channels.").classes(
            "text-xs"
        ).style(f"color:{theme.NEUTRAL}")
        in_dir = _dir_input("Audio input folder")
        out_dir = _dir_input("Audio output folder")
        with ui.row().classes("items-center gap-3 wrap"):
            fmt = ui.select(
                list(convert.AUDIO_OUTPUT_FORMATS), value="wav", label="Format"
            ).classes("w-32")
            sr = ui.number("Sample rate (Hz)", value=16000, min=1000, step=1000).classes("w-40")
            ch = ui.select({1: "mono", 2: "stereo"}, value=1, label="Channels").classes("w-32")

        def start() -> None:
            opts = convert.AudioOptions(
                out_format=str(fmt.value),
                sample_rate=int(sr.value or 16000),
                channels=int(ch.value),
            )
            items = convert.plan_audio(in_dir.value or None, out_dir.value, opts)
            _start("Audio", items, lambda item: convert.convert_audio_file(item, opts))

        ui.button("Convert audio", icon="graphic_eq", on_click=start).props("unelevated")
    return out_dir


def _video_section(audio_out: ui.input) -> None:
    """Build the Video section (combine policy included)."""
    with ui.card().classes("w-full gap-2"):
        ui.label("Video").classes("text-lg font-medium")
        ui.label(
            "Re-encode to constant-frame-rate H.264 and validate every output with torchcodec."
        ).classes("text-xs").style(f"color:{theme.NEUTRAL}")
        in_dir = _dir_input("Video input folder")
        out_dir = _dir_input("Video output folder")

        ui.label("Resolution").classes("text-sm font-medium mt-1")
        keep_res = ui.checkbox("Keep original size", value=True)
        with (
            ui.row()
            .classes("items-center gap-3")
            .bind_visibility_from(keep_res, "value", backward=lambda v: not v)
        ):
            width = ui.number("Width", value=None, min=2, step=2).classes("w-32")
            height = ui.number("Height", value=None, min=2, step=2).classes("w-32")
        grayscale = ui.checkbox("Grayscale (black & white)", value=False)

        ui.label("Frame rate").classes("text-sm font-medium mt-1")
        keep_fps = ui.checkbox("Keep original fps", value=True)
        fps = (
            ui.number("Target fps", value=25, min=1, step=1)
            .classes("w-40")
            .bind_visibility_from(keep_fps, "value", backward=lambda v: not v)
        )

        ui.label("Audio").classes("text-sm font-medium mt-1")
        add_audio = ui.checkbox("Add audio track (match by name)", value=True)
        black = ui.checkbox(
            "Audio without a video → black-frame video", value=False
        ).bind_visibility_from(add_audio, "value")
        audio_src = _dir_input("Audio folder to mux (defaults to the audio output folder)")

        ui.label("Validation").classes("text-sm font-medium mt-1")
        min_frames = ui.number("Minimum output frames", value=2, min=1, step=1).classes("w-48")
        with min_frames:
            ui.tooltip(
                "Skip a source that yields fewer decodable frames than this. "
                "2 drops audio-only files carrying a single thumbnail frame; 1 only "
                "requires at least one decodable frame."
            ).props("delay=600")

        def start() -> None:
            keep_w = keep_res.value
            opts = convert.VideoOptions(
                width=None if keep_w else (int(width.value) if width.value else None),
                height=None if keep_w else (int(height.value) if height.value else None),
                grayscale=bool(grayscale.value),
                fps=None if keep_fps.value else (float(fps.value) if fps.value else None),
                add_audio=bool(add_audio.value),
                audio_only_black=bool(add_audio.value and black.value),
                min_frames=int(min_frames.value or 2),
            )
            audio_dir = (audio_src.value or audio_out.value) or None
            items = convert.plan_video(in_dir.value or None, out_dir.value, opts, audio_dir)
            _start("Video", items, lambda item: convert.convert_video_file(item, opts))

        ui.button("Convert video", icon="movie", on_click=start).props("unelevated")


def render() -> None:
    """Build the Convert tab body."""
    with ui.column().classes("w-full gap-3"):
        ui.label("Convert").classes("text-xl font-medium")
        ui.label(
            "Re-encode audio/video to a consistent, torchcodec-validated form so previews, "
            "renders, and downstream loaders never hit broken seeking."
        ).classes("text-sm").style(f"color:{theme.NEUTRAL}")
        audio_out = _audio_section()
        _video_section(audio_out)
        _build_progress()
    if state.converter.running():
        background_tasks.create(_watch(), name="annie-convert-watch")
