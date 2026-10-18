"""A real Annie server for end-to-end tests: ``python -m annie.app`` on a free port.

Each server gets its own throwaway ``ANNIE_HOME``, so a test never sees another test's
review state, and never the developer's own. The server runs as a separate process, as
in production; under ``coverage run`` it is measured too (``patch = ["subprocess"]``).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
"""The repository root (this file lives in ``<repo>/tests/e2e``)."""

EXAMPLE_CONFIG = REPO / "data" / "config" / "mosei_mini.json"
"""The bundled example dataset (3 public videos with vdet, tracks and labels)."""

_KEEP = frozenset({"ANNIE_FFMPEG_LIB_DIR"})

BOOT_TIMEOUT = 120.0
"""Seconds to wait for a cold start (importing torch and the first scan)."""


def free_port() -> int:
    """A TCP port nobody listens on right now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class AnnieServer:
    """One Annie process with its own home.

    Attributes:
        config: A dataset config to open on start (as if it was the last one used), or
            ``None`` for a first run with no dataset.
        env: Extra environment variables for the process.
        home: The process's ``ANNIE_HOME`` (a fresh temporary directory).
        port: The port it listens on.
    """

    config: Path | None = EXAMPLE_CONFIG
    env: dict[str, str] = field(default_factory=dict)
    home: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="annie-e2e-")))
    port: int = field(default_factory=free_port)
    _process: subprocess.Popen[bytes] | None = None
    _log: Path | None = None
    _final_output: str = ""

    @property
    def url(self) -> str:
        """The app's base URL."""
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        """Launch the server and wait until it serves the page.

        Raises:
            RuntimeError: If the process exits or does not answer within
                :data:`BOOT_TIMEOUT`; the message carries the end of its output.
        """
        if self.config is not None:
            pointer = {"config": str(self.config.resolve())}
            (self.home / "last_config.json").write_text(json.dumps(pointer), encoding="utf-8")
        # The developer's own ANNIE_* settings (dataset folders, database) never leak in;
        # only where FFmpeg lives is kept, as the machine needs it to decode at all.
        env = {k: v for k, v in os.environ.items() if not k.startswith("ANNIE_") or k in _KEEP}
        env.update(self.env)
        env.update(ANNIE_HOME=str(self.home), ANNIE_PORT=str(self.port), ANNIE_HOST="127.0.0.1")
        self._log = self.home / "server.log"
        with self._log.open("wb") as log:
            self._process = subprocess.Popen(
                [sys.executable, "-m", "annie.app"],
                cwd=REPO,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                # Windows: its own console group, so a Ctrl+Break stops only this server.
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        deadline = time.monotonic() + BOOT_TIMEOUT
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"Annie exited during startup:\n{self.output()}")
            try:
                with urllib.request.urlopen(self.url, timeout=2):  # noqa: S310 - localhost
                    return
            except (urllib.error.URLError, OSError):
                time.sleep(0.25)
        self.stop()
        raise RuntimeError(f"Annie did not answer on {self.url}:\n{self.output()}")

    def output(self) -> str:
        """The end of the server's console output (for failure messages)."""
        if self._log is None or not self._log.exists():
            return self._final_output
        return self._log.read_text(encoding="utf-8", errors="replace")[-4000:]

    def stop(self) -> None:
        """Stop the server the way a user does (Ctrl+C), then remove its home.

        A graceful stop runs the app's shutdown hooks and lets coverage save its data.
        """
        if self._process is not None and self._process.poll() is None:
            interrupt = signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT
            self._process.send_signal(interrupt)
            try:
                self._process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        self._final_output = self.output()
        shutil.rmtree(self.home, ignore_errors=True)
