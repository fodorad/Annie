r"""Annie launcher for Windows, installed by Annie-Setup.exe (see annie.iss).

Started by the "Annie" shortcut in the Annie folder (and the optional desktop shortcut)
with the bundled ``python\pythonw.exe``, so no console window appears. It lives outside
the ``annie`` environment (``env\``) on purpose: it can then create, repair and update
that environment while Annie is closed.

What a start does:

1. If Annie is already running, open it in the browser and stop.
2. If the environment is missing or broken, create it (first use, or after a repair).
3. Otherwise ask PyPI for the newest Annie and, if it is newer, update to it.
   Offline, or if anything fails, the installed version starts anyway.
4. Start Annie with its notification-area icon (``pythonw -m annie.desktop``) and wait
   until it answers.

From the first click until Annie answers, a small window shows the Annie logo and name, one
status line ("Checking for updates...", "Downloading: 230 of 820 MB", "Starting..."), a
progress bar and a small info box (installed and latest version, whether an update is
needed, the packages). It closes by itself; if Annie cannot start, it says why and offers
the log. Everything in full detail (uv's output) is kept in ``install.log``.

Other modes:
  ``--install --progress``    setup's download step: create the environment and report into
                              the installer window through stdout (``@status <text>``,
                              ``@progress <0..1000>``, other lines for its log box).
  ``--install [--quiet]``     create/refresh the environment and exit, with the small
                              window (or, with --quiet, with none).
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
import math
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
# the bundled Python or FFmpeg changes in a way Annie relies on. 3: the bundled Python
# carries the Visual C++ runtime (fetch-deps.ps1).
LAUNCHER_VERSION = 3

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


def start_annie() -> subprocess.Popen:
    """Start Annie with its tray icon, detached from this launcher; return its process."""
    if (ENV / "Lib" / "site-packages" / "annie" / "desktop.py").is_file():
        return subprocess.Popen(  # noqa: S603 - fixed argv inside the install folder
            [str(ENV_PYTHONW), "-m", "annie.desktop"],
            cwd=APP,
            env=annie_environment(),
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    # Annie 1.12 and older have no tray, and their web server crashes without console
    # streams (pythonw has none): give them a console, hidden.
    return subprocess.Popen(  # noqa: S603 - fixed argv inside the install folder
        [str(ENV_PYTHON), "-m", "annie.app"],
        cwd=APP,
        env=annie_environment(),
        creationflags=CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
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


def run(args: list[str], on_line: Callable[[str], None]) -> None:
    """Run uv, passing each output line to the log and to ``on_line``."""
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
            on_line(line)
    if process.wait():
        raise RuntimeError(
            f"{Path(args[0]).name} {args[1]} failed (exit code {process.returncode})"
        )


def install_args() -> list[str]:
    """The uv command that installs Annie into the environment."""
    return [str(UV), "pip", "install", "--python", str(ENV_PYTHON), PACKAGE]


def update_args() -> list[str]:
    """The uv command that updates Annie; dependencies change only where Annie needs it."""
    return [
        str(UV), "pip", "install", "--python", str(ENV_PYTHON),
        "--upgrade-package", "annie", PACKAGE,
    ]  # fmt: skip


def package_changes(lines: list[str]) -> list[str]:
    """Readable package changes from uv's ``+ name==version`` / ``- name==version`` lines.

    A package removed and added again is an update and becomes one line,
    ``annie 1.13.1 → 1.13.2``; the rest are new or removed packages. A package from a
    file (``annie @ file:///.../annie-1.13.2-py3-none-any.whl``, how CI and ``make
    installer-try`` install a local build) takes its version from the wheel's name.
    """
    added: dict[str, str] = {}
    removed: dict[str, str] = {}
    for line in lines:
        match = re.match(r"\s*([+-])\s+([A-Za-z0-9._-]+)\s*(?:==(\S+)|@\s*(\S+))", line)
        if not match:
            continue
        sign, name, version, url = match.groups()
        if version is None:
            wheel = url.rsplit("/", 1)[-1]
            parts = wheel.split("-")
            version = parts[1] if wheel.endswith(".whl") and len(parts) > 2 else "local build"
        (added if sign == "+" else removed)[name] = version
    changes = [
        f"{name} {removed[name]} → {version}" if name in removed else f"{name} {version} (new)"
        for name, version in sorted(added.items())
    ]
    changes += [
        f"{name} {version} (removed)"
        for name, version in sorted(removed.items())
        if name not in added
    ]
    return changes


#: uv's summary lines, and the step that begins once each has been printed.
UV_STEPS = (("Resolved ", "download"), ("Prepared ", "install"), ("Installed ", None))


def uv_step(line: str) -> tuple[bool, str | None]:
    """Whether ``line`` is one of uv's summary lines, and the step it starts (if any)."""
    for prefix, step in UV_STEPS:
        if line.startswith(prefix):
            return True, step
    return False, None


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


