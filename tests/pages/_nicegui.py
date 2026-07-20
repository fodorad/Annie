"""Shared NiceGUI test scaffolding for page-level unit tests.

NiceGUI tracks the current UI slot in a :class:`contextvars.ContextVar`, which is
per-asyncio-task. ``IsolatedAsyncioTestCase`` runs each test in a fresh task, so a
test that builds UI must first establish a slot — otherwise element creation raises
"the slot stack ... is empty". Entering a throwaway :class:`~nicegui.Client` provides
that slot, making each async page test self-contained regardless of run order.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import TYPE_CHECKING

from nicegui import Client
from nicegui.page import page as _Page

if TYPE_CHECKING:
    from collections.abc import Iterator


def quiet_slow_callback_warnings(seconds: float = 2.0) -> None:
    """Raise the running loop's slow-callback threshold so real-but-slow tests are quiet.

    ``unittest.IsolatedAsyncioTestCase`` always runs its loop with ``debug=True``, which
    logs ``Executing <Task ...> took N seconds`` for any coroutine over asyncio's default
    100 ms threshold. Page tests that build several rows and decode frames legitimately
    exceed that, printing a noisy (but harmless) warning mid-run. Bumping the threshold
    keeps the output clean without disabling debug mode. Call from ``asyncSetUp``.

    Args:
        seconds: New slow-callback threshold for the current event loop.
    """
    asyncio.get_running_loop().slow_callback_duration = seconds


@contextmanager
def ui_client() -> Iterator[Client]:
    """Yield a NiceGUI client whose slot is active, so UI can be built inside it."""
    client = Client(_Page("/"), request=None)
    with client:
        yield client
