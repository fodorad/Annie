"""Visible-window geometry of the event timeline (pure, no UI).

The timeline shows a window ``[start, start + span]`` of the clip's frames. The window is
**state of its own**: it never derives from the playhead, so the playhead can travel inside it
(or leave it) while the user pans, zooms, or follows playback. Everything here is plain
arithmetic so the rules are unit-testable without a browser.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_ZOOM = 100.0
"""Upper bound on the zoom factor."""

FOLLOW_LEAD = 0.10
"""Fraction of the window left *behind* the playhead after a page-flip."""


@dataclass(slots=True)
class TimelineView:
    """The visible window of a clip's timeline.

    Attributes:
        total_frames: Clip length in frames (0 while unknown).
        zoom: Zoom factor, ``>= 1``; ``1`` shows the whole clip.
        start: First visible frame. Clamped whenever it is read, so it may be set freely.
    """

    total_frames: int
    zoom: float = 1.0
    start: int = 0

    @property
    def last_frame(self) -> int:
        """Index of the clip's last frame (``0`` for an empty/unknown clip)."""
        return max(0, self.total_frames - 1)

    @property
    def zoomed_in(self) -> bool:
        """Whether the window is narrower than the clip (pan/scrollbar make sense)."""
        return self.zoom > 1.0 and self.last_frame > 1

    @property
    def span(self) -> int:
        """Number of frames between the window's edges (``>= 1``)."""
        if not self.zoomed_in:
            return max(1, self.last_frame)
        return max(1, min(self.last_frame, round(self.last_frame / self.zoom)))

    def _clamp_start(self, start: float) -> int:
        return int(max(0, min(round(start), self.last_frame - self.span)))

    def window(self) -> tuple[int, int]:
        """Return the visible ``(first, last)`` frames, clamped inside the clip."""
        start = self._clamp_start(self.start) if self.zoomed_in else 0
        return start, start + self.span

    def contains(self, frame: int) -> bool:
        """Whether ``frame`` lies inside the visible window."""
        lo, hi = self.window()
        return lo <= frame <= hi

    def fraction_of(self, frame: int) -> float | None:
        """Return ``frame``'s horizontal position ``[0, 1]``, or ``None`` if off-screen."""
        if not self.contains(frame):
            return None
        return self.fraction_clamped(frame)

    def fraction_clamped(self, frame: int) -> float:
        """Return ``frame``'s position clipped to ``[0, 1]`` (for bands that clip at an edge)."""
        lo, _ = self.window()
        return min(1.0, max(0.0, (frame - lo) / self.span))

    def frame_at(self, fraction: float) -> int:
        """Map a window fraction ``[0, 1]`` back to an absolute frame."""
        lo, _ = self.window()
        fraction = min(1.0, max(0.0, fraction))
        return min(self.last_frame, lo + round(fraction * self.span))

    def zoom_by(self, factor: float, anchor_frame: int | None = None) -> None:
        """Multiply the zoom, keeping ``anchor_frame`` at the same screen position.

        Args:
            factor: Multiplier (``> 1`` zooms in).
            anchor_frame: Frame to hold still; ``None`` anchors on the window's centre. An
                anchor outside the window is treated like ``None``.
        """
        lo, hi = self.window()
        if anchor_frame is None or not lo <= anchor_frame <= hi:
            anchor_frame = (lo + hi) // 2
        fraction = (anchor_frame - lo) / self.span
        self.zoom = max(1.0, min(self.zoom * factor, MAX_ZOOM))
        self.start = self._clamp_start(anchor_frame - fraction * self.span) if self.zoomed_in else 0

    def pan_by(self, delta_frames: int) -> None:
        """Slide the window by ``delta_frames`` (no-op when not zoomed in)."""
        if self.zoomed_in:
            self.start = self._clamp_start(self.window()[0] + delta_frames)

    def pan_by_fraction(self, fraction: float) -> None:
        """Slide the window by a fraction of its own width (negative pans left)."""
        self.pan_by(round(fraction * self.span))

    def set_start_fraction(self, fraction: float) -> None:
        """Place the window's left edge at ``fraction`` of the pannable range (scrollbar)."""
        if self.zoomed_in:
            fraction = min(1.0, max(0.0, fraction))
            self.start = self._clamp_start(fraction * (self.last_frame - self.span))

    def center_on(self, frame: int) -> None:
        """Centre the window on ``frame`` (no-op when not zoomed in)."""
        if self.zoomed_in:
            self.start = self._clamp_start(frame - self.span / 2)

    def page_to(self, frame: int, lead: float = FOLLOW_LEAD) -> None:
        """Flip the window so ``frame`` sits ``lead`` of the way in (forward playback).

        Moving backwards past the left edge shows the history instead, leaving ``lead`` of
        the window ahead of ``frame``.
        """
        if not self.zoomed_in:
            return
        lo, _ = self.window()
        if frame < lo:
            self.start = self._clamp_start(frame - (1.0 - lead) * self.span)
        else:
            self.start = self._clamp_start(frame - lead * self.span)

    def thumb(self) -> tuple[float, float]:
        """Return the scrollbar thumb as ``(left, width)`` fractions of its track."""
        if not self.zoomed_in:
            return 0.0, 1.0
        lo, _ = self.window()
        return lo / self.last_frame, self.span / self.last_frame