# ── progress ──────────────────────────────────────────────────────────────────

Plan = tuple[tuple[str, float], ...]

#: The steps of each kind of start, and where on the bar (0..1) each one ends.
QUICK_START: Plan = (("check", 0.15), ("start", 1.0))
UPDATE_START: Plan = (
    ("check", 0.05), ("resolve", 0.12), ("download", 0.70), ("install", 0.85), ("start", 1.0),
)  # fmt: skip
FIRST_START: Plan = (
    ("environment", 0.04), ("resolve", 0.12), ("download", 0.82), ("install", 0.92),
    ("start", 1.0),
)  # fmt: skip
SETUP: Plan = (("environment", 0.05), ("resolve", 0.12), ("download", 0.88), ("install", 1.0))

#: Roughly how many seconds each step takes. Inside a step the bar eases toward the
#: step's end at this pace, and only reaches it when the step is done.
STEP_SECONDS = {"download": 45.0, "start": 6.0}


class Progress:
    """The progress bar's position: each step owns a slice of the bar.

    Inside a step the bar eases toward the step's end, and :meth:`advance` moves it to a
    known fraction of the step (e.g. how many megabytes are downloaded). It never moves
    backwards, also not when the plan changes mid-way (a start that turns out to need an
    update gets more steps).
    """

    def __init__(self, plan: Plan, *, slow: bool = False) -> None:
        self.value = 0.0
        self.plan = plan
        self.slow = slow  # a first install downloads far more than an update
        self._floor = 0.0
        self._step: str | None = None
        self._since = 0.0

    def _bounds(self, step: str) -> tuple[float, float] | None:
        start = 0.0
        for name, end in self.plan:
            if name == step:
                return start, end
            start = end
        return None

    def begin(self, step: str, now: float) -> None:
        """Step ``step`` started at time ``now`` (seconds, any monotonic clock)."""
        self.value = self.at(now)
        bounds = self._bounds(step)
        if bounds is None:
            return  # not part of this plan: hold still
        self._floor = max(self.value, bounds[0])
        self.value = self._floor
        self._step = step
        self._since = now

    def advance(self, fraction: float, now: float) -> None:
        """The current step is known to be ``fraction`` (0..1) done."""
        bounds = self._bounds(self._step) if self._step is not None else None
        if bounds is None:
            return
        start, end = bounds
        known = start + (end - start) * min(max(fraction, 0.0), 1.0)
        self.value = self.at(now)
        if known > self._floor:
            self._floor = max(known, self.value)
            self.value = self._floor
            self._since = now

    def finish(self) -> None:
        """Everything is done: a full bar."""
        self._step = None
        self._floor = self.value = 1.0

    def at(self, now: float) -> float:
        """The bar's position at time ``now``."""
        bounds = self._bounds(self._step) if self._step is not None else None
        if bounds is not None:
            end = bounds[1]
            pace = STEP_SECONDS.get(self._step or "", 2.0) * (4 if self.slow else 1)
            eased = 1.0 - math.exp(-max(0.0, now - self._since) / pace)
            # Up to 90% of what is left: the rest waits until the step is really done.
            target = self._floor + max(0.0, end - self._floor) * 0.9 * eased
            self.value = max(self.value, min(target, end))
        return self.value


_SIZE_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}


