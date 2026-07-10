"""Drop a row's preview images while it is far off screen (UI helper).

Paging bounds how fast rows appear, not how many end up on the page: a reviewer who
scrolls through 2800 samples has 2800 rows mounted, each pinning ~60 KB of base64
WebP in the server's element tree *and* in the browser tab, plus its decoded bitmaps.
That adds up to hundreds of megabytes long after the row has left the screen.

So each row carries a **viewport observer** — a Quasar ``q-intersection`` stretched
over the row — and a :class:`OffscreenGate`. When the row has been out of view for
:attr:`annie.core.state.UiSettings.unload_after_seconds`, its images are cleared back
to the grey placeholders they started as; when it scrolls back, they are decoded
again. Both tabs size their media boxes in fixed pixels, so an emptied row keeps its
exact height and nothing below it jumps.

The delay is what makes this safe to do on scroll: a row flicking past the edge of
the viewport, or a small scroll jitter, re-enters long before its unload fires, so
normal scrolling never triggers a redecode. Only rows genuinely left behind are
dropped.

What this does *not* reclaim is the row skeleton itself (~43 elements: the card, its
badges, buttons and selects). Those are far cheaper per row than the images, but they
still accumulate; capping the number of mounted rows is a separate change.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nicegui import ui

from annie.core.state import state
from annie.pages.lazy import schedule
from annie.pages.utils import _alive

if TYPE_CHECKING:
    from collections.abc import Callable

#: How far outside the viewport a row still counts as "on screen". Generous, so
#: rows just past the edge are never dropped and re-decoded during normal scrolling.
_MARGIN = "1200px"


@dataclass(slots=True)
class OffscreenGate:
    """Decides when an off-screen row's images may be dropped, and when to restore them.

    A row can leave and re-enter the viewport many times while an unload is waiting
    out its delay. Rather than trying to cancel the pending task, each scroll event
    bumps :attr:`pending`; a fired unload only takes effect if its generation is still
    the newest, so a row that came back is left alone.

    Attributes:
        loaded: Whether the row's images are currently mounted.
        pending: Generation of the most recent visibility change.
    """

    loaded: bool = True
    pending: int = 0

    def hide(self) -> int | None:
        """Record that the row left the viewport.

        Returns:
            The generation to arm an unload with, or ``None`` if the row's images are
            already gone and there is nothing to drop.
        """
        self.pending += 1
        return self.pending if self.loaded else None

    def show(self) -> bool:
        """Record that the row entered the viewport.

        Returns:
            ``True`` if the caller must rebuild the images (they had been dropped).
        """
        self.pending += 1  # invalidates any unload still waiting out its delay
        if self.loaded:
            return False
        self.loaded = True
        return True

    def expire(self, generation: int) -> bool:
        """Resolve an armed unload whose delay has elapsed.

        Args:
            generation: The generation the unload was armed with.

        Returns:
            ``True`` if the row is still off screen and its images should be dropped.
        """
        if generation != self.pending or not self.loaded:
            return False  # the row came back, or a later event superseded this one
        self.loaded = False
        return True


def observe_row(load: Callable[[], None], unload: Callable[[], None]) -> None:
    """Watch the enclosing row and drop its images once it sits off screen.

    Must be called inside a **relatively positioned** container (the row card); the
    observer stretches over it, so its visibility is the row's visibility.

    Args:
        load: Rebuilds the row's images. Called when a dropped row scrolls back in.
        unload: Clears the row's images back to their placeholders.
    """
    observer = ui.element("q-intersection").props(f"margin={_MARGIN}")
    observer.style("position:absolute;inset:0;pointer-events:none;z-index:-1")
    gate = OffscreenGate()

    async def _expire(generation: int) -> None:
        await asyncio.sleep(state.ui.unload_after_seconds)
        if not gate.expire(generation) or not _alive(observer):
            return
        with contextlib.suppress(RuntimeError):
            unload()

    def on_visibility(event) -> None:  # noqa: ANN001 - NiceGUI event args
        if event.args:
            if gate.show():
                with contextlib.suppress(RuntimeError):
                    load()
            return
        generation = gate.hide()
        if generation is not None:
            schedule(observer, lambda g=generation: _expire(g))

    observer.on("visibility", on_visibility)
