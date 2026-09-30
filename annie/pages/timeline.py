"""The Event-annotation timeline — an inline-SVG track/event surface (UI).

A video-editor-style timeline: a time ruler, a vertical playhead across every lane, and one
lane per track holding its event boxes. The surface is always the **full width of its
container**; zooming does not widen it — it narrows the *time window* shown, so the ruler
grows denser and boundaries are easier to place. The visible window is
``[view_start_frame, view_end_frame]`` (the task keeps it centred on the playhead); frames
map to x within that window, and the gesture x-fraction maps back through the same window.

Within one track, events that overlap in time are **stacked into sub-rows** rather than drawn
on top of each other, so every event stays clickable; a lane's height grows with how many
events are concurrent at once (see :func:`pack_lanes`).

**Where the logic lives.** Python owns the model and renders the SVG *markup* — this keeps
the geometry testable (:func:`build_svg`, :func:`pack_lanes`, both pure) and mirrors how the
segment-review layout keeps its logic server-side. JavaScript does two things only: it reports
pointer gestures back (carrying the track and an x fraction of the *window*) so Python can
convert them to frames, and it moves the single playhead line during playback without a full
re-render (see :func:`annie.pages.videoclock.set_playhead_x`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING

from annie.core import theme

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Height of one packed sub-row within a lane, in SVG user units.
ROW_HEIGHT = 22
#: Vertical padding inside a lane, above the first sub-row and below the last.
LANE_PAD = 6
#: Height of the ruler strip above the lanes.
RULER_HEIGHT = 22
#: Vertical gap between lanes.
LANE_GAP = 4
#: Fixed drawing width in SVG user units; the element still scales to 100% of its container,
#: so this is the internal coordinate space, not a pixel size on screen.
BASE_WIDTH = 1000
#: Rounded-corner radius of an event box.
EVENT_RADIUS = 4


@dataclass(slots=True, frozen=True)
class TimelineEvent:
    """One event to draw on a lane.

    Attributes:
        event_id: The event's stable id (echoed back on click, to select it).
        start_frame: Inclusive start frame.
        end_frame: Inclusive end frame.
        label: Text drawn inside the box (may be empty).
        selected: Whether to draw this box in the selected style.
        color: Per-event fill colour override (hex), or ``None`` to use the lane's colour.
    """

    event_id: str
    start_frame: int
    end_frame: int
    label: str
    selected: bool = False
    color: str | None = None


@dataclass(slots=True, frozen=True)
class TimelineTrack:
    """One lane: a track name and the events on it.

    Attributes:
        name: The track (category) name, shown as the lane label.
        events: The events to draw, in any order.
        color: Optional lane colour override (hex); ``None`` uses the palette by lane order.
    """

    name: str
    events: Sequence[TimelineEvent]
    color: str | None = None


def pack_lanes(events: Sequence[TimelineEvent]) -> list[int]:
    """Assign each event a sub-row so overlapping events never share one.

    A greedy interval-partitioning pass: events are considered in start-frame order and each
    is placed on the lowest sub-row whose last event has already ended. Two events overlap
    when one starts at or before the other ends (inclusive frames), so touching intervals are
    treated as overlapping and separated — clearer for the reviewer than a 1px seam.

    Args:
        events: The events on one track, in any order.

    Returns:
        A list of sub-row indices, parallel to ``events`` in their *original* order.
    """
    order = sorted(range(len(events)), key=lambda i: (events[i].start_frame, events[i].end_frame))
    row_last_end: list[int] = []  # last end frame occupying each sub-row
    assigned = [0] * len(events)
    for i in order:
        event = events[i]
        placed = False
        for row, last_end in enumerate(row_last_end):
            if event.start_frame > last_end:  # this row is free from here on
                row_last_end[row] = event.end_frame
                assigned[i] = row
                placed = True
                break
        if not placed:
            assigned[i] = len(row_last_end)
            row_last_end.append(event.end_frame)
    return assigned


def _lane_rows(track: TimelineTrack) -> tuple[list[int], int]:
    """Return ``(sub-row per event, sub-row count)`` for a track (min one row)."""
    if not track.events:
        return [], 1
    rows = pack_lanes(track.events)
    return rows, max(rows) + 1


def _lane_height(row_count: int) -> float:
    """Pixel height of a lane holding ``row_count`` stacked sub-rows."""
    return 2 * LANE_PAD + row_count * ROW_HEIGHT


def _tick_step_seconds(span_seconds: float, width: float) -> float:
    """Choose a ruler tick spacing (seconds) giving ~one tick per 90 px of the window.

    Args:
        span_seconds: Seconds visible in the current window.
        width: Rendered timeline width in user units.

    Returns:
        A "nice" tick step in seconds (1, 2, 5, 10, … scaled by powers of ten). Never zero.
    """
    if span_seconds <= 0 or width <= 0:
        return 1.0
    target_ticks = max(1.0, width / 90.0)
    raw = span_seconds / target_ticks
    magnitude = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    for nice in (1, 2, 5, 10):
        if raw <= nice * magnitude:
            return nice * magnitude
    return 10 * magnitude


def _x_of_frame(frame: int, view_start: int, view_end: int, width: float) -> float:
    """Map a frame to an x position within the visible window ``[view_start, view_end]``."""
    span = view_end - view_start
    if span <= 0:
        return 0.0
    return (frame - view_start) / span * width


def build_svg(
    tracks: Sequence[TimelineTrack],
    *,
    view_start: int,
    view_end: int,
    fps: float,
    svg_id: str,
    pending_start: int | None = None,
    pending_end: int | None = None,
) -> str:
    """Render the timeline as an inline-SVG string for the visible frame window.

    Pure and deterministic — no NiceGUI, no DOM — so the geometry (tick placement, event box
    extents, lane stacking) is unit-testable.

    Args:
        tracks: The lanes to draw, top to bottom.
        view_start: First frame shown at the left edge.
        view_end: Last frame shown at the right edge (``> view_start``).
        fps: Frames per second, for converting the window to ruler seconds.
        svg_id: DOM id for the ``<svg>`` (the playhead updater and gesture bridge use it).
        pending_start: While the reviewer is mid-capture (start marked, end not yet), the
            start frame of the growing amber band; ``None`` when not capturing.
        pending_end: The band's right edge — the live playhead frame; defaults to
            ``pending_start`` (a minimal band) when omitted. Ignored if ``pending_start`` is
            ``None``.

    Returns:
        A complete ``<svg>…</svg>`` markup string that scales to its container's width.
    """
    width = float(BASE_WIDTH)
    view_end = max(view_end, view_start + 1)
    lanes_top = RULER_HEIGHT + LANE_GAP

    # Pre-compute each lane's packing and height so the total height is known up front.
    packed = [_lane_rows(track) for track in tracks]
    lane_heights = [_lane_height(row_count) for _, row_count in packed]
    height = lanes_top + sum(h + LANE_GAP for h in lane_heights) + LANE_GAP

    parts: list[str] = [
        f'<svg id="{escape(svg_id)}" xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" preserveAspectRatio="none" '
        f'width="100%" height="{height:.0f}" '
        f'style="font-family:inherit;font-size:11px;display:block">',
        # Hover highlight for event boxes; the selected box is styled inline (below).
        "<style>.annie-event{transition:fill-opacity .1s}"
        ".annie-event:hover{fill-opacity:0.8}</style>",
    ]

    _append_ruler(parts, view_start, view_end, fps, width)

    # The pending band is drawn before the lanes/events so it sits *behind* them. It always
    # carries the annie-pending class so the poll can resize it live without a re-render; when
    # not capturing it is emitted zero-width and invisible so the JS hook still exists.
    _append_pending_band(
        parts, pending_start, pending_end, view_start, view_end, width, lanes_top, height
    )

    y = lanes_top
    for ordinal, track in enumerate(tracks):
        rows, row_count = packed[ordinal]
        lane_h = lane_heights[ordinal]
        colour = track.color or theme.event_track_color(ordinal)
        parts.append(
            f'<rect class="annie-lane" data-track="{escape(track.name)}" '
            f'x="0" y="{y:.0f}" width="{width:.0f}" height="{lane_h:.0f}" '
            f'fill="#fafafa" stroke="#e9ecef" stroke-width="1"/>'
        )
        for i, event in enumerate(track.events):
            _append_event(parts, event, colour, y, rows[i], view_start, view_end, width)
        _append_lane_label(parts, track.name, colour, y)
        y += lane_h + LANE_GAP

    # Playhead — one line the poll moves via set_playhead_x (class is the hook). Its x is set
    # by the caller after render for the current time; it starts at the window's left edge.
    parts.append(
        f'<line class="annie-playhead" x1="0" y1="0" x2="0" y2="{height:.0f}" '
        f'stroke="{theme.EVENT_PLAYHEAD}" stroke-width="2" pointer-events="none"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _append_pending_band(  # noqa: PLR0913 - a drawing routine needs its geometry inputs
    parts: list[str],
    pending_start: int | None,
    pending_end: int | None,
    view_start: int,
    view_end: int,
    width: float,
    lanes_top: float,
    height: float,
) -> None:
    """Draw the amber capture band behind the lanes, from ``pending_start`` to the playhead.

    Always emits a ``<rect class="annie-pending">`` so the poll's cheap JS resize
    (:func:`annie.pages.videoclock.set_pending_band`) has a stable hook; when there is no
    capture in progress the rect is zero-width and invisible. The band spans the lane area
    vertically (below the ruler) and is drawn before the events so it reads as a wash behind
    them.
    """
    band_h = max(0.0, height - lanes_top)
    if pending_start is None:
        parts.append(
            f'<rect class="annie-pending" x="0" y="{lanes_top:.0f}" width="0" '
            f'height="{band_h:.0f}" fill="{theme.EVENT_PENDING}" fill-opacity="0.25" '
            f'pointer-events="none"/>'
        )
        return
    end = pending_start if pending_end is None else pending_end
    lo, hi = (pending_start, end) if pending_start <= end else (end, pending_start)
    x0 = _x_of_frame(lo, view_start, view_end, width)
    x1 = _x_of_frame(hi, view_start, view_end, width)
    parts.append(
        f'<rect class="annie-pending" x="{x0:.1f}" y="{lanes_top:.0f}" '
        f'width="{max(0.0, x1 - x0):.1f}" height="{band_h:.0f}" '
        f'fill="{theme.EVENT_PENDING}" fill-opacity="0.25" pointer-events="none"/>'
    )


def _append_ruler(
    parts: list[str], view_start: int, view_end: int, fps: float, width: float
) -> None:
    """Draw the ruler strip: ticks and second labels across the visible window."""
    parts.append(f'<rect x="0" y="0" width="{width:.0f}" height="{RULER_HEIGHT}" fill="#f1f3f5"/>')
    if fps <= 0:
        return
    start_sec = view_start / fps
    end_sec = view_end / fps
    span = end_sec - start_sec
    step = _tick_step_seconds(span, width)
    # First tick at or after the window start, on a step boundary.
    first = math.ceil(start_sec / step) * step
    t = first
    while t <= end_sec + 1e-6:
        x = (t - start_sec) / span * width
        parts.append(
            f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{RULER_HEIGHT}" '
            f'stroke="#adb5bd" stroke-width="1"/>'
        )
        parts.append(f'<text x="{x + 2:.1f}" y="14" fill="{theme.NEUTRAL}">{t:g}s</text>')
        t += step


def _append_event(  # noqa: PLR0913 - a drawing routine needs its geometry inputs
    parts: list[str],
    event: TimelineEvent,
    colour: str,
    lane_y: float,
    sub_row: int,
    view_start: int,
    view_end: int,
    width: float,
) -> None:
    """Draw one event box (and its label) on its packed sub-row within a lane.

    Events fully outside the visible window are skipped; one that straddles an edge is
    clamped to the window so its visible part still shows and stays clickable.
    """
    if event.end_frame < view_start or event.start_frame > view_end:
        return
    x0 = _x_of_frame(max(event.start_frame, view_start), view_start, view_end, width)
    x1 = _x_of_frame(min(event.end_frame, view_end), view_start, view_end, width)
    box_w = max(2.0, x1 - x0)
    box_y = lane_y + LANE_PAD + sub_row * ROW_HEIGHT
    fill = event.color or colour  # per-event colour overrides the lane's default
    # Every box carries a black outline so a pale pastel fill still reads against the lane;
    # selection just thickens it rather than changing the colour.
    stroke = theme.EVENT_SELECTED
    stroke_w = 2.5 if event.selected else 1.0
    opacity = "0.9" if event.selected else "0.7"
    label = escape(event.label)
    parts.append(
        f'<rect class="annie-event" data-event="{escape(event.event_id)}" '
        f'x="{x0:.1f}" y="{box_y:.0f}" width="{box_w:.1f}" height="{ROW_HEIGHT - 4}" '
        f'rx="{EVENT_RADIUS}" fill="{fill}" fill-opacity="{opacity}" '
        f'stroke="{stroke}" stroke-width="{stroke_w}" style="cursor:pointer">'
        f"<title>{label}</title></rect>"
    )
    if event.label and _label_fits(event.label, box_w):
        # Centred in the box; only drawn when it plausibly fits, so a short box is not
        # overrun by text spilling past its edges (an estimate — see _label_fits).
        parts.append(
            f'<text x="{x0 + box_w / 2:.1f}" y="{box_y + ROW_HEIGHT / 2 + 1:.0f}" '
            f'text-anchor="middle" fill="#1a1a1a" style="pointer-events:none">{label}</text>'
        )


#: Rough width, in user units, of one label character at the 11px timeline font. Used only
#: to decide whether a label fits its box — an over-estimate is safe (it just hides a label
#: that might have squeezed in), which is why it errs generous.
_CHAR_WIDTH = 6.5


def _label_fits(label: str, box_width: float) -> bool:
    """Whether ``label`` plausibly fits inside a box ``box_width`` user units wide.

    A cheap character-count estimate (the server cannot measure real text metrics), with a
    small padding margin. When it does not fit, the caller draws no label and the full text
    is still available via the box's ``<title>`` tooltip.
    """
    return len(label) * _CHAR_WIDTH + 6 <= box_width


def _append_lane_label(parts: list[str], name: str, colour: str, lane_y: float) -> None:
    """Draw the category name at the top-left of a lane, over a small white backing pill.

    Drawn after the lane's events so it stays readable even where an event box starts at the
    very left. The backing keeps the text legible over a coloured box; the text takes the
    lane's colour so Mother / Baby are identifiable at a glance. ``pointer-events:none`` so the
    label never intercepts a click meant for an event or a seek.
    """
    if not name:
        return
    text = escape(name)
    pill_w = len(name) * _CHAR_WIDTH + 10
    parts.append(
        f'<rect x="2" y="{lane_y + 2:.0f}" width="{pill_w:.0f}" height="15" rx="3" '
        f'fill="#ffffff" fill-opacity="0.82" pointer-events="none"/>'
    )
    parts.append(
        f'<text x="7" y="{lane_y + 13:.0f}" fill="{colour}" '
        f'style="pointer-events:none;font-weight:600">{text}</text>'
    )


def gesture_script(svg_id: str, event_name: str) -> str:
    """Return JS that reports a timeline click to the server via ``emitEvent``.

    Attached once after the SVG is embedded. It listens for pointer down/up on the SVG and
    emits ``event_name`` with a ``detail`` of ``{kind, x, ...}`` where ``x`` is a ``[0, 1]``
    fraction of the *visible window*:

    * ``kind = "select"`` when an existing event box is clicked (carries ``event_id``);
    * ``kind = "seek"`` for a click anywhere else, or any drag.

    The timeline is navigation-only — it never creates events — so there is no click/drag
    authoring gesture; boundaries come from the START/END EVENT buttons and the i/o keys.

    Args:
        svg_id: DOM id of the timeline ``<svg>``.
        event_name: The custom event name the NiceGUI handler listens for.

    Returns:
        The JavaScript to run once via ``ui.run_javascript``.
    """
    # Gestures are reported to the server via NiceGUI's emitEvent (a Python ``ui.on`` handler
    # receives them), not a DOM CustomEvent — the latter needed a brittle forward hop through
    # the wrapper element. ``eventBox`` walks up from the pointer target with ``closest`` so a
    # click landing on a box's ``<title>`` child still resolves to the box; capturing the
    # event id at pointerdown makes a click select it even if the pointer twitches a pixel.
    #
    # The timeline is navigation-only: a click on a box selects it, anywhere else is a seek.
    # New events are created solely by the START/END EVENT buttons (and the i/o keys), so
    # their boundaries are exact rather than wherever a click happened to land.
    return f"""
    (() => {{
      const svg = document.getElementById('{svg_id}');
      if (!svg || svg.dataset.annieBound) return;
      svg.dataset.annieBound = '1';
      let downX = null, downEvent = null;
      const frac = (clientX) => {{
        const r = svg.getBoundingClientRect();
        return Math.min(1, Math.max(0, (clientX - r.left) / r.width));
      }};
      const eventBox = (t) => (t && t.closest) ? t.closest('.annie-event') : null;
      svg.addEventListener('pointerdown', (e) => {{
        downX = frac(e.clientX);
        const box = eventBox(e.target);
        downEvent = box ? box.getAttribute('data-event') : null;
      }});
      svg.addEventListener('pointerup', (e) => {{
        if (downX === null) return;
        const upX = frac(e.clientX);
        const moved = Math.abs(upX - downX) > 0.006;
        const detail = (downEvent && !moved)
          ? {{kind: 'select', event_id: downEvent, x: upX}}
          : {{kind: 'seek', track: null, x: upX}};
        emitEvent('{event_name}', detail);
        downX = null; downEvent = null;
      }});
    }})();
    """
