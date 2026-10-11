r"""Annie launcher for Windows, installed by Annie-Setup.exe (see annie.iss).

Started by the Start-menu and desktop shortcuts with the bundled ``python\pythonw.exe``,
so no console window appears. It lives outside the ``annie`` environment (``env\``) on
purpose: it can then create, repair and update that environment while Annie is closed.

What a start does:

1. If Annie is already running, open it in the browser and stop.
2. If the environment is missing or broken, create it (first use, or after a repair).
3. Otherwise ask PyPI for the newest Annie and, if it is newer, update to it.
   Offline, or if anything fails, the installed version starts anyway.
4. Start Annie with its notification-area icon (``pythonw -m annie.desktop``).

Long steps show a small progress window; uv's output is kept in ``install.log``.

Other modes:
  ``--install [--quiet]``     create/refresh the environment and exit (the installer runs
                              this, so the first start needs no download).
  ``--update --after-pid N``  the tray's "Check for updates": wait for Annie (process N)
                              to exit, update, then start it again.

Stdlib only: the bundled Python has nothing else installed. Paths come from annie.ini,
which the installer writes once; everything here reads them from there.
"""

from __future__ import annotations

import configparser
import contextlib
import ctypes
import json
import os
import queue
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# Bump together with REQUIRED_LAUNCHER_VERSION in annie/core/runtime.py when this file,
# the bundled Python or FFmpeg changes in a way Annie relies on.
LAUNCHER_VERSION = 2

APP = Path(__file__).resolve().parent
PYTHON = APP / "python" / "python.exe"
ENV = APP / "env"
ENV_PYTHON = ENV / "Scripts" / "python.exe"
ENV_PYTHONW = ENV / "Scripts" / "pythonw.exe"
UV = APP / "uv.exe"
FFMPEG_BIN = APP / "ffmpeg" / "bin"
LOG = APP / "install.log"
ICON = APP / "annie.ico"

PYPI_URL = "https://pypi.org/pypi/annie/json"
#: What gets installed; ANNIE_PACKAGE lets CI install a locally built wheel instead.
PACKAGE = os.environ.get("ANNIE_PACKAGE") or "annie[media,desktop]"
#: Work left in the user folder by the first installer, moved to the data folder once.
LEGACY_ITEMS = ("configs", "logs", "sessions", "tmp", "last_config.json")

CREATE_NO_WINDOW = 0x08000000


# ── settings ──────────────────────────────────────────────────────────────────


def data_dir() -> Path:
    """The data folder chosen in the installer (annie.ini), with %VARS% expanded."""
    ini = configparser.ConfigParser(interpolation=None)
    ini.read(APP / "annie.ini", encoding="utf-8")
    raw = ini.get("paths", "data", fallback="") or r"%USERPROFILE%\Annie\Data"
    return Path(os.path.expandvars(raw))


def port() -> int:
    """The port Annie serves on (ANNIE_PORT, default 8080)."""
    try:
        return int(os.environ.get("ANNIE_PORT", "") or 8080)
    except ValueError:
        return 8080


def annie_environment() -> dict[str, str]:
    """Environment for Annie: its folders, the bundled FFmpeg, and this launcher's version."""
    env = dict(os.environ)
    data = data_dir()
    env["ANNIE_HOME"] = str(data)
    env.setdefault("ANNIE_CONFIG_DIR", str(data / "configs"))
    # The example datasets (installer option, or the tray's "Download example datasets").
    env.setdefault("ANNIE_DATA_DIR", str(data / "examples"))
    env.setdefault("ANNIE_OPEN_BROWSER", "1")
    env["ANNIE_FFMPEG_LIB_DIR"] = str(FFMPEG_BIN)
    env["PATH"] = f"{FFMPEG_BIN};{env.get('PATH', '')}"
    env["ANNIE_LAUNCHER_VERSION"] = str(LAUNCHER_VERSION)
    env["ANNIE_INSTALL_DIR"] = str(APP)
    return env


def uv_environment() -> dict[str, str]:
    """Environment for uv: everything stays in this folder, nothing in %TEMP% or AppData.

    No cache, no user/system uv.toml, and never a uv-managed Python download: the env is
    built on the bundled interpreter, whose plain folder has no directory junction for
    Windows to refuse ("untrusted mount point", os error 448).
    """
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env.update(
        UV_NO_CACHE="1",
        UV_NO_CONFIG="1",
        UV_PYTHON_DOWNLOADS="never",
        UV_HTTP_TIMEOUT="60",
        UV_NO_PROGRESS="1",
    )
    return env


# ── versions ──────────────────────────────────────────────────────────────────


