"""Central event log — a dated file plus an in-memory store for the Log tab (infra).

Errors that happen in the background (lazy decode, render workers, the conversion
batch, NiceGUI's own callbacks) used to escape to the terminal and were easy to
miss. The log book gives them one home:

* every event is appended to an in-memory ring buffer the **Log tab** reads, and
* mirrored to a per-session file (``Annie_YYYY-MM-DD_HH-MM-SS.log``) so each
  startup gets its own log and multiple same-day runs stay distinct.

The store is event-sourced: each event has a monotonic ``seq`` so a per-client
poller can fetch only what is new (for toasts) without re-rendering everything. It
is pure stdlib and thread-safe, so render/convert worker threads can report too.
"""

from __future__ import annotations

import logging
import threading
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LEVELS: tuple[str, ...] = ("error", "warning", "info")
"""The levels an event can carry, most severe first."""

#: Annie level name → stdlib :mod:`logging` level, for mirroring to a log file.
_PY_LEVEL = {"error": logging.ERROR, "warning": logging.WARNING, "info": logging.INFO}


@dataclass(slots=True)
class LogEvent:
    """One recorded event.

    Attributes:
        seq: Monotonic sequence number (for incremental polling).
        timestamp: When it was recorded.
        level: One of :data:`LEVELS`.
        message: Short, one-line summary.
        details: Optional long text (e.g. a traceback) for the expandable view.
    """

    seq: int
    timestamp: datetime
    level: str
    message: str
    details: str = ""

    @property
    def time_text(self) -> str:
        """The timestamp formatted as ``YYYY-MM-DD HH:MM:SS``."""
        return self.timestamp.strftime("%Y-%m-%d %H:%M:%S")

    def as_clipboard(self) -> str:
        """The event rendered as plain text for the Copy button."""
        head = f"[{self.time_text}] {self.level.upper()}: {self.message}"
        return f"{head}\n{self.details}" if self.details else head


class LogBook:
    """A thread-safe, bounded event store mirrored to a dated log file."""

    def __init__(self, capacity: int = 2000) -> None:
        """Create an empty store.

        Args:
            capacity: Maximum number of events kept in memory (oldest dropped).
        """
        self._events: deque[LogEvent] = deque(maxlen=capacity)
        self._seq = 0
        self._lock = threading.Lock()
        self._logger: logging.Logger | None = None
        self.log_path: Path | None = None

    def attach_file(self, log_dir: str | Path) -> Path:
        """Mirror events to ``<log_dir>/Annie_<date>_<time>.log`` and return the file path.

        Args:
            log_dir: Directory to write the dated log file into (created if needed).

        Returns:
            The log file path.
        """
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"Annie_{datetime.now():%Y-%m-%d_%H-%M-%S}.log"
        logger = logging.getLogger("annie.events")
        logger.setLevel(logging.INFO)
        logger.propagate = False  # keep it out of the root/console handlers
        if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        self._logger = logger
        self.log_path = path
        return path

    def add(self, level: str, message: str, details: str = "") -> LogEvent:
        """Record an event (and mirror it to the file, if attached).

        Args:
            level: One of :data:`LEVELS`.
            message: One-line summary.
            details: Optional long text.

        Returns:
            The stored :class:`LogEvent`.
        """
        with self._lock:
            self._seq += 1
            event = LogEvent(self._seq, datetime.now(), level, message, details)
            self._events.append(event)
        if self._logger is not None:
            self._logger.log(
                _PY_LEVEL.get(level, logging.INFO),
                message + (f"\n{details}" if details else ""),
            )
        return event

    def events(self) -> list[LogEvent]:
        """Return a snapshot of all stored events, oldest first."""
        with self._lock:
            return list(self._events)

    def since(self, seq: int) -> tuple[list[LogEvent], int]:
        """Return events newer than ``seq`` and the latest sequence number.

        Args:
            seq: The last sequence number the caller has already seen.

        Returns:
            A ``(new_events, latest_seq)`` pair.
        """
        with self._lock:
            new = [e for e in self._events if e.seq > seq]
            return new, self._seq

    def latest_seq(self) -> int:
        """Return the most recent sequence number (``0`` when empty)."""
        with self._lock:
            return self._seq

    def clear(self) -> None:
        """Drop all in-memory events (the file is untouched)."""
        with self._lock:
            self._events.clear()


LOG = LogBook()
"""The process-wide event log."""


def report(message: str, *, level: str = "error", details: str = "") -> LogEvent:
    """Record an event on the global :data:`LOG`.

    Args:
        message: One-line summary.
        level: One of :data:`LEVELS`.
        details: Optional long text.

    Returns:
        The stored :class:`LogEvent`.
    """
    return LOG.add(level, message, details)


def report_exception(message: str, exc: BaseException | None) -> LogEvent:
    """Record an error event whose details are ``exc``'s formatted traceback.

    Args:
        message: One-line summary.
        exc: The exception to format into the details, or ``None``.

    Returns:
        The stored :class:`LogEvent`.
    """
    details = ""
    if exc is not None:
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
    return LOG.add("error", message, details)
