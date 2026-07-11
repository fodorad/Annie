"""Incremental row paging shared by the Browse and Annotator tabs (UI helper).

Both tabs render long lists of expensive rows — every row schedules a lazy frame
decode — so they reveal only :attr:`annie.core.state.UiSettings.page_size` rows at a
time. This module owns that behaviour once:

* :class:`Pager` is the pure, side-effect-free cursor: how many rows are shown, how
  many remain, and which slice the next page covers. It is unit-tested on its own.
* :func:`paged` is the thin NiceGUI shell around it, providing three affordances:

  1. a **Jump to row** section, so a reviewer resuming a long session can type
     ``3400`` and land there. Rows before the target are never built — that is the
     whole point, since building them would schedule thousands of frame decodes.
  2. a **Show more** button (the manual fallback, always present);
  3. a **scroll sentinel** — a zero-noise ``q-intersection`` element parked below the
     rows. Quasar fires its ``visibility`` event as the sentinel nears the viewport,
     which reveals the next page without a click. Because the listener is bound to
     the element, it dies with it on ``refresh()`` rather than accumulating.

Auto-scrolling is opt-out via the Settings tab; the sentinel is consulted on every
event, so toggling the setting takes effect without rebuilding the list.

**A row number identifies the sample, not its slot on screen.** It is
:attr:`annie.core.models.VideoEntry.row_id` — the video's 1-based position in the
whole scanned dataset, assigned once at scan time over the sorted manifest. So a
filtered Browse tab shows a sparse ``#3, #17, #204`` rather than renumbering from 1,
the Annotator's queued subset keeps each video's dataset number, and "I stopped at
3400" survives a restart, a filter change, and a re-queue.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nicegui import ui

from annie.core import theme
from annie.core.state import state

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: How far ahead of the viewport the sentinel triggers the next page.
_SENTINEL_MARGIN = "600px"


@dataclass(slots=True)
class Pager:
    """A cursor over ``total`` rows, revealed one page at a time.

    Attributes:
        total: The number of rows in the backing list.
        start: Index of the first row actually rendered (non-zero after a seek).
        shown: Index one past the last rendered row; also the count of rendered
            rows when ``start`` is 0.
    """

    total: int
    start: int = 0
    shown: int = 0

    def advance(self, page_size: int) -> slice:
        """Reserve the next page and return the slice of rows to render.

        Args:
            page_size: Rows to reveal. Values below 1 are clamped to 1, so a bad
                setting can never yield an empty page and stall the caller's loop.

        Returns:
            The slice of the backing list covering the newly revealed rows.
        """
        begin = self.shown
        self.shown = min(begin + max(1, page_size), self.total)
        return slice(begin, self.shown)

    def seek(self, index: int) -> None:
        """Restart paging at ``index``, clamped into range.

        The next :meth:`advance` renders the page beginning at ``index``; rows before
        it are skipped entirely rather than rendered and scrolled past.

        Args:
            index: Zero-based row index to resume from.
        """
        target = min(max(index, 0), max(self.total - 1, 0))
        self.start = target
        self.shown = target

    @property
    def remaining(self) -> int:
        """How many rows are still unrendered."""
        return self.total - self.shown

    @property
    def exhausted(self) -> bool:
        """Whether every row from :attr:`start` onward has been rendered."""
        return self.remaining <= 0


def index_of_row_id(row_ids: Sequence[int], target: int) -> int:
    """Return where ``target`` sits among the shown rows' ``row_ids``.

    Row ids increase along the list (they are positions in the same sorted manifest),
    so a filtered list is still ordered and can be searched. A row id that was
    filtered out resolves to the next one still on screen, which is what a reviewer
    typing "3400" into a filtered Browse tab means: *take me to where 3400 would be*.

    Args:
        row_ids: The dataset row ids of the shown rows, ascending.
        target: The dataset row id to land on.

    Returns:
        A 0-based index into ``row_ids``, clamped to the last row.
    """
    if not row_ids:
        return 0
    return min(bisect_left(row_ids, target), len(row_ids) - 1)


def paged[T](
    entries: Sequence[T],
    render_row: Callable[[T], None],
    *,
    row_id: Callable[[T], int],
    total_rows: int,
    actions: Callable[[], None] | None = None,
    container: Callable[[], ui.element] | None = None,
    page_size: int | None = None,
    jump_slot: ui.element | None = None,
) -> None:
    """Render ``entries`` one page at a time, with jump, auto-scroll, and a button.

    Args:
        entries: The rows to page through — already filtered, still ordered.
        render_row: Builds one row. Called inside the rows container.
        row_id: The dataset row id of an entry, used to drive the jump box. This is
            the sample's number in the whole dataset, not its index in ``entries``.
        total_rows: How many rows the *unfiltered* dataset has, so the jump box
            accepts any dataset row id even when the view is filtered.
        actions: Builds extra controls at the end of the jump row, for list-wide
            operations that belong next to it (the Annotator's "Clear all").
        container: Factory for the element newly revealed rows are built into.
            Defaults to a vertical column; the Browse grid view passes a wrapping
            row so its boxes flow into a grid.
        page_size: Rows revealed per page. Defaults to
            :attr:`annie.core.state.UiSettings.page_size`; the grid view reveals more.
        jump_slot: Where to build the "Jump to row" card. When given, it is rendered
            into that (pre-existing, persistent) element — e.g. the Browse View
            panel — instead of inline above the rows. The slot is cleared first, so a
            re-paged list never stacks duplicate jump cards.
    """
    pager = Pager(len(entries))
    row_ids = [row_id(entry) for entry in entries]
    reveal = page_size if page_size is not None else state.ui.page_size

    def restart(target: int) -> None:
        """Re-page from dataset row id ``target``, discarding the rendered rows."""
        body.clear()
        pager.seek(index_of_row_id(row_ids, target))
        show_more()

    def caption() -> str:
        parts = []
        if len(entries) < total_rows:
            parts.append(f"{len(entries)} of {total_rows} samples match the filters.")
        if pager.start > 0:
            parts.append(f"Showing from row #{row_ids[pager.start]} — earlier rows hidden.")
        return "  ".join(parts)

    if jump_slot is not None:
        jump_slot.clear()
        with jump_slot:
            _jump_section(total_rows, restart, caption, actions)
    else:
        _jump_section(total_rows, restart, caption, actions)

    body = (container or (lambda: ui.column().classes("w-full gap-2")))()

    def show_more() -> None:
        with body:
            for entry in entries[pager.advance(reveal)]:
                render_row(entry)
        more.set_text(f"Show more ({pager.remaining} left)")
        more.set_visibility(not pager.exhausted)
        sentinel.set_visibility(not pager.exhausted)

    def on_sentinel(event) -> None:  # noqa: ANN001 - NiceGUI event args
        if event.args and state.ui.auto_scroll and not pager.exhausted:
            show_more()

    more = ui.button("Show more", on_click=show_more).props("flat")
    # A 1px-tall element: an empty one has a zero-area rect, which browsers do not
    # reliably report as intersecting.
    sentinel = ui.element("q-intersection").props(f"margin={_SENTINEL_MARGIN}")
    sentinel.classes("w-full").style("height:1px")
    sentinel.on("visibility", on_sentinel)

    show_more()


def _jump_section(
    total_rows: int,
    jump: Callable[[int], None],
    caption: Callable[[], str],
    actions: Callable[[], None] | None = None,
) -> None:
    """Build the "Jump to row" card that re-pages the list from a dataset row id."""
    with ui.card().classes("w-full"), ui.column().classes("w-full gap-1"):
        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            ui.icon("my_location", color=theme.PRIMARY)
            ui.label("Jump to row").classes("font-medium")
            target = (
                ui.number(value=1, min=1, max=max(total_rows, 1), step=1, precision=0)
                .props("dense outlined")
                .classes("w-28")
                .tooltip("A row's #number, counted over the whole dataset")
            )
            ui.label(f"of {total_rows}").classes("text-sm").style(f"color:{theme.NEUTRAL}")

            def go() -> None:
                jump(int(target.value or 1))
                label.set_text(caption())

            def reset() -> None:
                target.set_value(1)
                go()

            ui.button("Jump", on_click=go).props("unelevated dense")
            ui.button(icon="restart_alt", on_click=reset).props("flat dense round").tooltip(
                "Back to the first row"
            )
            if actions is not None:
                actions()

        label = ui.label(caption()).classes("text-xs").style(f"color:{theme.NEUTRAL}")
