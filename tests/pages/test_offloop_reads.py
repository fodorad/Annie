"""Slow reads run off the event loop behind a visible busy state, and return the same data."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from nicegui import core, ui

from annie.app_state import state
from annie.pages import csv_dialog
from annie.pages import settings as settings_page
from tests.fixtures import write_table, write_workbook
from tests.pages._nicegui import quiet_slow_callback_warnings, ui_client


class TestReading(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved_loop = core.loop
        core.loop = asyncio.get_running_loop()
        quiet_slow_callback_warnings()
        self.tmp = Path(tempfile.mkdtemp())

    async def asyncTearDown(self) -> None:
        core.loop = self._saved_loop

    async def test_a_spinner_dialog_shows_while_reading_and_goes_away_after(self) -> None:
        with ui_client() as client:
            seen: list[bool] = []

            def read() -> int:
                return 42

            async def from_a_click() -> int | None:
                with client.layout:  # a click handler runs inside the page's slot
                    return await csv_dialog.reading(self.tmp / "big.csv", read)

            task = asyncio.create_task(from_a_click())
            await asyncio.sleep(0)
            seen.append(any(el.tag == "q-spinner" for el in client.layout.descendants()))
            self.assertEqual(await task, 42)
            self.assertEqual(seen, [True])
            self.assertFalse(any(el.tag == "q-spinner" for el in client.layout.descendants()))

    async def test_csv_read_returns_header_rows_and_suggested_key(self) -> None:
        path = write_table(self.tmp / "t.csv", ["id", "score"], [{"id": "v1", "score": "3"}])
        header, rows, key = csv_dialog._read_csv(path, {"v1"})  # noqa: SLF001
        self.assertEqual(header, ["id", "score"])
        self.assertEqual(rows, [{"id": "v1", "score": "3"}])
        self.assertEqual(key, "id")
        empty = self.tmp / "empty.csv"
        empty.write_text("", encoding="utf-8")
        self.assertEqual(csv_dialog._read_csv(empty, set()), ([], [], ""))  # noqa: SLF001

    async def test_workbook_read_previews_every_sheet_with_its_join(self) -> None:
        path = write_workbook(
            self.tmp / "w.xlsx",
            {"visits": [["subject", "age"], ["4021", "3"]], "notes": [["x"]]},
        )
        previews = csv_dialog._read_workbook(path, {"subject": {"4021"}})  # noqa: SLF001
        self.assertEqual([p.name for p in previews], ["visits", "notes"])
        visits = previews[0]
        self.assertEqual(visits.header, ["subject", "age"])
        assert visits.hint is not None
        self.assertEqual((visits.hint.column, visits.hint.target), ("subject", "subject"))


class TestMaintenanceSpace(unittest.TestCase):
    def test_space_is_measured_from_the_folders(self) -> None:
        space = settings_page._measure_space()  # noqa: SLF001
        self.assertGreaterEqual(space.render_size, 0)
        self.assertEqual(space.log_size, sum(f.stat().st_size for f in space.old_logs))
        self.assertTrue(all(f != state.store.db_path for f in space.old_dbs))

    def test_card_spins_until_measured(self) -> None:
        with ui_client() as client:
            settings_page._maintenance_section()  # noqa: SLF001
            tags = [el.tag for el in client.layout.descendants()]
        self.assertIn("q-spinner", tags)
        self.assertFalse(any(isinstance(el, ui.button) for el in client.layout.descendants()))


if __name__ == "__main__":
    unittest.main()
