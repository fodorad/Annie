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

From the first click until Annie answers, a small window shows the Annie icon, a progress
bar and a text box with what happens: the update check, the packages an update changes,
uv's download/install steps, and Annie starting. It closes by itself; if Annie cannot
start, it stays open and says why. uv's full output is kept in ``install.log``.

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
#: step's end at this pace, and only reaches it when the step is done: uv does not say
#: how far a download is when it is not writing to a terminal.
STEP_SECONDS = {"download": 45.0, "start": 6.0}


class Progress:
    """The progress bar's position: each step owns a slice of the bar.

    The bar never moves backwards, also not when the plan changes mid-way (a start that
    turns out to need an update gets more steps).
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
            # Up to 90% of the slice: the rest waits until the step is really done.
            target = self._floor + max(0.0, end - self._floor) * 0.9 * eased
            self.value = max(self.value, min(target, end))
        return self.value


class Reporter:
    """What the work reports to the window: steps, lines for the text box, the outcome.

    Thread-safe (the work runs in its own thread). Without a window (``--quiet``) the
    lines only go to install.log.
    """

    def __init__(self) -> None:
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()

    def heading(self, text: str) -> None:
        """Replace the window's heading."""
        self.events.put(("heading", text))

    def plan(self, plan: Plan, *, slow: bool = False) -> None:
        """Switch to ``plan``'s steps (e.g. once an update turns out to be needed)."""
        self.events.put(("plan", (plan, slow)))

    def step(self, name: str) -> None:
        """Step ``name`` begins."""
        self.events.put(("step", name))

    def note(self, text: str) -> None:
        """Add a line to the text box (and to install.log)."""
        log(text)
        self.show(text)

    def show(self, text: str) -> None:
        """Add a line to the text box only (for lines install.log already has)."""
        self.events.put(("note", text))

    def done(self) -> None:
        """Everything worked: the window closes."""
        self.events.put(("done", None))

    def fail(self, text: str) -> None:
        """Something failed: the window says why and stays open."""
        log(f"ERROR: {text}")
        self.events.put(("fail", text))


def install_with_progress(ui: Reporter, args: list[str]) -> None:
    """Run uv ``args`` (an install or update), showing the package changes and steps."""
    ui.step("resolve")
    ui.note("Checking which packages change...")
    preview: list[str] = []
    try:
        run([*args, "--dry-run"], preview.append)
    except RuntimeError:
        preview = []  # the real run below reports the problem
    changes = package_changes(preview)
    if changes:
        ui.note(f"{len(changes)} package(s) to download and install:")
        for change in changes:
            ui.note(f"    {change}")

    def on_line(line: str) -> None:
        summary, step = uv_step(line)
        if summary:
            ui.show(line)  # run() has logged it
        if step is not None:
            ui.step(step)

    ui.step("download")
    ui.note("Downloading...")
    run(args, on_line)


def create_environment(ui: Reporter) -> None:
    """Create the ``annie`` environment on the bundled Python and install Annie into it."""
    if installed_version() is None and ENV.exists():
        shutil.rmtree(ENV, ignore_errors=True)  # half-created: start over
    ui.step("environment")
    if not ENV_PYTHON.is_file():
        ui.note("Creating the annie environment...")
        run([str(UV), "venv", "--python", str(PYTHON), "--prompt", "annie", str(ENV)], log)
    install_with_progress(ui, install_args())


def update_if_newer(ui: Reporter) -> None:
    """Update to the newest Annie on PyPI if there is one; never stops Annie starting."""
    current = installed_version()
    ui.step("check")
    ui.note("Checking for updates...")
    latest = latest_version()
    if current is None or latest is None:
        ui.note("Could not reach the update server; starting the installed Annie.")
        return
    if not is_newer(latest, current):
        ui.note(f"Annie {current} is up to date.")
        return
    ui.heading(f"Updating Annie {current} → {latest}")
    ui.plan(UPDATE_START)
    ui.note(f"Annie {latest} is available (you have {current}).")
    try:
        install_with_progress(ui, update_args())
    except Exception as exc:  # noqa: BLE001 - the installed version still starts
        ui.note(f"The update did not finish ({exc}); starting the installed Annie {current}.")
        ui.note(f"Details: {LOG}")
        return
    ui.note(f"Updated to Annie {installed_version() or latest}.")


