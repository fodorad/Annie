"""Switching Annotator tasks rebuilds the body; choosing the shown task keeps it."""

from __future__ import annotations

from annie.dataset.sources import TaskKind
from annie.pages import annotator
from tests.pages._dataset import DatasetCase
from tests.pages._nicegui import ui_client


class TestTaskSwitch(DatasetCase):
    def _ids(self, client) -> set[int]:  # noqa: ANN001
        return {el.id for el in client.layout.descendants()}

    async def test_choosing_the_shown_task_keeps_the_rows(self) -> None:
        with ui_client() as client:
            annotator.render()
            await self.settle()
            shown = annotator._current_task()  # noqa: SLF001
            before = self._ids(client)
            annotator._select_task(shown)  # noqa: SLF001
            await self.settle()
            self.assertEqual(self._ids(client), before)

    async def test_choosing_another_task_rebuilds_the_rows(self) -> None:
        with ui_client() as client:
            annotator.render()
            await self.settle()
            before = self._ids(client)
            annotator._select_task(TaskKind.PROTAGONIST)  # noqa: SLF001
            await self.settle()
            self.assertEqual(annotator._current_task(), TaskKind.PROTAGONIST)  # noqa: SLF001
            self.assertNotEqual(self._ids(client), before)
