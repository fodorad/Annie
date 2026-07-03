"""Deferred UI work that survives a body refresh (UI helper).

The Browse/Annotator rows decode frames lazily. Doing that with a per-row
``ui.timer`` is fragile: NiceGUI's ``Timer._run_once`` enters its parent slot
*before* it checks for cancellation, so a one-shot timer whose row was refreshed
away raises ``RuntimeError: The parent slot ... has been deleted`` — flooding the
log.

So at **runtime** (event loop running) we defer with a tracked background task,
which has no parent slot to delete; the coroutine itself guards every UI mutation.
Only at **build time** (auto-index, no loop yet) do we fall back to a one-shot
timer parented to a persistent host, which is safe because nothing is being
refreshed during the initial build.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import background_tasks, core, ui

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import Any


def schedule(host: ui.element, factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Run ``factory()`` soon, surviving a refresh of the calling row.

    Args:
        host: A persistent element used only for the build-time timer fallback.
        factory: A zero-arg coroutine function producing the deferred work.
    """
    if core.loop is None:  # build time: no running loop yet
        with host:
            ui.timer(0.05, factory, once=True)
    else:  # runtime: a tracked task has no parent slot to be deleted
        background_tasks.create(factory(), name="annie-lazy")
