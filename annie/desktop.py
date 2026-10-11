"""Annie as a Windows desktop app: the server plus a notification-area (tray) icon.

The Windows launcher (``installer/windows/launcher.pyw``) starts ``pythonw -m
annie.desktop``, so there is no console window. Instead, the Annie icon sits in the
notification area at the bottom right of the taskbar, and its menu opens Annie in the
browser, opens the Annie folder, downloads the example datasets when they were not
installed, checks for updates, and quits Annie.

The tray runs on its own thread (``pystray``'s ``run_detached``) and the NiceGUI server
keeps the main thread, exactly as with ``annie`` on the command line. Everything else is
the regular app: :func:`annie.app.main` is called unchanged.

Run ``python -m annie.desktop`` on another platform, or without the ``desktop`` extra,
and it starts Annie without the tray.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

#: ``MessageBoxW`` flags and its "Yes" answer (winuser.h).
_MB_OK, _MB_YESNO = 0x0, 0x4
_MB_ICONERROR, _MB_ICONQUESTION, _MB_ICONINFORMATION = 0x10, 0x20, 0x40
_MB_SETFOREGROUND = 0x10000
_IDYES = 6


def _quiet_streams() -> None:
    """Give ``pythonw`` real (discarding) stdout/stderr.

    Without a console both are ``None``, and the first library that logs to them
    (uvicorn does) crashes. Annie's own log goes to its log file regardless.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


def _message_box(text: str, flags: int) -> int:  # pragma: no cover - Windows UI
    """Show a native message box titled "Annie" and return the button pressed."""
    if sys.platform != "win32":
        return 0
    import ctypes

    return ctypes.windll.user32.MessageBoxW(None, text, "Annie", flags | _MB_SETFOREGROUND)


def _install_dir() -> Path | None:
    """Return the Windows install folder the launcher passed in, or ``None``."""
    raw = os.environ.get("ANNIE_INSTALL_DIR", "").strip()
    return Path(raw) if raw and Path(raw).is_dir() else None


