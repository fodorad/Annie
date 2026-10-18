"""The table dialogs end to end: a label CSV and a workbook sheet become label sources."""

from __future__ import annotations

import asyncio

from nicegui import ui

from annie.dataset.sources import CsvRole
from annie.pages import csv_dialog
from tests.fixtures import write_table, write_workbook
from tests.pages._dataset import DatasetCase, buttons
from tests.pages._nicegui import ui_client

_TARGETS = {csv_dialog.VIDEO_ID_TARGET: {v: v for v in ("alpha", "bravo")}}


def _checkbox(client, text: str | None = None) -> bool:  # noqa: ANN001
    """Whether the page shows a checkbox (with ``text``, if given)."""
    return any(
        isinstance(el, ui.checkbox) and (text is None or el.text == text)
        for el in client.layout.descendants()
    )


async def _until(predicate, seconds: float = 3.0) -> None:  # noqa: ANN001
    for _ in range(int(seconds / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)


class TestTableDialogs(DatasetCase):
    async def _press(self, client, label: str) -> None:  # noqa: ANN001
        await _until(lambda: bool(buttons(client.layout, label)))
        (button,) = buttons(client.layout, label)
        for listener in list(button._event_listeners.values()):  # noqa: SLF001
            if listener.type == "click":
                button._handle_event({"listener_id": listener.id, "args": None})  # noqa: SLF001

    async def test_a_label_csv_becomes_a_source(self) -> None:
        path = write_table(
            self.root / "extra.csv", ["video_id", "rating"], [{"video_id": "alpha", "rating": "5"}]
        )
        with ui_client() as client:

            async def configure() -> object:
                with client.layout:
                    return await csv_dialog.configure_table(path, _TARGETS, set())

            task = asyncio.create_task(configure())
            await _until(
                lambda: any(isinstance(el, ui.checkbox) for el in client.layout.descendants())
            )
            for box in [el for el in client.layout.descendants() if isinstance(el, ui.checkbox)]:
                if box.text == "rating":
                    box.set_value(True)
            await self._press(client, "Add source")
            sources = await asyncio.wait_for(task, 5)
        assert sources is not None
        (source,) = sources
        self.assertEqual(source.role, CsvRole.LABELS)
        self.assertEqual(source.key_column, "video_id")
        self.assertEqual(source.value_columns, ("rating",))

    async def test_a_workbook_sheet_becomes_a_source(self) -> None:
        path = write_workbook(
            self.root / "book.xlsx",
            {"visits": [["video", "age"], ["alpha", 3], ["bravo", 4]], "notes": [["x"]]},
        )
        with ui_client() as client:

            async def configure() -> object:
                with client.layout:
                    return await csv_dialog.configure_table(path, _TARGETS, set())

            task = asyncio.create_task(configure())
            await _until(
                lambda: any(isinstance(el, ui.expansion) for el in client.layout.descendants())
            )
            visits = next(
                el
                for el in client.layout.descendants()
                if isinstance(el, ui.expansion) and el._props.get("label") == "visits"  # noqa: SLF001
            )
            visits.set_value(True)  # opening a sheet builds its editor
            await _until(
                lambda: any(
                    isinstance(el, ui.checkbox) and el.text == "age"
                    for el in client.layout.descendants()
                )
            )
            for box in [el for el in client.layout.descendants() if isinstance(el, ui.checkbox)]:
                if box.text == "age":
                    box.set_value(True)
            await self._press(client, "Add sheets")
            sources = await asyncio.wait_for(task, 5)
        assert sources is not None
        (source,) = sources
        self.assertEqual(source.sheet, "visits")
        self.assertEqual(source.value_columns, ("age",))

    async def test_cancel_returns_nothing(self) -> None:
        path = write_table(self.root / "x.csv", ["video_id"], [{"video_id": "alpha"}])
        with ui_client() as client:

            async def configure() -> object:
                with client.layout:
                    return await csv_dialog.configure_table(path, _TARGETS, set())

            task = asyncio.create_task(configure())
            await self._press(client, "Cancel")
            self.assertIsNone(await asyncio.wait_for(task, 5))
