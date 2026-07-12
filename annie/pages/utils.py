"""Shared UI helpers used by multiple page modules (UI)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from nicegui import ui

from annie.core.config import settings
from annie.core.state import state
from annie.pages.lazy import schedule

if TYPE_CHECKING:
    from collections.abc import Callable

    from nicegui import Client


def render_embed_ttl() -> float:
    """Idle-revert delay for an embedded *rendered* clip.

    Capped at the temp-dir sweep TTL so a rendered clip's ``ui.video`` always reverts
    to its placeholder no later than the periodic sweep deletes the underlying file —
    the element never points at a clip that has been reclaimed from disk.
    """
    return min(state.ui.embed_ttl_seconds, settings.temp_ttl_seconds)


def notify_detached(client: Client, message: str, *, color: str) -> None:
    """Toast from a handler that tore down the slot it was invoked in.

    ``ui.notify`` resolves its client through the slot the handler runs in, which
    NiceGUI takes from the sender's **parent** element. A handler that deletes its
    own row — or refreshes the container holding its button — drops the last
    reference to that parent, so a later ``ui.notify`` dies with "the parent element
    this slot belongs to has been deleted".

    Re-entering ``client`` lands in :attr:`nicegui.Client.content`, which outlives
    any refresh, so the toast survives the teardown that caused it.

    Args:
        client: The client captured *before* the teardown (``context.client``).
        message: The toast text.
        color: The toast colour.
    """
    with client:
        ui.notify(message, color=color)


def unembed_after_idle(
    box: ui.element, restore: Callable[[], None], *, ttl: float | None = None
) -> None:
    """Swap an embedded clip back to its placeholder once it has sat idle.

    An embedded ``ui.video`` keeps its clip buffered in the browser tab (and its
    element alive server-side) for as long as the row is on the page. A reviewer
    who plays a dozen clips while scrolling never gets that memory back. So each
    embed schedules its own reversal: after ``ttl`` seconds, ``restore`` rebuilds the
    cheap placeholder and the clip is dropped.

    The wait runs via :func:`annie.pages.lazy.schedule`, so a row refreshed away
    mid-wait has no parent slot to be deleted; ``restore`` is skipped when the box
    no longer exists.

    Args:
        box: The container holding the embedded clip.
        restore: Rebuilds the placeholder content inside ``box``. Must clear it.
        ttl: Idle seconds before reverting. Defaults to
            :attr:`annie.core.state.UiSettings.embed_ttl_seconds`; rendered clips pass
            :func:`render_embed_ttl` so they never outlive the temp-dir sweep.
    """
    delay = ttl if ttl is not None else state.ui.embed_ttl_seconds

    async def _revert() -> None:
        await asyncio.sleep(delay)
        if not _alive(box):
            return  # the row was refreshed or the page closed while we waited
        with contextlib.suppress(RuntimeError):
            restore()

    schedule(box, _revert)


def _alive(element: ui.element) -> bool:
    """Whether it is safe to mutate ``element`` (its client and element both exist).

    Accessing ``element.client`` *raises* once the client is deleted, and mutating a
    deleted element *warns*, so any lazy task that resumes after a reload or refresh
    must call this before touching the DOM.

    Args:
        element: The NiceGUI element to check.

    Returns:
        ``True`` if the element's client is alive and the element has not been deleted.
    """
    return element._client() is not None and not element.is_deleted  # noqa: SLF001
