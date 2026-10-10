"""Launch-time helpers for running Annie as an installed desktop app (infrastructure).

Three concerns that only matter at process start, kept out of :mod:`annie.app` so they
can be unit-tested:

* **FFmpeg DLLs on Windows.** Since Python 3.8, Windows no longer searches ``PATH`` for
  the DLLs an extension module depends on, so torchcodec cannot find FFmpeg's
  ``avcodec-*.dll`` & co. unless their folder is registered with
  :func:`os.add_dll_directory` before torchcodec is imported.
* **A second launch.** Double-clicking the shortcut while Annie is already running must
  open the running instance instead of crashing on a busy port.
* **Launcher age.** The Windows installer's launcher (``installer/windows/Annie.cmd``)
  stamps its version into ``ANNIE_LAUNCHER_VERSION``. Annie itself updates on every
  start, but the launcher and bundled FFmpeg only change when the user reinstalls, so
  Annie tells them when that is needed.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

REQUIRED_LAUNCHER_VERSION = 1
"""Oldest ``installer/windows/Annie.cmd`` version this Annie works with.

Bump this together with ``ANNIE_LAUNCHER_VERSION`` in the launcher whenever the launcher
or its bundled FFmpeg changes in a way this Annie relies on.
"""

INSTALLER_URL = "https://github.com/fodorad/Annie/releases/latest/download/Annie-Setup.exe"
"""Permanent download link to the newest Windows installer."""

_DLL_DIRECTORIES: list[object] = []
"""Handles returned by :func:`os.add_dll_directory`; kept alive so the paths stay registered."""


def ffmpeg_dll_dir(override: str | None, ffmpeg_exe: str | None) -> Path | None:
    """Return the folder holding FFmpeg's shared libraries, or ``None`` if unknown.

    On Windows the DLLs sit next to ``ffmpeg.exe`` in a shared build's ``bin`` folder.

    Args:
        override: The ``ANNIE_FFMPEG_LIB_DIR`` value, if any; wins when it is a folder.
        ffmpeg_exe: The ``ffmpeg`` executable found on ``PATH``, if any.

    Returns:
        The folder to register, or ``None`` when neither source points at one.
    """
    if override and override.strip() and Path(override.strip()).is_dir():
        return Path(override.strip())
    if ffmpeg_exe:
        return Path(ffmpeg_exe).parent
    return None


def ensure_windows_ffmpeg_dlls() -> Path | None:
    """On Windows, register FFmpeg's DLL folder so torchcodec can load it.

    Idempotent and a no-op on other platforms. Call before importing torchcodec.

    Returns:
        The folder registered by this call, or ``None`` if nothing was registered.
    """
    if sys.platform != "win32" or _DLL_DIRECTORIES:
        return None
    folder = ffmpeg_dll_dir(os.environ.get("ANNIE_FFMPEG_LIB_DIR"), shutil.which("ffmpeg"))
    if folder is None:
        return None
    _DLL_DIRECTORIES.append(os.add_dll_directory(str(folder)))
    return folder


def _probe_host(host: str) -> str:
    """Map a bind-all address to loopback, which is where a local client connects."""
    return "127.0.0.1" if host in ("", "0.0.0.0", "::") else host  # noqa: S104


def app_url(host: str, port: int) -> str:
    """Return the browser URL for an Annie server bound to ``host:port``."""
    return f"http://{_probe_host(host)}:{port}/"


def port_in_use(host: str, port: int) -> bool:
    """Return whether something already accepts connections on ``host:port``."""
    try:
        with socket.create_connection((_probe_host(host), port), timeout=0.5):
            return True
    except OSError:
        return False


def annie_running(host: str, port: int) -> bool:
    """Return whether the server on ``host:port`` is Annie (its page title says so).

    Distinguishes a second launch of Annie from an unrelated app holding the port.
    """
    if not port_in_use(host, port):
        return False
    try:
        with urllib.request.urlopen(app_url(host, port), timeout=3) as response:  # noqa: S310
            return b"<title>Annie</title>" in response.read(65536)
    except (OSError, urllib.error.URLError):
        return False


def launcher_outdated(raw_version: str | None) -> bool:
    """Return whether the Windows launcher that started Annie is too old.

    Args:
        raw_version: The ``ANNIE_LAUNCHER_VERSION`` value; empty or ``None`` when Annie
            was not started by the installer's launcher.

    Returns:
        ``True`` only for a readable version below :data:`REQUIRED_LAUNCHER_VERSION`.
    """
    try:
        return int(raw_version or "") < REQUIRED_LAUNCHER_VERSION
    except ValueError:
        return False