def parse_version(raw: str) -> tuple[int, ...] | None:
    """Numeric release part of a version (same rules as annie.core.runtime.parse_version)."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", raw.strip())
    return tuple(int(p) for p in match.group(1).split(".")) if match else None


def is_newer(latest: str, current: str) -> bool:
    """Whether release ``latest`` is newer than ``current`` (garbage is never newer)."""
    new, old = parse_version(latest), parse_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def installed_version() -> str | None:
    """The Annie version in the environment, or ``None`` if it is not (fully) installed."""
    if not ENV_PYTHONW.is_file():
        return None
    for meta in (ENV / "Lib" / "site-packages").glob("annie-*.dist-info/METADATA"):
        with contextlib.suppress(OSError):
            for line in meta.read_text(encoding="utf-8").splitlines():
                if line.startswith("Version:"):
                    return line.split(":", 1)[1].strip()
    return None


def tls_context() -> ssl.SSLContext:
    """TLS settings that trust PyPI on a fresh Windows, too.

    Python only sees the root certificates Windows has already cached; Windows fetches
    the others on demand, but only for its own TLS stack, so a new PC can fail where the
    browser works. certifi's bundle (installed in the env along with NiceGUI) fills the gap.
    """
    context = ssl.create_default_context()
    bundle = ENV / "Lib" / "site-packages" / "certifi" / "cacert.pem"
    if bundle.is_file():
        with contextlib.suppress(OSError, ssl.SSLError):
            context.load_verify_locations(cafile=str(bundle))
    return context


def latest_version() -> str | None:
    """The newest Annie on PyPI, or ``None`` when offline."""
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=4, context=tls_context()) as response:  # noqa: S310
            version = json.load(response)["info"]["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return version if isinstance(version, str) else None


# ── running Annie ─────────────────────────────────────────────────────────────


def annie_running() -> bool:
    """Whether Annie already answers on its port (its page title says so)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port()}/", timeout=3) as r:  # noqa: S310
            return b"<title>Annie</title>" in r.read(65536)
    except OSError:
        return False


