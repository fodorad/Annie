"""Log tab — browse, filter, and copy recorded events (UI).

A consumer of :data:`annie.logbook.LOG`. Shows the most recent events (newest
first) with a level/text filter and, per row, a **Copy** button (to the clipboard)
and a **Details** dialog with the full text / traceback. A per-client poller
surfaces *new* errors as auto-dismissing toasts so a background failure is visible
without watching the terminal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from annie.core import logbook, theme

if TYPE_CHECKING:
    from annie.core.logbook import LogEvent

#: How many (filtered) events the table shows at once, newest first.
_MAX_ROWS = 300

_level_filter = {"value": "all"}  #: active level filter (``"all"`` or a level name)
_text_filter = {"value": ""}  #: active free-text substring filter

#: Log level → chip colour, mirroring the semantic palette in :mod:`annie.core.theme`.
_LEVEL_COLOR = {
    "error": theme.DANGER,
    "warning": theme.WARNING,
    "info": theme.NEUTRAL,
}


def _level_color(level: str) -> str:
    """Chip colour for a level."""
    return _LEVEL_COLOR.get(level, theme.NEUTRAL)


def _filtered() -> list[LogEvent]:
    """Return the events passing the current filters, newest first, capped.

    The text filter matches the timestamp, message, and details — so a query like
    ``14:30`` or ``2026-06-26`` narrows by time as well as content.
    """
    level = _level_filter["value"]
    text = _text_filter["value"].strip().lower()
    out: list[LogEvent] = []
    for event in reversed(logbook.LOG.events()):
        if level != "all" and event.level != level:
            continue
        if text and not any(
            text in field
            for field in (event.time_text.lower(), event.message.lower(), event.details.lower())
        ):
            continue
        out.append(event)
        if len(out) >= _MAX_ROWS:
            break
    return out


def _copy(event: LogEvent) -> None:
    """Copy an event's text to the clipboard."""
    ui.clipboard.write(event.as_clipboard())
    ui.notify("Copied to clipboard", color=theme.PRIMARY)


def _details(event: LogEvent) -> None:
    """Open a dialog with the event's full text / traceback."""
    with ui.dialog() as dialog, ui.card().classes("w-[52rem] max-w-full gap-2"):
        ui.label(event.message).classes("font-medium break-all")
        ui.label(f"{event.time_text} · {event.level}").classes("text-xs").style(
            f"color:{theme.NEUTRAL}"
        )
        ui.code(event.details or "(no details)").classes("w-full max-h-96 overflow-auto")
        with ui.row().classes("justify-end gap-2"):
            ui.button("Copy", icon="content_copy", on_click=lambda: _copy(event)).props("flat")
            ui.button("Close", on_click=dialog.close).props("unelevated")
    dialog.open()


def _row(event: LogEvent) -> None:
    """Render one event row."""
    with ui.row().classes("w-full items-center gap-2 no-wrap"):
        ui.label(event.time_text).classes("text-xs font-mono").style(
            f"color:{theme.NEUTRAL};white-space:nowrap;flex:none"
        )
        ui.badge(event.level, color=_level_color(event.level))
        ui.label(event.message).classes("text-sm flex-grow break-all")
        ui.button(icon="content_copy", on_click=lambda e=event: _copy(e)).props(
            "flat dense round"
        ).tooltip("Copy")
        if event.details:
            ui.button(icon="open_in_full", on_click=lambda e=event: _details(e)).props(
                "flat dense round"
            ).tooltip("Details")


@ui.refreshable
def _table() -> None:
    """Build the filtered event list."""
    events = _filtered()
    total = logbook.LOG.latest_seq()
    ui.label(f"{len(events)} shown · {total} recorded").classes("text-xs").style(
        f"color:{theme.NEUTRAL}"
    )
    if not events:
        ui.label("No events match the filter.").style(f"color:{theme.NEUTRAL}")
        return
    with ui.column().classes("w-full gap-1"):
        for event in events:
            _row(event)


def render() -> None:
    """Build the Log tab body."""
    with ui.column().classes("w-full gap-3"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Log").classes("text-xl font-medium")
            ui.button("Refresh", icon="refresh", on_click=_table.refresh).props("flat")

        with ui.row().classes("items-center gap-3 wrap"):
            level = ui.select(
                {"all": "all levels", "error": "errors", "warning": "warnings", "info": "info"},
                value=_level_filter["value"],
            ).props("dense outlined")
            level.on_value_change(lambda e: (_level_filter.update(value=e.value), _table.refresh()))
            text = ui.input("filter text", value=_text_filter["value"]).props(
                "dense outlined clearable"
            )
            text.on_value_change(
                lambda e: (_text_filter.update(value=e.value or ""), _table.refresh())
            )
            ui.button("Clear log", icon="delete_sweep", on_click=_clear).props("flat")

        _table()


def _clear() -> None:
    """Clear the in-memory event store (the dated file is kept)."""
    logbook.LOG.clear()
    _table.refresh()
    ui.notify("Cleared the in-memory log (the file is kept).", color=theme.PRIMARY)


def refresh() -> None:
    """Rebuild the event table (called when the tab is opened)."""
    _table.refresh()


def start_toasts() -> None:
    """Start a per-client poller that shows new errors as auto-dismissing toasts."""
    seen = {"seq": logbook.LOG.latest_seq()}  # only surface events after this client connected

    def poll() -> None:
        new, latest = logbook.LOG.since(seen["seq"])
        seen["seq"] = latest
        errors = [e for e in new if e.level == "error"]
        if not errors:
            return
        if len(errors) == 1:
            ui.notify(
                errors[0].message,
                type="negative",
                multi_line=True,
                close_button="✕",
                timeout=8000,
            )
        else:
            ui.notify(
                f"{len(errors)} new errors — see the Log tab",
                type="negative",
                close_button="✕",
                timeout=8000,
            )

    ui.timer(2.0, poll)
