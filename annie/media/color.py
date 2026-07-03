"""Box-drawing colours and frame-overlay rendering (service).

These colours are burned **onto the video frames** and are distinct from the UI
status chrome in :mod:`annie.theme`. Annie renders overlays with Pillow, which
works in **RGB**, so colours here are RGB tuples. The numeric values match the
design spec (e.g. blue ``(0, 0, 255)``, green ``(0, 255, 0)``); only the
colour-space label differs from the spec's BGR note, because Annie does not use
OpenCV for drawing.

Drawing rules (from the design):

1. **vdet-only frame** — every face box is drawn in flat **blue** so it does not
   flicker as detection ordering changes between frames.
2. **tracks available** — each track gets a **stable colour** keyed by
   ``track_id``, drawn identically on every frame. The palette excludes the two
   reserved colours, blue and green.
3. **active / main-character track** — always **green**, overriding its palette
   colour.
4. each box is labelled with its ``track_id`` as a small number at the top-left.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

    from annie.core.models import BBox, FrameAnnotation

BLUE = (0, 0, 255)
"""Raw vdet detections."""
GREEN = (0, 255, 0)
"""The active / main-character track."""

TRACK_PALETTE: tuple[tuple[int, int, int], ...] = (
    (239, 68, 68),  # red
    (249, 115, 22),  # orange
    (234, 179, 8),  # amber
    (168, 85, 247),  # purple
    (236, 72, 153),  # pink
    (20, 184, 166),  # teal
    (132, 204, 22),  # lime
    (217, 70, 239),  # fuchsia
    (251, 146, 60),  # light orange
    (244, 114, 182),  # light pink
)
"""Stable palette for ordinary tracks.

Deliberately excludes blue and green (and anything close to them) so reserved
meanings stay unambiguous. Keyed by ``track_id`` modulo the palette length, so a
track is always the same colour.
"""


def color_for_track(track_id: int, *, active: bool = False) -> tuple[int, int, int]:
    """Return the stable RGB colour for a track.

    The active (main-character) track short-circuits to :data:`GREEN` before the
    palette is consulted; otherwise the colour is a deterministic function of
    ``track_id`` drawn from :data:`TRACK_PALETTE` (never blue or green).

    Args:
        track_id: The track index.
        active: Whether this is the active / main-character track.

    Returns:
        An ``(r, g, b)`` colour tuple.
    """
    if active:
        return GREEN
    return TRACK_PALETTE[track_id % len(TRACK_PALETTE)]


def box_color(box: BBox, *, has_tracks: bool, active_track_id: int | None) -> tuple[int, int, int]:
    """Choose the colour for a single box per the four drawing rules.

    Args:
        box: The box being drawn.
        has_tracks: Whether any track data is available for this frame. When
            ``False`` (raw vdet only) every box is blue.
        active_track_id: The resolved active track id, or ``None``/-1 if none.

    Returns:
        The RGB colour to draw the box outline (and its label) in.
    """
    if not has_tracks or box.track_id is None:
        return BLUE
    is_active = active_track_id is not None and box.track_id == active_track_id
    return color_for_track(box.track_id, active=is_active)


def draw_overlay(
    frame: np.ndarray,
    annotation: FrameAnnotation,
    *,
    has_tracks: bool,
    active_track_id: int | None = None,
    line_width: int = 3,
) -> Image.Image:
    """Draw all boxes (and their track-id labels) for one frame.

    Args:
        frame: The source frame as an HWC ``uint8`` RGB numpy array.
        annotation: The boxes to draw on this frame.
        has_tracks: Whether track data is available (governs blue vs palette).
        active_track_id: The active track id to highlight green, or ``None``.
        line_width: Outline thickness in pixels.

    Returns:
        A new :class:`PIL.Image.Image` with the overlay drawn on a copy of the
        frame (the input array is not modified).
    """
    image = Image.fromarray(frame).convert("RGB")
    draw = ImageDraw.Draw(image)
    for box in annotation.boxes:
        rgb = box_color(box, has_tracks=has_tracks, active_track_id=active_track_id)
        draw.rectangle((box.x, box.y, box.x2, box.y2), outline=rgb, width=line_width)
        if box.track_id is not None:
            label = str(box.track_id)
            draw.text((box.x + 2, box.y + 2), label, fill=rgb)
    return image


def hit_test(
    click_xy: tuple[int, int],
    annotation: FrameAnnotation,
    *,
    smallest_wins: bool = True,
) -> int | None:
    """Return the ``track_id`` of the box under a click, or ``None``.

    Used by the main-character correction flow: the click is tested against every
    box on the frame. When boxes overlap, the smallest enclosing box wins by
    default (configurable), which makes it possible to select a face nested
    inside a larger one.

    Args:
        click_xy: The ``(x, y)`` click position in image-pixel space.
        annotation: The boxes drawn on the clicked frame.
        smallest_wins: If ``True``, ties break to the smallest-area box; if
            ``False``, to the largest.

    Returns:
        The enclosing box's ``track_id``, or ``None`` if the click hit no box (or
        the hit box has no track id).
    """
    px, py = click_xy
    hits = [box for box in annotation.boxes if box.contains(px, py)]
    if not hits:
        return None
    chosen = min(hits, key=lambda b: b.area) if smallest_wins else max(hits, key=lambda b: b.area)
    return chosen.track_id
