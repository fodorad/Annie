"""Bug reports from the Log tab: what is sent, where to, and how often (service).

The Log tab's **Report** button emails an error to Annie's maintainer. Annie cannot send
mail itself, so the report goes to a form-to-email service (`Web3Forms
<https://web3forms.com>`_) over HTTPS, from the user's browser, where Annie's UI runs.
The service's access key decides the recipient, so no email address is stored here;
the key is public by design (it can only deliver to that one inbox).

Before anything is sent, the user sees the exact text (:func:`build_report`) and may add a
note. Each error can be reported once, and at most :data:`MAX_REPORTS_PER_HOUR` reports
leave one Annie per hour, so a crash loop cannot flood the inbox.
"""

from __future__ import annotations

import os
import platform
import sys
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from annie.core.logbook import LogEvent

REPORT_SUBJECT = "Annie bug report"
"""The email subject of every report."""

REPORT_URL = "https://api.web3forms.com/submit"
"""Where reports are posted (JSON); the service emails them to the maintainer."""

ACCESS_KEY = "2ac1f9d9-3a01-4c94-a74c-87c623e9717f"
"""The Web3Forms access key bound to the maintainer's inbox (public by design).

It can only deliver to that inbox; if it is ever abused, reset it on web3forms.com and
ship the new one in a release. ``ANNIE_REPORT_KEY`` in the environment overrides it
(e.g. to test with another inbox); an empty key turns reporting off.
"""

MAX_REPORTS_PER_HOUR = 5
"""How many reports one Annie process sends per hour at most."""

_lock = threading.Lock()
_sent: set[int] = set()  #: log event ``seq`` numbers reported in this process
_recent: deque[float] = deque()  #: when the reports of the last hour were sent


def access_key() -> str:
    """The access key in use: ``ANNIE_REPORT_KEY`` if set, else :data:`ACCESS_KEY`."""
    return os.environ.get("ANNIE_REPORT_KEY", "").strip() or ACCESS_KEY


def reporting_available() -> bool:
    """Whether reports can be sent (an access key is configured)."""
    return bool(access_key())


def build_report(event: LogEvent, note: str, *, version: str) -> str:
    """The plain text that is sent, exactly as the user previews it.

    Args:
        event: The logged error being reported.
        note: What the user was doing (optional; may be empty).
        version: The running Annie version.

    Returns:
        The report: the note, the error with its full details, and where it happened
        (Annie, Python and OS versions). Nothing else from the PC is included.
    """
    lines = []
    if note.strip():
        lines += ["What I was doing:", note.strip(), ""]
    lines += [
        f"Error: {event.message}",
        f"Time: {event.time_text}",
        f"Annie: {version}",
        f"Python: {sys.version.split()[0]}",
        f"OS: {platform.platform()}",
        "",
        "Details:",
        event.details or "(none)",
    ]
    return "\n".join(lines)


def payload(text: str) -> dict[str, str]:
    """The JSON body the form-to-email service expects."""
    return {
        "access_key": access_key(),
        "subject": REPORT_SUBJECT,
        "from_name": "Annie",
        "message": text,
    }


def already_sent(seq: int) -> bool:
    """Whether the error with log sequence number ``seq`` was reported already."""
    with _lock:
        return seq in _sent


def can_send(now: float | None = None) -> bool:
    """Whether another report fits within :data:`MAX_REPORTS_PER_HOUR`."""
    now = time.monotonic() if now is None else now
    with _lock:
        while _recent and now - _recent[0] >= 3600:
            _recent.popleft()
        return len(_recent) < MAX_REPORTS_PER_HOUR


def mark_sent(seq: int, now: float | None = None) -> None:
    """Remember that the error ``seq`` was reported, for the Sent state and the limit."""
    now = time.monotonic() if now is None else now
    with _lock:
        _sent.add(seq)
        _recent.append(now)


def reset() -> None:
    """Forget sent reports (for tests)."""
    with _lock:
        _sent.clear()
        _recent.clear()
