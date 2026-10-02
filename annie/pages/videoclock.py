"""Frame-accurate control of one HTML5 ``<video>`` element (UI, browser-gated).

The Event-annotation task drives a single ``ui.video`` from Python: play/pause, seek to a
time or a frame, step frame-by-frame, and jump ±5 s. HTML5 video exposes only a **time**
cursor (``currentTime`` in seconds), not a frame cursor, so every frame operation converts
through the video's ``fps``:

    frame  ->  currentTime = frame / fps + half_frame_nudge
    time   ->  frame       = round(currentTime * fps)

**Documented limitation.** Because seeking is time-based, the landed frame is
``round(currentTime * fps)``. On constant-fps media — which Annie's convert pipeline
produces (see :mod:`annie.media.convert`) — this is exact. On a variable-fps source it can
be off by one frame. That is the accepted trade-off for smooth playback *with audio*, which
a torchcodec frame-by-frame decoder cannot give; an exact confirm is a possible later
refinement, deliberately out of scope here.

This module is a thin bridge: each function returns the JavaScript that acts on the element,
run via :func:`nicegui.ui.run_javascript`. It holds no state — the task owns the model. It
is exercised in the browser, not unit-tested, like the other ``ui.video`` paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from collections.abc import Awaitable

#: A half-frame nudge (in frame units) added when seeking to a frame, so ``currentTime``
#: lands *inside* the target frame rather than exactly on the boundary between it and its
#: predecessor — where a browser may round back to the earlier frame.
_HALF_FRAME = 0.5


def element_id(client_id: str) -> str:
    """Return the stable DOM id given to the task's video element for a client.

    Args:
        client_id: The NiceGUI client id (``context.client.id``).

    Returns:
        A DOM id unique to that client's Event-annotation video.
    """
    return f"annie-event-video-{client_id}"


def _video_js(element_id_: str, body: str) -> str:
    """Wrap ``body`` so it runs only once the element exists (``v`` is the video)."""
    return f"const v = document.getElementById('{element_id_}'); if (v) {{ {body} }}"


def play(element_id_: str) -> None:
    """Start playback of the video element."""
    ui.run_javascript(_video_js(element_id_, "v.play();"))


def pause(element_id_: str) -> None:
    """Pause the video element."""
    ui.run_javascript(_video_js(element_id_, "v.pause();"))


def toggle(element_id_: str) -> None:
    """Toggle play/pause on the video element."""
    ui.run_javascript(_video_js(element_id_, "if (v.paused) { v.play(); } else { v.pause(); }"))


def seek_seconds(element_id_: str, seconds: float) -> None:
    """Seek the video to an absolute time in seconds (clamped at zero).

    Args:
        element_id_: The video element's DOM id.
        seconds: The target time; negative values clamp to the start.
    """
    target = max(0.0, seconds)
    ui.run_javascript(_video_js(element_id_, f"v.currentTime = {target};"))


def seek_frame(element_id_: str, frame: int, fps: float) -> None:
    """Seek the video to a specific frame via its time, with a half-frame nudge.

    Args:
        element_id_: The video element's DOM id.
        frame: The target frame index (clamped at zero).
        fps: Frames per second; a non-positive fps seeks to the start.
    """
    if fps <= 0:
        seek_seconds(element_id_, 0.0)
        return
    target = max(0, frame)
    seconds = (target + _HALF_FRAME) / fps
    ui.run_javascript(_video_js(element_id_, f"v.pause(); v.currentTime = {seconds};"))


def step_frames(element_id_: str, delta: int, fps: float) -> None:
    """Pause and move the cursor by ``delta`` frames (positive forward, negative back).

    Args:
        element_id_: The video element's DOM id.
        delta: Number of frames to move; sign gives the direction.
        fps: Frames per second; a non-positive fps is a no-op.
    """
    if fps <= 0:
        return
    offset = delta / fps
    ui.run_javascript(
        _video_js(
            element_id_,
            f"v.pause(); v.currentTime = Math.max(0, v.currentTime + ({offset}));",
        )
    )


def nudge_seconds(element_id_: str, delta: float) -> None:
    """Jump the cursor by ``delta`` seconds (the ±5 s buttons), clamped at the start.

    Args:
        element_id_: The video element's DOM id.
        delta: Seconds to move; sign gives the direction.
    """
    ui.run_javascript(
        _video_js(element_id_, f"v.currentTime = Math.max(0, v.currentTime + ({delta}));")
    )


def current_time(element_id_: str) -> Awaitable[float]:
    """Read the video's current time in seconds from the browser.

    Used when ``I``/``O`` capture the playhead: the caller awaits this, converts to a frame,
    and writes the event boundary. Returns ``0.0`` if the element is gone.

    Args:
        element_id_: The video element's DOM id.

    Returns:
        An awaitable resolving to the current time in seconds.
    """
    js = f"const v = document.getElementById('{element_id_}'); return v ? v.currentTime : 0.0;"
    return ui.run_javascript(js)


def set_playhead_x(timeline_id: str, x_fraction: float | None) -> None:
    """Move the timeline playhead line to a horizontal fraction without a full re-render.

    The playhead is a single SVG line inside the timeline element; the poll updates only its
    position so playback stays cheap (no server round-trip re-draw of the event boxes).

    Args:
        timeline_id: DOM id of the timeline's SVG element.
        x_fraction: Playhead position as a fraction ``[0, 1]`` of the visible window, or
            ``None`` when the playhead is outside it — the line is then hidden, never pinned
            to an edge.
    """
    if x_fraction is None:
        action = "line.style.display = 'none';"
    else:
        percent = min(1.0, max(0.0, x_fraction)) * 100.0
        action = (
            f"line.style.display = ''; line.setAttribute('x1', '{percent:.3f}%');"
            f" line.setAttribute('x2', '{percent:.3f}%');"
        )
    js = (
        f"const t = document.getElementById('{timeline_id}');"
        f" if (t) {{ const line = t.querySelector('.annie-playhead');"
        f" if (line) {{ {action} }} }}"
    )
    ui.run_javascript(js)


def set_scrollbar(scroll_id: str, left: float, width: float) -> None:
    """Move the pan scrollbar's thumb without rebuilding it (so a drag is not interrupted).

    Args:
        scroll_id: DOM id of the scrollbar track.
        left: The thumb's left edge as a fraction of the track.
        width: The thumb's width as a fraction of the track.
    """
    js = (
        f"const s = document.getElementById('{scroll_id}');"
        f" if (s) {{ const th = s.querySelector('.annie-thumb');"
        f" if (th) {{ th.style.left = '{left * 100.0:.3f}%';"
        f" th.style.width = '{width * 100.0:.3f}%'; }} }}"
    )
    ui.run_javascript(js)


def set_pending_band(timeline_id: str, start_fraction: float, end_fraction: float) -> None:
    """Resize the amber capture band without a full re-render, while a start is armed.

    Mirrors :func:`set_playhead_x`: the band is one ``.annie-pending`` rect, and the poll
    updates only its ``x``/``width`` from two ``[0, 1]`` fractions so it grows with the
    playhead cheaply. Fractions are ordered and clamped, so a playhead left of the start still
    yields a valid (leftward) band.

    Args:
        timeline_id: DOM id of the timeline's SVG element.
        start_fraction: The band's start edge as a fraction of the timeline width.
        end_fraction: The band's end edge (the live playhead) as a fraction of the width.
    """
    lo = min(1.0, max(0.0, min(start_fraction, end_fraction))) * 100.0
    hi = min(1.0, max(0.0, max(start_fraction, end_fraction))) * 100.0
    js = (
        f"const t = document.getElementById('{timeline_id}');"
        f" if (t) {{ const band = t.querySelector('.annie-pending');"
        f" if (band) {{ band.setAttribute('x', '{lo:.3f}%');"
        f" band.setAttribute('width', '{hi - lo:.3f}%'); }} }}"
    )
    ui.run_javascript(js)
