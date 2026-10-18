"""Which tab a browser shows, so key handlers and pollers run only while theirs is visible.

NiceGUI keeps every tab panel alive once built, and a ``ui.keyboard`` listens on the whole
document. Without this, the Annotator's keys (Space, the arrows, digits) still acted while
the reviewer typed or scrolled on Browse, and the 10 Hz playhead poll kept talking to the
browser from a hidden tab.

Elements with an ``active`` flag (``ui.keyboard``, ``ui.timer``) are registered with
:func:`only_while_shown`; :func:`show` (called on every tab switch) turns them on for the
visible tab and off for the others.
"""

from __future__ import annotations

from typing import Protocol

from nicegui import context


class _Switchable(Protocol):
    """An element that can be switched on and off (``ui.keyboard``, ``ui.timer``)."""

    active: bool

    @property
    def is_deleted(self) -> bool:
        """Whether the element was removed from its page."""
        ...


#: The tab each browser tab shows, keyed by client id.
_shown: dict[str, str] = {}

#: Per client: the registered elements and the tab each belongs to.
_members: dict[str, list[tuple[str, _Switchable]]] = {}


def shown_tab() -> str | None:
    """The tab the current browser tab shows (``None`` before the first :func:`show`)."""
    return _shown.get(context.client.id)


def show(tab: str) -> None:
    """Record that the current browser tab now shows ``tab``; switch members on or off."""
    client = context.client
    if client.id not in _shown:
        client.on_delete(lambda: (_shown.pop(client.id, None), _members.pop(client.id, None)))
    _shown[client.id] = tab
    members = [(name, el) for name, el in _members.get(client.id, []) if not el.is_deleted]
    _members[client.id] = members
    for name, element in members:
        element.active = name == tab


def only_while_shown[T: _Switchable](element: T, tab: str) -> T:
    """Keep ``element`` active only while ``tab`` is the shown tab of its browser tab.

    Args:
        element: A ``ui.keyboard`` or ``ui.timer`` built inside ``tab``.
        tab: The tab name it belongs to (e.g. ``"annotator"``).

    Returns:
        The element, already switched to match the tab shown now.
    """
    cid = context.client.id
    live = [(name, el) for name, el in _members.get(cid, []) if not el.is_deleted]
    _members[cid] = [*live, (tab, element)]  # rebuilds replace members: drop deleted ones
    element.active = _shown.get(cid, tab) == tab
    return element
