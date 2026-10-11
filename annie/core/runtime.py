"""Launch-time helpers for running Annie as an installed desktop app (infrastructure).

Concerns of the installed app rather than of the UI, kept out of :mod:`annie.app` and
:mod:`annie.desktop` so they can be unit-tested:

* **FFmpeg DLLs on Windows.** Since Python 3.8, Windows no longer searches ``PATH`` for
  the DLLs an extension module depends on, so torchcodec cannot find FFmpeg's
  ``avcodec-*.dll`` & co. unless their folder is registered with
  :func:`os.add_dll_directory` before torchcodec is imported.
* **A second launch.** Double-clicking the shortcut while Annie is already running must
  open the running instance instead of crashing on a busy port.
* **Launcher age.** The Windows installer's launcher (``installer/windows/launcher.pyw``)
  stamps its version into ``ANNIE_LAUNCHER_VERSION``. Annie itself updates on every
  start, but the launcher, the bundled Python and FFmpeg only change when the user
  reinstalls, so Annie tells them when that is needed.
* **Updates.** The tray's "Check for updates" compares the running version with the
  newest release on PyPI.
* **The Annie folder.** The tray opens the folder that holds Annie's program and data.
* **Example datasets.** The installer can include two small example datasets; the tray
  downloads them later when they were left out.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import socket
import ssl
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from annie.core.config import Settings

REQUIRED_LAUNCHER_VERSION = 2
"""Oldest ``installer/windows/launcher.pyw`` version this Annie works with.

Bump this together with ``LAUNCHER_VERSION`` in the launcher whenever the launcher, its
bundled Python or FFmpeg changes in a way this Annie relies on. Version 2 is the tray
launcher with the bundled Python; installs still running ``Annie.cmd`` (version 1) are
asked to run the new installer.
"""

INSTALLER_URL = "https://github.com/fodorad/Annie/releases/latest/download/Annie-Setup.exe"
"""Permanent download link to the newest Windows installer."""

PYPI_URL = "https://pypi.org/pypi/annie/json"
"""PyPI's JSON description of the ``annie`` project; ``info.version`` is the newest release."""

EXAMPLES_URL = "https://github.com/fodorad/Annie/releases/latest/download/annie-examples.zip"
"""The example datasets (the repo's ``data/config`` and ``data/example``), per release."""

EXAMPLE_FOLDERS = ("config", "example")
"""The top-level folders of the examples archive; nothing else is extracted from it."""

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


def parse_version(raw: str) -> tuple[int, ...] | None:
    """Return the numeric release part of a version string, or ``None`` if there is none.

    ``"1.12.0"`` gives ``(1, 12, 0)``. Anything after the release numbers (``.dev3``,
    ``rc1``, ``+g1a2b3c``) is ignored, so a development build of 1.13.0 compares equal to
    the 1.13.0 release rather than newer.

    Args:
        raw: A version string such as ``"1.12.0"`` or ``"v1.12.1.dev2+gabc"``.
    """
    match = re.match(r"v?(\d+(?:\.\d+)*)", raw.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    """Return whether release ``latest`` is newer than the running ``current`` version.

    Unreadable versions never count as newer, so a garbled answer from PyPI cannot
    trigger an update. Missing trailing parts count as zero (``1.12`` equals ``1.12.0``).
    """
    new, old = parse_version(latest), parse_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def _tls_context() -> ssl.SSLContext:
    """TLS that trusts certifi's root certificates on top of the system ones.

    On Windows, Python only sees the roots the OS has already cached, so a fresh PC can
    fail to verify PyPI or GitHub without them.
    """
    context = ssl.create_default_context()
    with contextlib.suppress(ImportError, OSError, ssl.SSLError):
        import certifi

        context.load_verify_locations(cafile=certifi.where())
    return context


def latest_pypi_version(timeout: float = 5.0) -> str | None:  # pragma: no cover - network
    """Return the newest Annie release on PyPI, or ``None`` when PyPI cannot be reached."""
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=timeout, context=_tls_context()) as response:  # noqa: S310
            version = json.load(response)["info"]["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return version if isinstance(version, str) else None


def annie_folder(settings: Settings, install_dir: Path | None) -> Path:
    r"""Return the folder the tray's "Open Annie folder" opens.

    With the installer's defaults that is ``%USERPROFILE%\Annie``, which holds both the
    program (``App``) and your work (``Data``). When the two were put in unrelated
    places, or Annie was not started by the Windows launcher, it is the data folder.

    Args:
        settings: The running app's settings, which know where the data lives.
        install_dir: The ``ANNIE_INSTALL_DIR`` folder, or ``None`` when not installed.
    """
    home = settings.annie_home
    if install_dir is not None and install_dir.parent == home.parent:
        return install_dir.parent
    return home


def examples_installed(directory: Path) -> bool:
    """Whether the example datasets' configs are in ``directory`` (an ``ANNIE_DATA_DIR``)."""
    return any((directory / "config").glob("*.json"))


def extract_examples(archive: Path, directory: Path) -> int:
    """Unpack the examples archive into ``directory`` and return the number of files.

    Only entries under ``config/`` and ``example/`` are extracted, and none may escape
    ``directory``, so a damaged or hostile archive cannot write anywhere else.

    Args:
        archive: The downloaded ``annie-examples.zip``.
        directory: The ``ANNIE_DATA_DIR`` folder; created when missing.

    Raises:
        ValueError: If the archive holds no example configs.
        zipfile.BadZipFile: If it is not a zip archive.
    """
    root = directory.resolve()
    count = 0
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            parts = [p for p in info.filename.replace("\\", "/").split("/") if p]
            if info.is_dir() or not parts or parts[0] not in EXAMPLE_FOLDERS:
                continue
            target = root.joinpath(*parts).resolve()
            if not target.is_relative_to(root):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    if not examples_installed(root):
        raise ValueError("the archive holds no example datasets")
    return count


def download_examples(directory: Path, timeout: float = 60.0) -> int:  # pragma: no cover - network
    """Download the example datasets into ``directory``; see :func:`extract_examples`.

    The archive is kept inside ``directory`` only while it is unpacked.

    Raises:
        OSError: If the download fails.
        ValueError: If the download is not a valid examples archive.
    """
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "annie-examples.zip.part"
    try:
        with (
            urllib.request.urlopen(
                EXAMPLES_URL, timeout=timeout, context=_tls_context()
            ) as response,  # noqa: S310
            archive.open("wb") as out,
        ):
            shutil.copyfileobj(response, out)
        return extract_examples(archive, directory)
    except zipfile.BadZipFile as exc:
        raise ValueError("the download is not a zip archive") from exc
    finally:
        archive.unlink(missing_ok=True)