class Tray:  # pragma: no cover - drives the Windows notification area
    """The notification-area icon and its menu, for the lifetime of the server."""

    def __init__(self, url: str, version: str, install_dir: Path | None) -> None:
        from annie.core import runtime
        from annie.core.config import settings

        self.url = url
        self.version = version
        self.install_dir = install_dir
        self._loop: Any = None
        self._icon: Any = None
        self._checking = threading.Lock()
        self._downloading = threading.Lock()
        self._folder = runtime.annie_folder(settings, install_dir)
        # The launcher points ANNIE_DATA_DIR at Data\examples; None when not installed.
        self._examples = settings.data_dir

    # ── lifecycle ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Show the icon; the menu works as soon as it appears."""
        if sys.platform != "win32":
            return  # pystray (the desktop extra) is installed on Windows only
        import pystray
        from nicegui import app

        menu = pystray.Menu(
            pystray.MenuItem("Open Annie", self._open_browser, default=True),
            pystray.MenuItem("Open Annie folder", self._open_folder),
            pystray.MenuItem(
                "Download example datasets",
                self._download_examples,
                visible=lambda _item: self._examples_missing(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Check for updates", self._check_for_updates),
            pystray.MenuItem(f"Annie v{self.version}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Annie", self._quit),
        )
        self._icon = pystray.Icon("Annie", self._image(), f"Annie v{self.version}", menu)
        # The server's event loop exists only once it runs; the menu needs it to stop it.
        app.on_startup(self._remember_loop)
        self._icon.run_detached()

    def stop(self) -> None:
        """Remove the icon (Windows otherwise leaves a ghost until hovered)."""
        if self._icon is not None:
            self._icon.stop()

    def _remember_loop(self) -> None:
        import asyncio

        self._loop = asyncio.get_running_loop()

    def _image(self) -> Any:
        """The Annie icon: the installed ``annie.ico``, else a plain disc in Annie's colour."""
        from PIL import Image, ImageDraw

        from annie.core import theme

        ico = self.install_dir / "annie.ico" if self.install_dir else None
        if ico is not None and ico.is_file():
            return Image.open(ico)
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(image).ellipse((4, 4, 60, 60), fill=theme.PRIMARY)
        return image

    # ── menu actions (pystray calls them on its own thread) ─────────────────────

    def _open_browser(self) -> None:
        webbrowser.open(self.url)

    def _open_folder(self) -> None:
        if sys.platform != "win32":
            return
        self._folder.mkdir(parents=True, exist_ok=True)
        os.startfile(self._folder)  # noqa: S606 - opens a folder in Explorer

    def _examples_missing(self) -> bool:
        from annie.core import runtime

        return self._examples is not None and not runtime.examples_installed(self._examples)

    def _download_examples(self) -> None:
        # Off the menu thread: about 50 MB, and a message box blocks.
        threading.Thread(target=self._examples_flow, daemon=True).start()

    def _examples_flow(self) -> None:
        from annie.core import runtime

        if self._examples is None or not self._downloading.acquire(blocking=False):
            return
        try:
            self._icon.notify("Downloading the example datasets (about 50 MB)...", "Annie")
            try:
                runtime.download_examples(self._examples)
            except (OSError, ValueError) as exc:
                _message_box(
                    f"The example datasets could not be downloaded:\n{exc}\n\n"
                    "Check the internet connection and try again.",
                    _MB_OK | _MB_ICONERROR,
                )
                return
            self._icon.update_menu()  # hides "Download example datasets"
            _message_box(
                "The example datasets are ready.\n\n"
                "In Annie, reload the page, open the Dataset tab and choose "
                '"[Example] CMU-MOSEI Mini" or "[Example] First Impression V2 Mini".',
                _MB_OK | _MB_ICONINFORMATION,
            )
        finally:
            self._downloading.release()

    def _quit(self) -> None:
        from nicegui import app

        self.stop()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(app.shutdown)
        else:
            app.shutdown()

    def _check_for_updates(self) -> None:
        # Off the menu thread: PyPI may take seconds, and a message box blocks.
        threading.Thread(target=self._update_flow, daemon=True).start()

    def _update_flow(self) -> None:
        from annie.core import runtime

        if not self._checking.acquire(blocking=False):
            return  # a check is already showing its answer
        try:
            latest = runtime.latest_pypi_version()
            if latest is None:
                _message_box(
                    "Could not reach the update server. Check the internet connection "
                    "and try again.",
                    _MB_OK | _MB_ICONERROR,
                )
            elif not runtime.is_newer(latest, self.version):
                _message_box(f"Annie v{self.version} is up to date.", _MB_OK | _MB_ICONINFORMATION)
            elif self.install_dir is None:
                _message_box(
                    f"Annie v{latest} is available. Update it the way you installed it, "
                    'e.g. uv pip install -U "annie[media]".',
                    _MB_OK | _MB_ICONINFORMATION,
                )
            elif (
                _message_box(
                    f"Annie v{latest} is available (you have v{self.version}).\n\n"
                    "Update now? Annie closes, updates, and opens again.",
                    _MB_YESNO | _MB_ICONQUESTION,
                )
                == _IDYES
            ):
                self._start_update()
        finally:
            self._checking.release()

    def _start_update(self) -> None:
        """Hand over to the launcher, which waits for this process to exit, then updates."""
        if sys.platform != "win32" or self.install_dir is None:
            return
        subprocess.Popen(  # noqa: S603 - fixed argv inside the install folder
            [
                str(self.install_dir / "python" / "pythonw.exe"),
                str(self.install_dir / "launcher.pyw"),
                "--update",
                "--after-pid",
                str(os.getpid()),
            ],
            cwd=self.install_dir,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        self._quit()


def main() -> None:  # pragma: no cover - process entry point
    """Start Annie with its tray icon (Windows), or plainly where there is no tray."""
    _quiet_streams()

    from annie import __version__
    from annie import app as annie_app
    from annie.core import runtime
    from annie.core.config import settings

    if runtime.port_in_use(settings.host, settings.port):
        if runtime.annie_running(settings.host, settings.port):
            # A second start: open the running Annie instead of a second icon.
            webbrowser.open(runtime.app_url(settings.host, settings.port))
        elif sys.platform == "win32":
            _message_box(
                f"Annie cannot start: port {settings.port} is used by another program.\n\n"
                "Close that program and start Annie again.",
                _MB_OK | _MB_ICONERROR,
            )
        return

    tray = None
    if sys.platform == "win32":
        try:
            tray = Tray(runtime.app_url(settings.host, settings.port), __version__, _install_dir())
            tray.start()
        except Exception:  # noqa: BLE001 - no desktop extra, or no notification area
            tray = None  # Annie still works from the browser, just without the icon
    try:
        annie_app.main()
    finally:
        if tray is not None:
            tray.stop()


if __name__ == "__main__":
    main()
