"""The pending slot: a box that shows a spinner until its content arrives (UI helper).

Every pane whose content Annie is still producing in the background — a decoding frame,
a rendering clip, a video being opened — shows a spinner in its box, so the page never
looks idle while work is under way. This module owns that look and its life cycle:

- :func:`pending` puts a centred spinner in a box. With ``keep=True`` the current
  content stays visible, dimmed under the spinner (e.g. frames being redrawn for another
  track).
- :func:`ready` swaps in the finished content; :func:`failed` shows an error icon whose
  tooltip carries the reason.
- :func:`pending_badge` / :func:`settle_badge` do the same for a small tag such as
  ``#frames``.

Each call is a no-op once the box (or its browser tab) is gone, so a background task that
finishes after a refresh or reload never has to guard the UI itself.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from nicegui import ui

from annie.core import theme
from annie.pages.utils import _alive

if TYPE_CHECKING:
    from collections.abc import Callable

MEDIA_BOX = (
    "position:relative;border-radius:8px;background:#e5e7eb;display:flex;"
    "align-items:center;justify-content:center;overflow:hidden"
)
"""Style of the grey, centred media placeholder box (frames, ORIGINAL, render slots)."""

CSS = (
    ".annie-pending{position:absolute;inset:0;display:flex;align-items:center;"
    "justify-content:center;border-radius:inherit;z-index:2;pointer-events:none}"
    ".annie-pending.annie-dim{background:rgba(229,231,235,0.65)}"
)
"""Global CSS for the spinner overlay; registered once by the app at start-up."""

_MARK = "data-annie-pending"
"""Prop that marks an element as a pending overlay, so it can be found and removed."""


def media_box(width: int, height: int) -> ui.element:
    """Create an empty grey media box of a fixed CSS size.

    Args:
        width: Width in px.
        height: Height in px.

    Returns:
        The box, ready for :func:`pending` / :func:`ready`.
    """
    return ui.element("div").style(f"width:{width}px;height:{height}px;{MEDIA_BOX}")


def _overlays(box: ui.element) -> list[ui.element]:
    """The pending overlays currently inside ``box``."""
    return [child for child in box.default_slot.children if child.props.get(_MARK)]


def is_pending(box: ui.element) -> bool:
    """Whether ``box`` currently shows a pending spinner (``False`` once it is gone)."""
    return _alive(box) and bool(_overlays(box))


def pending(box: ui.element, *, keep: bool = False, size: str = "md") -> None:
    """Show a centred spinner in ``box`` until :func:`ready` or :func:`failed`.

    Calling it again on a pending box changes nothing (no second spinner).

    Args:
        box: The box whose content is being produced.
        keep: Keep the current content visible, dimmed under the spinner, instead of
            emptying the box first.
        size: Quasar spinner size (``"xs"`` … ``"xl"`` or a CSS length).
    """
    if not _alive(box):
        return
    with contextlib.suppress(RuntimeError):  # the box was refreshed away meanwhile
        if _overlays(box):
            return
        if not keep:
            box.clear()
        with box:
            overlay = ui.element("div").props(f"{_MARK}=1").classes("annie-pending")
        if keep:
            overlay.classes("annie-dim")
        with overlay:
            ui.spinner(size=size, color=theme.PRIMARY)


def ready(box: ui.element, build: Callable[[], object]) -> None:
    """Replace the spinner (and any old content) with the finished content.

    Args:
        box: The pending box.
        build: Builds the new content; called inside ``box``.
    """
    if not _alive(box):
        return
    with contextlib.suppress(RuntimeError):
        box.clear()
        with box:
            build()


def failed(box: ui.element, reason: str) -> None:
    """Replace the spinner with an error icon whose tooltip explains why.

    Args:
        box: The pending box.
        reason: A short, user-readable cause (shown on hover).
    """
    ready(box, lambda: ui.icon("broken_image", color=theme.DANGER).tooltip(reason))


def pending_badge(text: str) -> ui.element:
    """Create a badge slot showing ``text`` with a small spinner, for a value not known yet.

    Args:
        text: The badge text while pending, e.g. ``"#frames"``.

    Returns:
        The slot to pass to :func:`settle_badge` once the value is known.
    """
    slot = ui.element("span")
    with slot, ui.badge(text, color=theme.NEUTRAL).classes("items-center gap-1"):
        ui.spinner(size="0.9em", color="white")
    return slot


def settle_badge(slot: ui.element, text: str, color: str = theme.NEUTRAL) -> None:
    """Replace a pending badge with its final ``text`` (no spinner).

    Args:
        slot: The slot returned by :func:`pending_badge`.
        text: The final text, e.g. ``"#frames: 120"`` or ``"audio: ?"``.
        color: The badge colour.
    """
    ready(slot, lambda: ui.badge(text, color=color))
