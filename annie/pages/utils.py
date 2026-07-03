"""Shared UI helpers used by multiple page modules (UI)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nicegui import ui


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
