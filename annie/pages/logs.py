"""Log tab — browse, filter, copy and report recorded events (UI).

A consumer of :data:`annie.logbook.LOG`. Shows the most recent events (newest
first) with a level/text filter and, per row, a **Copy** button (to the clipboard)
and a **Details** dialog with the full text / traceback. Errors also get a **Report**
button: it previews exactly what will be emailed to Annie's developer, lets the user add
a note, and after sending turns into a disabled green **Sent** (see
:mod:`annie.core.bugreport`). A per-client poller surfaces *new* errors as
auto-dismissing toasts so a background failure is visible without watching the terminal.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from nicegui import ui

from annie import __version__
from annie.core import bugreport, logbook, theme
from annie.pages.refresh import client_refreshable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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


async def _post_report(text: str) -> tuple[bool, str]:
    """Send a report from the user's browser; return whether it was accepted, and why not.

    The browser posts it (Annie's UI runs there), and a failure of any kind comes back
    as ``(False, reason)`` rather than an exception.
    """
    body = json.dumps(json.dumps(bugreport.payload(text)))  # a JS string literal of the JSON
    code = f"""
    try {{
      const r = await fetch({json.dumps(bugreport.REPORT_URL)}, {{
        method: "POST",
        headers: {{"Content-Type": "application/json", Accept: "application/json"}},
        body: {body},
      }});
      const j = await r.json().catch(() => ({{}}));
      const reason = j.message || r.statusText || String(r.status);
      return {{ok: r.ok && j.success === true, reason}};
    }} catch (e) {{
      return {{ok: false, reason: String(e)}};
    }}
    """
    try:
        result = await ui.run_javascript(code, timeout=30)
    except TimeoutError:
        return False, "the report service did not answer"
    if not isinstance(result, dict):
        return False, "no answer from the browser"
    return bool(result.get("ok")), str(result.get("reason") or "")


def _report(event: LogEvent, on_sent: Callable[[], None]) -> None:
    """Preview the report for ``event``, with an optional note, and send it on confirm."""

    def text(note: str) -> str:
        return bugreport.build_report(event, note, version=__version__)

    with ui.dialog() as dialog, ui.card().classes("w-[52rem] max-w-full gap-2"):
        ui.label("Report this error to Annie's developer").classes("font-medium")
        ui.label(
            "This text is sent by email, as shown. It can contain file paths from this PC, "
            "which may include your Windows user name. Nothing else is sent."
        ).classes("text-xs").style(f"color:{theme.NEUTRAL}")
        note = ui.textarea("What were you doing? (optional)").classes("w-full")
        note.props("outlined autogrow")
        preview = ui.code(text(""), language=None).classes("w-full max-h-80 overflow-auto")
        note.on_value_change(lambda e: preview.set_content(text(e.value or "")))
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            send = ui.button("Send", icon="send").props("unelevated")

    async def do_send() -> None:
        send.props("loading")
        sent, message = await _send(event, note.value or "")
        send.props(remove="loading")
        ui.notify(message, type="positive" if sent else "negative")
        if sent:
            dialog.close()
            on_sent()

    send.on_click(do_send)
    dialog.open()


async def _send(
    event: LogEvent,
    note: str,
    post: Callable[[str], Awaitable[tuple[bool, str]]] | None = None,
) -> tuple[bool, str]:
    """Send the report for ``event`` once, within the hourly limit.

    Args:
        event: The logged error.
        note: What the user was doing.
        post: Sends the report text (default: from the user's browser).

    Returns:
        Whether it is sent (also when it was already), and the message to show.
    """
    if bugreport.already_sent(event.seq):
        return True, "This error was already reported."
    if not bugreport.can_send():
        return False, "Too many reports this hour — please try again later."
    sender = _post_report if post is None else post
    ok, reason = await sender(bugreport.build_report(event, note, version=__version__))
    if not ok:
        return False, f"The report could not be sent: {reason}"
    bugreport.mark_sent(event.seq)
    return True, "Report sent — thank you!"


def _report_control(event: LogEvent) -> None:
    """A **Report** button for an error, or a disabled green **Sent** once reported.

    Shown only for errors, and only when reporting is set up
    (:func:`annie.core.bugreport.reporting_available`).
    """
    if event.level != "error" or not bugreport.reporting_available():
        return
    box = ui.element("div").style("flex:none")

    def paint() -> None:
        box.clear()
        with box:
            if bugreport.already_sent(event.seq):
                ui.button("Sent", icon="check").props("dense unelevated disable no-caps").style(
                    f"background:{theme.SUCCESS} !important;color:white !important"
                ).tooltip("This error was reported. Thank you!")
            else:
                ui.button(
                    "Report", icon="outgoing_mail", on_click=lambda: _report(event, on_sent)
                ).props("dense flat no-caps").tooltip("Email this error to Annie's developer")

    def on_sent() -> None:
        paint()
        _table.refresh()  # the row and the details dialog show the same state

    paint()


def _details(event: LogEvent) -> None:
    """Open a dialog with the event's full text / traceback."""
    with ui.dialog() as dialog, ui.card().classes("w-[52rem] max-w-full gap-2"):
        ui.label(event.message).classes("font-medium break-all")
        ui.label(f"{event.time_text} · {event.level}").classes("text-xs").style(
            f"color:{theme.NEUTRAL}"
        )
        ui.code(event.details or "(no details)").classes("w-full max-h-96 overflow-auto")
        with ui.row().classes("justify-end gap-2 items-center"):
            _report_control(event)
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
        _report_control(event)
        ui.button(icon="content_copy", on_click=lambda e=event: _copy(e)).props(
            "flat dense round"
        ).tooltip("Copy")
        if event.details:
            ui.button(icon="open_in_full", on_click=lambda e=event: _details(e)).props(
                "flat dense round"
            ).tooltip("Details")


@client_refreshable
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
        with ui.row().classes("w-full items-center justify-end"):
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
