"""NiceGUI application entry — assembles the four tabs into one page.

This is the only module that touches NiceGUI's app lifecycle. It applies the
global theme colour, builds a fixed primary **header bar** that carries the logo,
the tab navigation (Dataset, Browse, Annotator, Settings), and the version — all
white-on-primary and always visible — then starts the server bound to the
configured host/port. Run it with ``python -m annie.app``, ``make run``, or the
installed ``annie`` console script.

The UI is built **per browser connection** via :func:`@ui.page("/") <nicegui.ui.page>`
(not the shared auto-index) so every client has a live connection and a running
event loop while it builds. That is what lets the rows decode their frames as
background tasks bound to the real client, instead of a build-time timer firing
against a torn-down auto-index client.
"""

from __future__ import annotations

import asyncio

from nicegui import app, run, ui

from annie import __version__
from annie.core import logbook, theme
from annie.core.config import settings
from annie.core.state import state
from annie.media.decode import media_available
from annie.pages import annotator, browse, convert, dataset, home
from annie.pages import logs as logs_page
from annie.pages import settings as settings_page

#: The tab bar, in display order: ``(page id, Tabler icon, label)`` per tab.
_TABS = (
    ("home", "home", "Home"),
    ("convert", "autorenew", "Convert"),
    ("dataset", "folder", "Dataset"),
    ("browse", "grid_view", "Browse"),
    ("annotator", "edit", "Annotator"),
    ("logs", "receipt_long", "Log"),
    ("settings", "settings", "Settings"),
)

# The fixed header's width tracks the viewport. Without this, switching from a
# short tab (no scrollbar) to a tall one (scrollbar appears) shrinks the viewport
# by the scrollbar's width and visibly squeezes/shifts the header. Reserving the
# gutter unconditionally keeps the header's width constant across every tab.
ui.add_css("html { overflow-y: scroll; scrollbar-gutter: stable; }", shared=True)


def build() -> None:
    """Build the single-page tabbed UI."""
    ui.colors(primary=theme.PRIMARY)

    # Three equal-width (flex-1) sections so the middle one — the tabs — stays
    # exactly centered regardless of how wide the logo or version text are.
    with ui.header().classes("items-center q-px-md"):
        with ui.row().classes("flex-1 items-center gap-2"):
            ui.html(theme.LOGO_MARK_SVG).classes("w-8 h-8")
            ui.label("Annie").classes("text-lg font-medium")
        with ui.row().classes("flex-1 items-center justify-center"):
            with ui.tabs() as tabs:
                for name, icon, title in _TABS:
                    tab = ui.tab(name, label=title, icon=icon)
                    if name == "annotator":
                        annotator.set_tab(tab)
        with ui.row().classes("flex-1 items-center justify-end"):
            ui.label(f"v{__version__}").classes("text-xs opacity-70")

    def navigate(name: str) -> None:
        tabs.set_value(name)

    with ui.tab_panels(tabs, value="home").classes("w-full"):
        with ui.tab_panel("home"):
            home.render(navigate)
        with ui.tab_panel("convert"):
            convert.render()
        with ui.tab_panel("dataset"):
            dataset.render()
        with ui.tab_panel("browse"):
            browse.render()
        with ui.tab_panel("annotator"):
            annotator.render()
        with ui.tab_panel("logs"):
            logs_page.render()
        with ui.tab_panel("settings"):
            settings_page.render()

    # Browse and Annotator are consumers of the cached scan; rebuild them whenever
    # opened so they reflect source changes made on the Dataset tab.
    def _on_tab_change(event) -> None:  # noqa: ANN001 - NiceGUI event args
        if event.value == "browse":
            browse.refresh()
        elif event.value == "annotator":
            annotator.refresh()
        elif event.value == "logs":
            logs_page.refresh()

    tabs.on_value_change(_on_tab_change)
    annotator.sync_tab()  # set the tab's initial enabled/disabled state
    logs_page.start_toasts()  # per-client poller: surface new errors as toasts


@ui.page("/")
def index() -> None:
    """Build the tabbed UI fresh for each browser connection."""
    build()


def main() -> None:
    """Console-script / module entry point: register the page and run the server."""
    log_path = logbook.LOG.attach_file(settings.logs_dir)
    # Create the persistent config dir up front so the Save/Load pickers open
    # inside the auto-discovered directory (e.g. /annie-home/configs in Docker).
    if settings.config_dir is not None:
        settings.config_dir.mkdir(parents=True, exist_ok=True)
    app.on_exception(lambda exc: logbook.report_exception("Unhandled exception", exc))
    logbook.report(f"Annie started — logging to {log_path}", level="info")
    if not media_available():
        logbook.report(
            "Media extra not installed — frame thumbnails and rendered clips unavailable. "
            'Run: uv pip install -e ".[all]"',
            level="warning",
        )
    app.on_startup(lambda: asyncio.create_task(run.io_bound(state.rescan)))
    app.on_shutdown(state.renderer.shutdown)
    app.on_shutdown(state.converter.shutdown)
    ui.run(
        host=settings.host,
        port=settings.port,
        title="Annie",
        favicon=theme.LOGO_MARK_SVG,
        reload=False,
        show=False,
        reconnect_timeout=30.0,  # survive brief disconnects without dropping the page
    )


# The page is registered via the @ui.page decorator above; ``main`` only starts it.
if __name__ in {"__main__", "__mp_main__"}:
    main()