def wait_for_exit(pid: int, timeout_s: float = 60.0) -> None:
    """Wait until process ``pid`` has exited, so its files can be replaced."""
    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return  # already gone
    try:
        ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout_s * 1000))
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def start_annie() -> None:
    """Start Annie with its tray icon, detached from this launcher."""
    # Annie releases before the tray (1.12 and older) have no annie.desktop.
    tray = (ENV / "Lib" / "site-packages" / "annie" / "desktop.py").is_file()
    subprocess.Popen(  # noqa: S603 - fixed argv inside the install folder
        [str(ENV_PYTHONW), "-m", "annie.desktop" if tray else "annie.app"],
        cwd=APP,
        env=annie_environment(),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


# ── installing ────────────────────────────────────────────────────────────────


def log(line: str) -> None:
    """Append a line to install.log (kept small: restarted when over 1 MB)."""
    with contextlib.suppress(OSError):
        if LOG.exists() and LOG.stat().st_size > 1_000_000:
            LOG.unlink()
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")


def run(args: list[str], status: Callable[[str], None]) -> None:
    """Run uv, streaming each output line to the log and the progress window."""
    log("$ " + " ".join(args))
    process = subprocess.Popen(  # noqa: S603 - fixed argv inside the install folder
        args,
        cwd=APP,
        env=uv_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip()
        if line:
            log(line)
            status(line)
    if process.wait():
        raise RuntimeError(
            f"{Path(args[0]).name} {args[1]} failed (exit code {process.returncode})"
        )


def install(status: Callable[[str], None]) -> None:
    """Create the ``annie`` environment on the bundled Python and install Annie into it."""
    if installed_version() is None and ENV.exists():
        shutil.rmtree(ENV, ignore_errors=True)  # half-created: start over
    if not ENV_PYTHON.is_file():
        run(
            [str(UV), "venv", "--python", str(PYTHON), "--prompt", "annie", str(ENV)],
            status,
        )
    run([str(UV), "pip", "install", "--python", str(ENV_PYTHON), PACKAGE], status)


def update(status: Callable[[str], None]) -> None:
    """Update Annie; its dependencies change only where the new Annie needs them to."""
    run(
        [
            str(UV), "pip", "install", "--python", str(ENV_PYTHON),
            "--upgrade-package", "annie", PACKAGE,
        ],
        status,
    )  # fmt: skip


def migrate_legacy_data() -> None:
    r"""Move the first installer's data (kept directly in %USERPROFILE%\Annie) once.

    Only into a new data folder *inside* %USERPROFILE%\Annie (the default, Annie\Data),
    and only while that folder does not exist yet, so nothing is ever overwritten and
    work never leaves the Annie folder the user knows. With any other data folder the old
    work stays where it is.
    """
    legacy = Path(os.path.expandvars(r"%USERPROFILE%\Annie"))
    data = data_dir()
    if data.exists() or data.parent != legacy or not legacy.is_dir():
        return
    items = [legacy / name for name in LEGACY_ITEMS] + sorted(legacy.glob("annie_*.db*"))
    items = [item for item in items if item.exists()]
    if not items:
        return
    data.mkdir(parents=True)
    for item in items:
        with contextlib.suppress(OSError):
            shutil.move(str(item), str(data / item.name))
            log(f"moved {item} -> {data / item.name}")


# ── progress window ───────────────────────────────────────────────────────────


def with_progress(title: str, work: Callable[[Callable[[str], None]], None]) -> bool:
    """Run ``work`` while a small window shows ``title`` and uv's latest line.

    Returns:
        Whether ``work`` finished without an error (errors are written to install.log).
    """
    import tkinter as tk
    from tkinter import ttk

    lines: queue.Queue[str] = queue.Queue()
    outcome: dict[str, BaseException | None] = {}

    def worker() -> None:
        try:
            work(lines.put)
            outcome["error"] = None
        except BaseException as exc:  # noqa: BLE001 - reported to the user below
            log(f"ERROR: {exc}")
            outcome["error"] = exc

    root = tk.Tk()
    root.title("Annie")
    with contextlib.suppress(tk.TclError):
        root.iconbitmap(str(ICON))
    root.resizable(False, False)
    root.protocol("WM_DELETE_WINDOW", lambda: None)  # finishing is safer than stopping halfway
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=title, font=("Segoe UI", 11)).pack(anchor="w")
    bar = ttk.Progressbar(frame, mode="indeterminate", length=420)
    bar.pack(fill="x", pady=(10, 6))
    bar.start(12)
    detail = ttk.Label(frame, text="", foreground="#666666", width=60)
    detail.pack(anchor="w")
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry(f"+{x}+{y}")

    def poll() -> None:
        last = None
        while not lines.empty():
            last = lines.get_nowait()
        if last is not None:
            detail.config(text=last if len(last) <= 70 else last[:67] + "...")
        if "error" in outcome:
            root.destroy()
        else:
            root.after(150, poll)

    threading.Thread(target=worker, daemon=True).start()
    root.after(150, poll)
    root.mainloop()
    return outcome.get("error") is None


def message(text: str, *, error: bool = False, ask: bool = False) -> bool:
    """Show a message box; with ``ask``, return whether the user chose Yes."""
    flags = 0x10000 | (0x10 if error else 0x40)  # MB_SETFOREGROUND | icon
    if ask:
        flags = 0x10000 | 0x20 | 0x4  # question icon, Yes/No
    return ctypes.windll.user32.MessageBoxW(None, text, "Annie", flags) == 6


# ── entry points ──────────────────────────────────────────────────────────────


def ensure_installed() -> bool:
    """Install Annie if needed (first use or after a repair); return whether it is there."""
    if installed_version() is not None:
        return True
    ok = with_progress(
        "Preparing Annie for first use. This one-time download is several hundred MB...",
        install,
    )
    if not ok:
        message(
            "Annie could not be installed. Check the internet connection, then start "
            f"Annie again.\n\nDetails are in:\n{LOG}",
            error=True,
        )
    return ok


def update_if_newer(*, from_menu: bool = False) -> None:
    """Update to the newest Annie on PyPI, if there is a newer one."""
    current = installed_version()
    latest = latest_version()
    if current is None or latest is None or not is_newer(latest, current):
        return
    if not with_progress(f"Updating Annie to v{latest}...", update) and from_menu:
        message(
            f"The update did not finish, so the installed Annie v{current} starts.\n\n"
            f"Details are in:\n{LOG}",
            error=True,
        )


def main(argv: list[str]) -> int:
    """Launcher entry point; see the module docstring for the modes."""
    if "--install" in argv:
        migrate_legacy_data()
        if "--quiet" in argv:
            try:
                install(lambda _line: None)
            except Exception as exc:  # noqa: BLE001 - reported via exit code + log
                log(f"ERROR: {exc}")
                return 1
            return 0
        return (
            0
            if with_progress(
                "Downloading Annie and its components. This takes a few minutes...", install
            )
            else 1
        )

    if "--update" in argv and "--after-pid" in argv:
        with contextlib.suppress(ValueError, IndexError):
            wait_for_exit(int(argv[argv.index("--after-pid") + 1]))
        if ensure_installed():
            update_if_newer(from_menu=True)
            start_annie()
        return 0

    if annie_running():
        webbrowser.open(f"http://127.0.0.1:{port()}/")
        return 0
    migrate_legacy_data()
    if not ensure_installed():
        return 1
    update_if_newer()
    start_annie()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