def uv_download(line: str) -> tuple[str, str, int] | None:
    """Parse uv's large-download notices: ``Downloading torch (241.5MiB)``, ``Downloaded torch``.

    Returns:
        ``("start", package, bytes)``, ``("done", package, 0)``, or ``None`` for other lines.
    """
    match = re.match(r"\s*Downloading (\S+) \(([\d.]+)\s*([KMG]?i?B)\)\s*$", line)
    if match:
        name, amount, unit = match.groups()
        return "start", name, int(float(amount) * _SIZE_UNITS.get(unit, 1))
    match = re.match(r"\s*Downloaded (\S+)\s*$", line)
    if match:
        return "done", match.group(1), 0
    return None


class Downloads:
    """How far uv's large downloads are, from its ``Downloading``/``Downloaded`` lines.

    uv announces every large download with its size and reports when it is done; small
    packages are not announced and are negligible next to PyTorch.
    """

    def __init__(self) -> None:
        self.sizes: dict[str, int] = {}
        self.finished: set[str] = set()

    def feed(self, line: str) -> bool:
        """Take one uv output line; return whether it was a download notice."""
        event = uv_download(line)
        if event is None:
            return False
        kind, name, size = event
        if kind == "start":
            self.sizes[name] = size
        else:
            self.finished.add(name)
        return True

    @property
    def total(self) -> int:
        """Bytes of all announced downloads."""
        return sum(self.sizes.values())

    @property
    def done(self) -> int:
        """Bytes of the finished downloads."""
        return sum(size for name, size in self.sizes.items() if name in self.finished)

    def fraction(self) -> float:
        """The share of announced bytes that are downloaded (0 when nothing is announced)."""
        return self.done / self.total if self.total else 0.0

    def describe(self) -> str:
        """``Downloading: 230 of 820 MB``."""
        mb = 1024**2
        return f"Downloading: {self.done / mb:.0f} of {self.total / mb:.0f} MB"


class Reporter:
    """What the work reports: a status line, details, progress, and the outcome.

    The start window shows only the **status** and the progress bar; **details** (the
    package list, uv's lines) go to install.log, and to setup's log box via
    :class:`StreamReporter`. Thread-safe: the work runs in its own thread, and the window
    reads :attr:`events`. Without a window (``--quiet``) everything only goes to the log.
    """

    def __init__(self) -> None:
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()

    def _emit(self, kind: str, value: object) -> None:
        self.events.put((kind, value))

    def status(self, text: str) -> None:
        """What is happening now, in a few words (also logged)."""
        log(text)
        self._emit("status", text)

    def detail(self, text: str) -> None:
        """A line for the log (and setup's log box), not for the start window."""
        log(text)
        self.show(text)

    def show(self, text: str) -> None:
        """Like :meth:`detail`, for lines install.log already has."""
        self._emit("detail", text)

    def plan(self, plan: Plan, *, slow: bool = False) -> None:
        """Switch to ``plan``'s steps (e.g. once an update turns out to be needed)."""
        self._emit("plan", (plan, slow))

    def step(self, name: str) -> None:
        """Step ``name`` begins."""
        self._emit("step", name)

    def advance(self, fraction: float) -> None:
        """The current step is ``fraction`` (0..1) done."""
        self._emit("advance", fraction)

    def done(self) -> None:
        """Everything worked: the window closes."""
        self._emit("done", None)

    def fail(self, text: str) -> None:
        """Something failed: the window says why and stays open."""
        log(f"ERROR: {text}")
        self._emit("fail", text)


#: Characters setup's log box may not show (its output is read as plain ASCII).
_ASCII = str.maketrans({"→": "->", "…": "...", "–": "-", "—": "-", "’": "'"})


def ascii_line(text: str) -> str:
    """``text`` in plain ASCII for setup's log box, which reads the output as ASCII."""
    return text.translate(_ASCII).encode("ascii", "replace").decode("ascii")