def launch_and_wait(ui: Reporter, timeout_s: float = 180.0) -> bool:
    """Start Annie and wait until it answers; report why when it does not."""
    ui.step("start")
    ui.heading("Starting Annie")
    ui.note("Starting Annie...")
    process = start_annie()
    deadline = time.monotonic() + timeout_s
    while not annie_running():
        code = process.poll()
        if code is not None:
            ui.fail(
                f"Annie stopped while starting (exit code {code}). Details are in "
                f"{data_dir() / 'logs'} and {LOG}."
            )
            return False
        if time.monotonic() > deadline:
            ui.fail(f"Annie did not start within {timeout_s:.0f} seconds. Details: {LOG}")
            return False
        time.sleep(0.5)
    ui.note("Annie is running and opens in your browser.")
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


def with_window(
    heading: str,
    plan: Plan,
    work: Callable[[Reporter], object],
    *,
    slow: bool = False,
    close_on_failure: bool = False,
) -> bool:
    """Show the progress window while ``work`` runs; return whether it succeeded.

    The window has the Annie icon, a heading, the progress bar and a text box with what
    happens. It closes by itself when the work is done. On a failure it stays open with
    the reason and a Close button, unless ``close_on_failure`` (the installer, which
    shows its own message).
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
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    top = ttk.Frame(frame)
    top.pack(fill="x")
    with contextlib.suppress(tk.TclError):
        logo = tk.PhotoImage(file=str(APP / "annie.png"))
        ttk.Label(top, image=logo).pack(side="left", padx=(0, 12))
        root.logo = logo  # type: ignore[attr-defined] - keep a reference, or Tk drops it
    title = ttk.Label(top, text=heading, font=("Segoe UI", 12))
    title.pack(side="left", anchor="w")
    bar = ttk.Progressbar(frame, mode="determinate", maximum=1000, length=480)
    bar.pack(fill="x", pady=(12, 8))
    text = tk.Text(
        frame, height=9, width=68, wrap="word", relief="flat", borderwidth=0,
        font=("Segoe UI", 9), background=root.cget("background"),
    )  # fmt: skip
    text.pack(fill="both", expand=True)
    text.configure(state="disabled")
    close = ttk.Button(frame, text="Close", command=root.destroy)
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

    def add_line(line: str) -> None:
        text.configure(state="normal")
        text.insert("end", line + "\n")
        text.see("end")
        text.configure(state="disabled")

    def poll() -> None:
        now = time.monotonic()
        while not ui.events.empty():
            kind, value = ui.events.get_nowait()
            if kind == "heading":
                title.configure(text=str(value))
            elif kind == "plan":
                progress.plan, progress.slow = value  # type: ignore[misc]
            elif kind == "step":
                progress.begin(str(value), now)
            elif kind == "note":
                add_line(str(value))
            elif kind == "done":
                outcome["ok"] = True
                progress.finish()
                bar.configure(value=1000)
                root.after(500, root.destroy)
                return
            elif kind == "fail":
                outcome["ok"] = False
                add_line(f"\n{value}")
                title.configure(text="Something went wrong", foreground="#b00020")
                if close_on_failure:
                    root.after(500, root.destroy)
                    return
                close.pack(anchor="e", pady=(8, 0))
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
        ui.heading("Preparing Annie for first use")
        ui.plan(FIRST_START, slow=True)
        ui.note("Downloading Annie and its components: several hundred MB, once.")
        create_environment(ui)
    else:
        update_if_newer(ui)
    return launch_and_wait(ui)


def main(argv: list[str]) -> int:
    """Launcher entry point; see the module docstring for the modes."""
    if "--install" in argv:
        migrate_legacy_data()
        if "--quiet" in argv:
            try:
                create_environment(Reporter())
            except Exception as exc:  # noqa: BLE001 - reported via exit code + log
                log(f"ERROR: {exc}")
                return 1
            return 0
        ok = with_window(
            "Downloading Annie and its components", SETUP, create_environment,
            slow=True, close_on_failure=True,
        )  # fmt: skip
        return 0 if ok else 1

    if "--update" in argv and "--after-pid" in argv:
        with contextlib.suppress(ValueError, IndexError):
            wait_for_exit(int(argv[argv.index("--after-pid") + 1]))
        return 0 if with_window("Updating Annie", QUICK_START, start) else 1

    if annie_running():
        webbrowser.open(f"http://127.0.0.1:{port()}/")
        return 0
    migrate_legacy_data()
    return 0 if with_window("Starting Annie", QUICK_START, start) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
