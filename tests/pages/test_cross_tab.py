"""Data-wide changes reach every open browser tab; per-tab work stays in its own tab."""

from __future__ import annotations

import asyncio

from nicegui import ui

from annie.app_state import state
from annie.pages import annotator, browse, dataset, event_task, pending, visibility
from annie.pages.lazy import schedule
from annie.pages.refresh import client_refreshable
from tests.pages._dataset import DatasetCase
from tests.pages._nicegui import ui_client

_builds: list[str] = []


@client_refreshable
def _view(name: str) -> None:
    _builds.append(name)
    ui.label(name)


def _ids(client) -> set[int]:  # noqa: ANN001
    return {el.id for el in client.layout.descendants()}


class TestRefreshScope(DatasetCase):
    async def asyncTearDown(self) -> None:
        _view.targets.clear()
        await super().asyncTearDown()

    async def test_refresh_others_skips_the_acting_tab(self) -> None:
        with ui_client() as a:
            _view("a")
        with ui_client():
            _view("b")
        _builds.clear()
        with a:
            await _view.refresh_others()
        self.assertEqual(_builds, ["b"])

    async def test_scheduled_work_refreshes_only_the_tab_that_scheduled_it(self) -> None:
        with ui_client():
            _view("a")
            host = ui.element("div")
        with ui_client():
            _view("b")
        _builds.clear()

        async def work() -> None:
            await _view.refresh()

        schedule(host, work)
        await self.settle()
        self.assertEqual(_builds, ["a"])


class TestDataWideChanges(DatasetCase):
    async def test_a_db_switch_rebuilds_browse_in_every_tab(self) -> None:
        with ui_client() as a:
            browse.render()
        with ui_client() as b:
            browse.render()
        await self.settle()
        before = _ids(b)
        with a:
            dataset._switch_review_db(self.root / "other.db")  # noqa: SLF001
        await self.settle()
        self.assertNotEqual(_ids(b), before)
        self.assertEqual(state.store.db_path, self.root / "other.db")

    async def test_a_queue_change_rebuilds_the_annotator_in_other_tabs(self) -> None:
        with ui_client() as a:
            annotator.render()
        with ui_client() as b:
            annotator.render()
        await self.settle()
        before_a, before_b = _ids(a), _ids(b)
        entry = state.scan.entries[0]  # type: ignore[union-attr]
        with a:
            state.store.set_annotate(entry.key, entry.video_id, None, True)
            annotator.update_availability()
        await self.settle()
        self.assertEqual(_ids(a), before_a)  # the acting tab rebuilds when it opens it
        self.assertNotEqual(_ids(b), before_b)


class TestEventCategoriesAcrossTabs(DatasetCase):
    async def test_a_deleted_participant_is_dropped_from_another_tabs_bar(self) -> None:
        state.store.add_category("Mother")
        state.store.add_category("Baby")
        with ui_client():
            st = event_task._event_state()  # noqa: SLF001
            st.active_category = "Baby"
            event_task._category_bar(st)  # noqa: SLF001
            state.store.delete_category("Baby")  # e.g. from another tab's manager dialog
            event_task._category_bar.refresh_everywhere()  # noqa: SLF001
            await self.settle()
            self.assertEqual(st.active_category, "Mother")

    async def test_resizing_the_video_restores_its_position(self) -> None:
        st = event_task._EventState(playhead_frame=42)  # noqa: SLF001
        with ui_client():
            event_task._resize_video(st, 1)  # noqa: SLF001
        self.assertEqual(st.restore_frame, 42)


class TestHousekeeping(DatasetCase):
    async def test_rebuilt_members_do_not_pile_up(self) -> None:
        with ui_client() as client:
            visibility.show("annotator")
            first = visibility.only_while_shown(ui.keyboard(on_key=lambda _e: None), "annotator")
            first.delete()
            visibility.only_while_shown(ui.keyboard(on_key=lambda _e: None), "annotator")
            self.assertEqual(len(visibility._members[client.id]), 1)  # noqa: SLF001

    async def test_busy_dialog_shows_while_the_work_runs(self) -> None:
        with ui_client() as client:
            seen: list[bool] = []

            def work() -> str:
                return "done"

            async def run_it() -> str | None:
                with client.layout:
                    return await pending.while_busy("Saving…", work)

            task = asyncio.create_task(run_it())
            await asyncio.sleep(0)
            seen.append(any(el.tag == "q-spinner" for el in client.layout.descendants()))
            self.assertEqual(await task, "done")
        self.assertEqual(seen, [True])