class StreamReporter(Reporter):
    """Reports to the installer window through stdout (``launcher.pyw --install --progress``).

    Setup reads every line (Inno's ``ExecAndLogOutput``): ``@status <text>`` sets its
    status line, ``@progress <0..1000>`` its progress bar, and every other line goes to
    its log box. A heartbeat keeps the bar moving during long downloads.
    """

    def __init__(self, plan: Plan, *, out: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self.progress = Progress(plan, slow=True)
        self._out = out or (lambda line: print(line, flush=True))
        self._lock = threading.Lock()
        self._last = -1
        self._stop = threading.Event()

    def _write(self, line: str) -> None:
        with self._lock:
            self._out(ascii_line(line))

    def _emit(self, kind: str, value: object) -> None:
        now = time.monotonic()
        if kind == "status":
            self._write(f"@status {value}")
        elif kind == "detail":
            self._write(str(value))
        elif kind == "fail":
            self._write(f"@status {value}")
            self._write(f"ERROR: {value}")
        elif kind == "plan":
            self.progress.plan, self.progress.slow = value  # type: ignore[misc]
        elif kind == "step":
            self.progress.begin(str(value), now)
        elif kind == "advance":
            self.progress.advance(float(value), now)  # type: ignore[arg-type]
        elif kind == "done":
            self.progress.finish()
        self.beat(now)

    def beat(self, now: float | None = None) -> None:
        """Send the bar's position if it moved."""
        value = int(self.progress.at(time.monotonic() if now is None else now) * 1000)
        if value != self._last:
            self._last = value
            self._write(f"@progress {value}")

    def run_heartbeat(self, every: float = 0.5) -> threading.Thread:
        """Keep sending the eased position until :meth:`stop_heartbeat`."""

        def loop() -> None:
            while not self._stop.wait(every):
                self.beat()

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        return thread

    def stop_heartbeat(self) -> None:
        """Stop :meth:`run_heartbeat`."""
        self._stop.set()


def packages(count: int) -> str:
    """``1 package``, ``3 packages``."""
    return f"{count} package" + ("" if count == 1 else "s")


def install_with_progress(ui: Reporter, args: list[str]) -> None:
    """Run uv ``args`` (an install or update), reporting the packages, sizes and steps."""
    ui.step("resolve")
    ui.status("Checking which packages are needed...")
    preview: list[str] = []
    try:
        run([*args, "--dry-run"], preview.append)
    except RuntimeError:
        preview = []  # the real run below reports the problem
    changes = package_changes(preview)
    if changes:
        ui.detail(f"{packages(len(changes))} to download and install:")
        for change in changes:
            ui.detail(f"    {change}")

    downloads = Downloads()

    def on_line(line: str) -> None:
        if downloads.feed(line):
            ui.show(line)
            ui.advance(downloads.fraction())
            ui.status(downloads.describe())
            return
        summary, step = uv_step(line)
        if summary:
            ui.show(line)  # run() has logged it
        if step == "install":
            ui.status("Installing packages...")
        if step is not None:
            ui.step(step)

    ui.step("download")
    ui.status(f"Downloading {packages(len(changes))}..." if changes else "Downloading...")
    run(args, on_line)


def create_environment(ui: Reporter) -> None:
    """Create the ``annie`` environment on the bundled Python and install Annie into it."""
    if installed_version() is None and ENV.exists():
        shutil.rmtree(ENV, ignore_errors=True)  # half-created: start over
    ui.step("environment")
    if not ENV_PYTHON.is_file():
        ui.status("Creating the Python environment...")
        run([str(UV), "venv", "--python", str(PYTHON), "--prompt", "annie", str(ENV)], log)
    install_with_progress(ui, install_args())
    ui.status("Components installed.")


def update_if_newer(ui: Reporter) -> None:
    """Update to the newest Annie on PyPI if there is one; never stops Annie starting."""
    current = installed_version()
    ui.step("check")
    ui.status("Checking for updates...")
    ui.detail(f"Installed: {current}")
    latest = latest_version()
    if current is None or latest is None:
        ui.detail("Could not reach the update server. Starting the installed version.")
        return
    if not is_newer(latest, current):
        ui.detail(f"Latest: {latest}. No update needed.")
        return
    ui.plan(UPDATE_START)
    ui.detail(f"Latest: {latest}. Updating {current} → {latest}.")
    ui.status(f"Updating {current} → {latest}...")
    try:
        install_with_progress(ui, update_args())
    except Exception as exc:  # noqa: BLE001 - the installed version still starts
        ui.detail(f"The update did not finish ({exc}). Starting {current}.")
        return
    ui.detail(f"Updated to {installed_version() or latest}.")


def launch_and_wait(ui: Reporter, timeout_s: float = 180.0) -> bool:
    """Start Annie and wait until it answers; report why when it does not."""
    ui.step("start")
    ui.status("Starting...")
    process = start_annie()
    deadline = time.monotonic() + timeout_s
    while not annie_running():
        code = process.poll()
        if code is not None:
            ui.fail(f"Stopped while starting (exit code {code}).")
            return False
        if time.monotonic() > deadline:
            ui.fail(f"Did not start within {timeout_s:.0f} seconds.")
            return False
        time.sleep(0.5)
    ui.detail(f"Running at http://127.0.0.1:{port()}/")
    ui.done()
    return True


# ── the window ────────────────────────────────────────────────────────────────


def run_headless(work: Callable[[Reporter], object]) -> bool:
    """Run ``work`` without a window (its lines still go to install.log)."""
    ui = Reporter()
    try:
        if work(ui) is False:
            return False
    except Exception as exc:  # noqa: BLE001 - reported via the return value + log
        log(f"ERROR: {exc}")
        return False
    events = []
    while not ui.events.empty():
        events.append(ui.events.get_nowait()[0])
    return "fail" not in events


def run_streaming(
    plan: Plan, work: Callable[[Reporter], object], ui: StreamReporter | None = None
) -> bool:
    """Run ``work`` reporting to setup's window through stdout; return whether it worked.

    ``ui`` defaults to a reporter writing to stdout (another one can collect the lines).
    """
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    ui = StreamReporter(plan) if ui is None else ui
    ui.run_heartbeat()
    try:
        ok = work(ui) is not False
    except Exception as exc:  # noqa: BLE001 - reported via the exit code + setup's log box
        ui.fail(str(exc) or type(exc).__name__)
        ok = False
    else:
        if ok:
            ui.done()
    finally:
        ui.stop_heartbeat()
    return ok


def open_log() -> None:
    """Open install.log in the default text editor."""
    with contextlib.suppress(OSError):
        os.startfile(LOG)  # noqa: S606 - a fixed file in the install folder


def with_window(
    status: str,
    plan: Plan,
    work: Callable[[Reporter], object],
    *,
    slow: bool = False,
    close_on_failure: bool = False,
) -> bool:
    """Show the start window while ``work`` runs; return whether it succeeded.

    The Annie logo and name, one status line, the progress bar, and a small info box with
    the details: the installed and the latest version, whether an update is needed, the
    packages being downloaded. It closes by itself when the work is done. On a failure
    the status line turns red with the reason, and **Open log** / **Close** appear
    (unless ``close_on_failure``).
    """
    import tkinter as tk
    from tkinter import ttk

    ui = Reporter()
    progress = Progress(plan, slow=slow)
    outcome: dict[str, bool] = {}

    def worker() -> None:
        try:
            result = work(ui)
        except Exception as exc:  # noqa: BLE001 - shown in the window
            ui.fail(str(exc) or type(exc).__name__)
            return
        if result is not False:  # False: the work has reported its failure itself
            ui.done()

    try:
        root = tk.Tk()
    except tk.TclError:  # no desktop to show a window on: do the work without one
        return run_headless(work)
    root.title("Annie")
    with contextlib.suppress(tk.TclError):
        root.iconbitmap(str(ICON))
    root.resizable(False, False)
    root.protocol("WM_DELETE_WINDOW", lambda: None)  # finishing is safer than stopping halfway
    frame = ttk.Frame(root, padding=(20, 18, 20, 18))
    frame.pack(fill="both", expand=True)
    top = ttk.Frame(frame)
    top.pack(fill="x")
    with contextlib.suppress(tk.TclError):
        logo = tk.PhotoImage(file=str(APP / "annie.png")).subsample(2)  # 56 -> 28 px
        ttk.Label(top, image=logo).pack(side="left", padx=(0, 10))
        root.logo = logo  # type: ignore[attr-defined] - keep a reference, or Tk drops it
    ttk.Label(top, text="Annie", font=("Segoe UI Semibold", 12)).pack(side="left")
    line = ttk.Label(
        frame, text=status, font=("Segoe UI", 9), foreground="#4b5563", width=58, anchor="w"
    )
    line.pack(fill="x", pady=(12, 6))
    bar = ttk.Progressbar(frame, mode="determinate", maximum=1000, length=400)
    bar.pack(fill="x")
    # The info box: a few lines of detail, newest at the bottom.
    info = tk.Text(
        frame, height=5, width=58, wrap="none", relief="solid", borderwidth=1,
        font=("Consolas", 8), foreground="#4b5563", background="#fafafa",
        highlightthickness=0, padx=6, pady=4,
    )  # fmt: skip
    info.pack(fill="x", pady=(10, 0))
    info.configure(state="disabled")
    buttons = ttk.Frame(frame)

    def add_info(text: str) -> None:
        info.configure(state="normal")
        if info.get("1.0", "end-1c"):
            info.insert("end", "\n")
        info.insert("end", text)
        info.see("end")
        info.configure(state="disabled")

    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry(f"+{x}+{y}")
    # In front of the browser and other windows while it works (it is short-lived and
    # closes by itself), so the user sees that Annie is starting.
    root.attributes("-topmost", True)
    root.lift()
    with contextlib.suppress(tk.TclError):
        root.focus_force()

    def poll() -> None:
        now = time.monotonic()
        while not ui.events.empty():
            kind, value = ui.events.get_nowait()
            if kind == "status":
                line.configure(text=str(value))
            elif kind == "detail":
                add_info(str(value))
            elif kind == "plan":
                progress.plan, progress.slow = value  # type: ignore[misc]
            elif kind == "step":
                progress.begin(str(value), now)
            elif kind == "advance":
                progress.advance(float(value), now)  # type: ignore[arg-type]
            elif kind == "done":
                outcome["ok"] = True
                progress.finish()
                bar.configure(value=1000)
                root.after(400, root.destroy)
                return
            elif kind == "fail":
                outcome["ok"] = False
                line.configure(text=str(value), foreground="#b00020", wraplength=400)
                if close_on_failure:
                    root.after(500, root.destroy)
                    return
                root.attributes("-topmost", False)
                buttons.pack(anchor="e", pady=(14, 0))
                ttk.Button(buttons, text="Open log", command=open_log).pack(side="left")
                ttk.Button(buttons, text="Close", command=root.destroy).pack(
                    side="left", padx=(8, 0)
                )
                root.protocol("WM_DELETE_WINDOW", root.destroy)
                return
        bar.configure(value=int(progress.at(now) * 1000))
        root.after(100, poll)

    threading.Thread(target=worker, daemon=True).start()
    root.after(100, poll)
    root.mainloop()
    return outcome.get("ok", False)


# ── entry points ──────────────────────────────────────────────────────────────


def start(ui: Reporter) -> bool:
    """A start from the shortcut: install if needed, update if newer, start, wait."""
    if installed_version() is None:
        ui.plan(FIRST_START, slow=True)
        ui.status("Preparing for first use...")
        create_environment(ui)
    else:
        update_if_newer(ui)
    return launch_and_wait(ui)


def main(argv: list[str]) -> int:
    """Launcher entry point; see the module docstring for the modes."""
    if "--install" in argv:
        migrate_legacy_data()
        if "--progress" in argv:  # setup: report into the installer window
            return 0 if run_streaming(SETUP, create_environment) else 1
        if "--quiet" in argv:
            try:
                create_environment(Reporter())
            except Exception as exc:  # noqa: BLE001 - reported via exit code + log
                log(f"ERROR: {exc}")
                return 1
            return 0
        ok = with_window(
            "Downloading Annie and its components...", SETUP, create_environment, slow=True
        )
        return 0 if ok else 1

    if "--update" in argv and "--after-pid" in argv:
        with contextlib.suppress(ValueError, IndexError):
            wait_for_exit(int(argv[argv.index("--after-pid") + 1]))
        return 0 if with_window("Updating...", QUICK_START, start) else 1

    if annie_running():
        webbrowser.open(f"http://127.0.0.1:{port()}/")
        return 0
    migrate_legacy_data()
    return 0 if with_window("Starting...", QUICK_START, start) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
