"""Lazily built tab panels: a tab's content is built the first time it is opened (UI helper).

Building every tab on each page load used to cost the full Browse first page, the Annotator
task, the Dataset config scan and more, before the user had even looked. :class:`LazyTabs`
creates the empty panels up front and fills each one on its first visit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class LazyTabs:
    """Tab panels whose content is built on first open.

    Must be created inside the page (a NiceGUI client context), under ``ui.tab_panels``.

    Args:
        builders: Tab name → function that builds the tab's content (called once, inside
            its panel), in display order.
    """

    def __init__(self, builders: Mapping[str, Callable[[], None]]) -> None:
        self._builders = dict(builders)
        self._panels = {name: ui.tab_panel(name) for name in self._builders}
        self._built: set[str] = set()

    def open(self, name: str) -> bool:
        """Make sure tab ``name`` is built.

        Returns:
            ``True`` if it was built just now (its content is fresh), ``False`` if it had
            been built before (or ``name`` is not a lazy tab).
        """
        if name in self._built or name not in self._builders:
            return False
        self._built.add(name)
        with self._panels[name]:
            self._builders[name]()
        return True

    def is_built(self, name: str) -> bool:
        """Whether tab ``name`` has been built in this page."""
        return name in self._built
