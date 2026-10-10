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
import logging
import os
import sys
import webbrowser
from pathlib import Path

from nicegui import app, run, ui

from annie import __version__
from annie.core import logbook, runtime, theme
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

    if runtime.launcher_outdated(os.environ.get("ANNIE_LAUNCHER_VERSION")):
        _launcher_update_banner()

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


def _launcher_update_banner() -> None:
    """Ask the user to reinstall when the Windows launcher is older than this Annie needs."""
    with (
        ui.row()
        .classes("w-full items-center gap-2 q-pa-sm rounded")
        .style(f"background:{theme.WARNING}22")
    ):
        ui.icon("system_update", color=theme.WARNING)
        ui.label("A new Annie installer is available. Download it and run it once to update.")
        ui.link("Download installer", runtime.INSTALLER_URL, new_tab=True)


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


#: Sentinel marking that the macOS FFmpeg re-exec (see :func:`_ensure_macos_ffmpeg_libs`) has
#: already happened, so the relaunched process does not loop.
_REEXEC_SENTINEL = "ANNIE_FFMPEG_REEXEC"

#: Homebrew FFmpeg lib dirs probed on macOS (Apple Silicon, then Intel); ``ANNIE_FFMPEG_LIB_DIR``
#: overrides both for a non-standard install.
_MACOS_FFMPEG_LIB_DIRS = ("/opt/homebrew/opt/ffmpeg/lib", "/usr/local/opt/ffmpeg/lib")


def _macos_ffmpeg_lib_dir() -> str | None:
    """Return the FFmpeg lib dir to add to ``DYLD_LIBRARY_PATH`` on macOS, or ``None``.

    Honours ``ANNIE_FFMPEG_LIB_DIR`` first, else the Homebrew prefixes; returns ``None`` when
    none exist (so the caller does nothing and the usual torchcodec error still surfaces).
    """
    override = os.environ.get("ANNIE_FFMPEG_LIB_DIR", "").strip()
    candidates = (override,) if override else _MACOS_FFMPEG_LIB_DIRS
    return next((d for d in candidates if d and Path(d).is_dir()), None)


def _ensure_macos_ffmpeg_libs() -> None:
    """On macOS, re-exec once with ``DYLD_LIBRARY_PATH`` set so torchcodec finds FFmpeg.

    torchcodec's native extensions link the Homebrew FFmpeg dylibs via ``@rpath`` and look for
    them in the Python install's ``lib`` dir, not Homebrew's prefix — so launching Annie any
    way that doesn't already export ``DYLD_LIBRARY_PATH`` fails frame decode with
    ``Library not loaded: @rpath/libavutil.NN.dylib``. The env var must be present *before*
    Python starts (a process cannot fix its own already-resolved ``@rpath`` lookups), so when
    it is missing we relaunch the exact same process once with it set. A sentinel env var
    guards against an infinite loop. No-op off macOS, when no Homebrew FFmpeg is found, or when
    the path is already in ``DYLD_LIBRARY_PATH`` (e.g. ``make run`` already exported it).
    """
    if sys.platform != "darwin" or os.environ.get(_REEXEC_SENTINEL):
        return
    lib_dir = _macos_ffmpeg_lib_dir()
    if lib_dir is None:
        return
    current = os.environ.get("DYLD_LIBRARY_PATH", "")
    if lib_dir in current.split(":"):
        return  # already set (e.g. by `make run`); nothing to do
    env = {**os.environ, _REEXEC_SENTINEL: "1"}
    env["DYLD_LIBRARY_PATH"] = f"{lib_dir}:{current}" if current else lib_dir
    logbook.report(
        f"Re-launching with DYLD_LIBRARY_PATH={lib_dir} so torchcodec can load FFmpeg",
        level="info",
    )
    os.execve(sys.executable, [sys.executable, *sys.argv], env)


def _report_busy_port() -> None:
    """Explain a busy port: reuse a running Annie, or name the conflict clearly.

    Runs before the log file is attached and the process exits right after, so the
    message goes to the console, where the user launching Annie is looking.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    console = logging.getLogger("annie.startup")
    url = runtime.app_url(settings.host, settings.port)
    if runtime.annie_running(settings.host, settings.port):
        console.info("Annie is already running at %s — opening it.", url)
        if settings.open_browser:
            webbrowser.open(url)
        return
    console.error(
        "Port %s is used by another program. Close it, or start Annie on another port "
        "with ANNIE_PORT (e.g. `make run PORT=8090`).",
        settings.port,
    )
    sys.exit(1)


def main() -> None:
    """Console-script / module entry point: register the page and run the server."""
    # macOS: make the Homebrew FFmpeg dylibs discoverable before anything imports torchcodec,
    # re-launching once with DYLD_LIBRARY_PATH set if needed (see the helper). Must run first.
    _ensure_macos_ffmpeg_libs()
    # A second launch (e.g. double-clicking the shortcut twice) opens the running Annie
    # instead of crashing on the busy port.
    if runtime.port_in_use(settings.host, settings.port):
        _report_busy_port()
        return
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
        # Open the browser only when asked (env ANNIE_OPEN_BROWSER). Off for dev/CI and for
        # the headless Docker server — in the container the launcher opens the browser on the
        # host instead, so NiceGUI must not try to open one inside the container.
        show=settings.open_browser,
        reconnect_timeout=30.0,  # survive brief disconnects without dropping the page
    )


# The page is registered via the @ui.page decorator above; ``main`` only starts it.
if __name__ in {"__main__", "__mp_main__"}:
    main()
