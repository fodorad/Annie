"""Every tab builds over a real dataset and its main controls work (headless browser tabs)."""

from __future__ import annotations

from nicegui import ui

from annie import app
from annie.app_state import state
from annie.pages import browse, convert, dataset, home
from annie.pages import settings as settings_page
from tests.pages._dataset import VIDEOS, DatasetCase, buttons, fire, texts
from tests.pages._nicegui import ui_client


class TestAppShell(DatasetCase):
    async def test_the_page_builds_home_first_and_other_tabs_on_open(self) -> None:
        with ui_client() as client:
            app.build()
            tabs = next(el for el in client.layout.descendants() if isinstance(el, ui.tabs))
            names = [
                el._props.get("name")
                for el in client.layout.descendants()
                if isinstance(el, ui.tab)
            ]  # noqa: SLF001
            self.assertEqual(
                names, ["home", "convert", "dataset", "browse", "annotator", "logs", "settings"]
            )
            self.assertNotIn("Sources", texts(client.layout))  # Dataset not built yet
            for name in ("dataset", "browse", "annotator", "logs", "settings", "convert"):
                tabs.set_value(name)
                await self.settle()
            shown = texts(client.layout)
            self.assertIn("Sources", shown)
            self.assertIn("Maintenance", shown)
            tabs.set_value("browse")  # opened again: rebuilt, not built twice
            await self.settle()


class TestHome(DatasetCase):
    async def test_home_cards_navigate(self) -> None:
        visited: list[str] = []
        with ui_client() as client:
            home.render(visited.append)
            for card in [el for el in client.layout.descendants() if isinstance(el, ui.card)]:
                fire(card, "click")
        self.assertIn("browse", visited)


class TestBrowse(DatasetCase):
    async def test_rows_tags_filters_and_views(self) -> None:
        with ui_client() as client:
            browse.render()
            await self.settle()
            shown = texts(client.layout)
            for name in VIDEOS:
                self.assertIn(name, shown)
            self.assertIn("mood: calm", shown)

            browse._set("name_prefix", "al")  # noqa: SLF001 - the "name starts with" box
            browse._rows.refresh()  # noqa: SLF001
            await self.settle()
            self.assertEqual([e.video_id for e in browse._filtered_entries()], ["alpha"])  # noqa: SLF001

            browse._clear()  # noqa: SLF001 - Clear filters
            await self.settle()
            self.assertEqual(len(browse._filtered_entries()), len(VIDEOS))  # noqa: SLF001

            browse._state().view_mode = "grid"  # noqa: SLF001
            browse._rows.refresh()  # noqa: SLF001
            await self.settle()
            browse._add_all_to_annotator()  # noqa: SLF001
        self.assertEqual(len(state.store.annotator_keys()), len(VIDEOS))


class TestDatasetTab(DatasetCase):
    async def test_sources_metrics_tasks_and_rescan(self) -> None:
        with ui_client() as client:
            dataset.render()
            await self.settle()
            shown = texts(client.layout)
            self.assertIn("Sources", shown)
            self.assertIn(str(len(VIDEOS)), shown)  # the # videos card
            self.assertIn("Tasks", shown)
            await dataset._rescan()  # noqa: SLF001
            await self.settle()
            source = state.registry.vdet
            assert source is not None
            await dataset._remove(source)  # noqa: SLF001
            await self.settle()
        self.assertIsNone(state.registry.vdet)


class TestSettingsTab(DatasetCase):
    async def test_preferences_apply_and_maintenance_measures(self) -> None:
        with ui_client() as client:
            settings_page.render()
            await self.settle(0.3)
            shown = texts(client.layout)
            self.assertIn("Maintenance", shown)
            numbers = [el for el in client.layout.descendants() if isinstance(el, ui.number)]
            for box in numbers:
                fire(box, "blur")
            await self.settle()


class TestConvertTab(DatasetCase):
    async def test_sections_and_progress_build(self) -> None:
        with ui_client() as client:
            convert.render()
            await self.settle()
            shown = texts(client.layout)
            self.assertIn("Audio", shown)
            self.assertIn("Video", shown)
            self.assertIn("No conversion running.", shown)
            self.assertTrue(buttons(client.layout, "Convert audio"))
