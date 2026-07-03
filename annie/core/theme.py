"""Central theme tokens — every colour Annie uses lives here.

Two distinct colour systems are kept apart on purpose:

* **UI status colours** (:data:`STATUS_COLORS`) — chrome that conveys a row's
  link state in the Dataset summary and Browse list. Referenced by name, never as
  raw hex inline.
* **Box-drawing colours** (:func:`color_for_track` and friends, in
  :mod:`annie.color`) — colours burned onto the video frames themselves, given in
  BGR to match the render convention.

Keeping both here means a redesign touches one file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from annie.core.models import RowStatus

PRIMARY = "#006d77"
"""App-wide primary/accent colour, applied via NiceGUI's global colour call."""
ACCENT = "#22c55e"
"""More vivid green, e.g. the "validated" check."""

LOGO_MARK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" '
    'style="width:100%;height:100%">'
    '<g fill="none" stroke="#cbd5e1" stroke-width="6" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M14 22 L14 12 L24 12"/><path d="M40 12 L50 12 L50 22"/>'
    '<path d="M14 42 L14 52 L24 52"/><path d="M40 52 L50 52 L50 42"/></g>'
    '<path d="M21 33 L29 41 L42 24" fill="none" stroke="#22c55e" stroke-width="7" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)
"""The brand mark — a detection-box bracket enclosing a green validation check.

Matches ``docs/assets/logo.svg`` (mark only, no wordmark) and is used for the app
header and the browser-tab favicon. The SVG scales to its container.
"""

SUCCESS = "#2a9d8f"
"""Teal — linked / complete / usable."""
WARNING = "#f77f00"
"""Amber — video only / watchable, annotation off."""
DANGER = "#d90429"
"""Coral — annotation only / nothing to preview."""
NEUTRAL = "#676D78"
"""Grey — totals, secondary text."""

VDET_COLOR = "#1d3557"
"""Navy blue — vdet-related metrics."""
TRACK_COLOR = "#40916c"
"""Pastel green — track-related metrics."""

AVAILABLE = "#2a9d8f"
"""Teal — the path exists and was read."""
UNAVAILABLE = "#d90429"
"""Coral — the configured path is missing."""

LABEL_COLOR = "#5a189a"
"""Violet — distinct from vdet/track/status chips."""

VIDEO_TAG_COLOR = "#3a86ff"
"""Bright blue — has video frames."""
AUDIO_TAG_COLOR = "#fb8500"
"""Orange — has an audio stream."""

HEADER_FG = "#ffffff"
"""White header text/icons, readable on the dark primary header bar."""

STATUS_COLORS: dict[RowStatus, str] = {
    "linked": SUCCESS,
    "video_only": WARNING,
    "annotation_only": DANGER,
}
"""Status → colour, the single source of truth for the tri-state UI treatment."""

STATUS_LABELS: dict[RowStatus, str] = {
    "linked": "linked",
    "video_only": "video only",
    "annotation_only": "annotation only",
}
"""Status → short human label shown on pills and summary cards."""

STATUS_ICONS: dict[RowStatus, str] = {
    "linked": "link",
    "video_only": "videocam",
    "annotation_only": "file_present",
}
"""Status → Tabler icon name used on cards and pills."""

ColorName = Literal["success", "warning", "danger", "neutral", "primary", "accent"]
"""The semantic colour names accepted by :func:`color`."""

#: Semantic colour name → hex, backing :func:`color` lookups.
_BY_NAME: dict[str, str] = {
    "success": SUCCESS,
    "warning": WARNING,
    "danger": DANGER,
    "neutral": NEUTRAL,
    "primary": PRIMARY,
    "accent": ACCENT,
}


def status_color(status: RowStatus) -> str:
    """Return the hex colour for a row status.

    Args:
        status: One of the :data:`~annie.models.RowStatus` values.

    Returns:
        The hex colour string for that status.
    """
    return STATUS_COLORS[status]


def color(name: ColorName) -> str:
    """Resolve a semantic colour name to its hex value.

    Args:
        name: A semantic colour name (e.g. ``"success"``).

    Returns:
        The hex colour string.

    Raises:
        KeyError: If ``name`` is not a known semantic colour.
    """
    return _BY_NAME[name]
