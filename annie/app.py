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
import contextlib

from nicegui import app, run, ui

from annie import __version__
from annie.core import logbook, theme
from annie.core.config import settings
from annie.core.state import state
from annie.media.decode import media_available
from annie.pages import annotator, browse, convert, dataset, home
from annie.pages import logs as logs_page
from annie.pages import settings as settings_page

#: The tab bar, in display order: ``(page id, Tabler icon, label, overview)`` per tab.
#: The overview is shown as a hover tooltip on the tab button (so each page body no
#: longer repeats its own title/description).
_TABS = (
    ("home", "home", "Home", "Overview of Annie and quick links to get started."),
    (
        "convert",
        "autorenew",
        "Convert",
        "Re-encode audio/video to a consistent, torchcodec-validated form so previews, "
        "renders, and downstream loaders never hit broken seeking.",
    ),
    (
        "dataset",
        "folder",
        "Dataset",
        "Build the dataset from data sources (videos, vdet, tracks, CSVs), pick a review "
        "database, and watch live metrics.",
    ),
    (
        "browse",
        "grid_view",
        "Browse",
        "Scroll, filter, and review every sample; queue videos for the Annotator.",
    ),
    (
        "annotator",
        "edit",
        "Annotator",
        "Correct the protagonist track and add temporal event annotations on queued videos.",
    ),
    ("logs", "receipt_long", "Log", "Live application logs and error toasts."),
    (
        "settings",
        "settings",
        "Settings",
        "Tune UI preferences: row heights, paging, auto-scroll, and off-screen unload timing.",
    ),
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
                for name, icon, title, overview in _TABS:
                    tab = ui.tab(name, label=title, icon=icon)
                    with tab:
                        ui.tooltip(overview).props("delay=1000")
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


#: Floor for the render-sweep cadence, so a tiny TTL can't busy-spin the loop.
_MIN_SWEEP_INTERVAL_SECONDS = 15


async def _sweep_render_clips() -> None:
    """Periodically reclaim rendered clips older than the (settable) temp TTL.

    Runs once per process for the app's lifetime. The cadence tracks
    :attr:`annie.core.config.Settings.temp_ttl_seconds` live, so changing the TTL on
    the Settings tab takes effect on the next cycle. Rendered clips revert their UI
    element at :func:`annie.pages.utils.render_embed_ttl` (also capped by the TTL), so
    the sweep never deletes a file a visible ``ui.video`` still points at.
    """
    while True:
        await asyncio.sleep(max(_MIN_SWEEP_INTERVAL_SECONDS, settings.temp_ttl_seconds))
        with contextlib.suppress(Exception):
            state.renderer.sweep()


def main() -> None:
    """Console-script / module entry point: register the page and run the server."""
    # Name the log after the active session DB so the two are paired (and renaming
    # the DB later renames the log too — see LogBook.retarget / AppState.set_store).
    log_path = logbook.LOG.attach_file(settings.logs_dir, state.store.db_path.stem)
    # Create the persistent config dir up front so the Save/Load pickers open
    # inside the auto-discovered directory (e.g. /annie-home/configs in Docker).
    if settings.config_dir is not None:
        settings.config_dir.mkdir(parents=True, exist_ok=True)
    app.on_exception(lambda exc: logbook.report_exception("Unhandled exception", exc))
    logbook.report(f"Annie started — logging to {log_path}", level="info")
    # Rendered clips are throwaway scratch, regenerated on demand and never tracked
    # across restarts — so any left in the temp dir are orphans from a previous run
    # (often a killed process that skipped its own cleanup). Reclaim them at startup.
    _jobs, freed = state.renderer.clear_all()
    if freed:
        logbook.report(
            f"Cleared {freed} leftover rendered clip(s) from {settings.temp_dir}", level="info"
        )
    if not media_available():
        logbook.report(
            "Media extra not installed — frame thumbnails and rendered clips unavailable. "
            'Run: uv pip install -e ".[all]"',
            level="warning",
        )
    app.on_startup(lambda: asyncio.create_task(run.io_bound(state.rescan)))
    app.on_startup(lambda: asyncio.create_task(_sweep_render_clips()))
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
