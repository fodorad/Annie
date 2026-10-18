"""Client-scoped refreshables: a refresh redraws only the browser tab that acted (UI helper).

NiceGUI's ``@ui.refreshable`` keeps one target per browser tab that built the view, and
``refresh()`` rebuilds **all** of them. Annie's views are module-level functions, so every
keystroke, click or tab switch in one tab used to rebuild that view in every open tab, all
on the one event loop.

:class:`client_refreshable` is a drop-in replacement:

- ``refresh()`` called while handling a tab (a NiceGUI page context is active) rebuilds only
  that tab's target. Other tabs catch up when they next build or open the view.
- ``refresh()`` called with no page context (start-up, a background service) rebuilds every
  tab, as before.
- :meth:`client_refreshable.refresh_everywhere` rebuilds every tab on purpose, for data that
  every open tab must show at once.
- :meth:`client_refreshable.refresh_others` rebuilds every tab except the acting one, for a
  data change whose acting tab will rebuild the view anyway when it next opens it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from nicegui import context
from nicegui.awaitable_response import AwaitableResponse
from nicegui.functions.refreshable import refreshable

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from nicegui import Client


def _acting_client() -> Client | None:
    """The browser tab whose handler is running, or ``None`` outside any page context."""
    try:
        return context.client
    except RuntimeError:  # no slot stack: start-up or a background task without a page
        return None


class client_refreshable(refreshable):  # noqa: N801 - mirrors NiceGUI's decorator name
    """``@ui.refreshable`` whose ``refresh()`` rebuilds only the acting tab (see module docs)."""

    def refresh(self, *args: Any, **kwargs: Any) -> AwaitableResponse:
        """Rebuild the acting tab's view, or every tab when no tab is acting.

        Accepts the same arguments as the view function (or a subset), like NiceGUI's.
        """
        return self._refresh_for(_acting_client(), args, kwargs)

    def refresh_everywhere(self, *args: Any, **kwargs: Any) -> AwaitableResponse:
        """Rebuild this view in every open tab (for data all tabs must show at once)."""
        return self._refresh_for(None, args, kwargs)

    def refresh_others(self, *args: Any, **kwargs: Any) -> AwaitableResponse:
        """Rebuild this view in every open tab except the acting one (all, outside a tab)."""
        return self._refresh_for(_acting_client(), args, kwargs, exclude=True)

    def _refresh_for(
        self,
        client: Client | None,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        exclude: bool = False,
    ) -> AwaitableResponse:
        """Rebuild the targets of ``client`` (``None``: all; ``exclude``: all but it)."""
        self.prune()
        instance = self.instance

        async def execute() -> None:
            awaitables = self._rebuild(client, args, kwargs, instance=instance, exclude=exclude)
            if awaitables:
                await asyncio.gather(*awaitables)

        name = getattr(self.func, "__name__", "view")
        return AwaitableResponse(execute, execute, name=f"refresh {name}")

    def _rebuild(
        self,
        client: Client | None,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        instance: Any,
        exclude: bool = False,
    ) -> list[Awaitable[Any]]:
        """Run NiceGUI's rebuild over the chosen targets only."""
        everything = self.targets
        if client is not None:
            self.targets = [t for t in everything if (t.container.client is client) != exclude]
        try:
            return self._execute_refresh(args, kwargs, instance=instance)
        finally:
            self.targets = everything
