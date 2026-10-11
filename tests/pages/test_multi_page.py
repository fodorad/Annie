"""Regression tests for state shared by several open Annie pages.

Each browser tab is its own NiceGUI client, built from scratch. The Annotator tab's
enabled state and the Convert progress panel used to be kept in single module-level
variables, which every newly built page overwrote: with two pages open, a video queued
in the first page enabled the Annotator tab of the second, and the first stayed greyed
out (the Windows tray opens a new page on every "Open Annie").
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nicegui import ui

from annie.core.models import VideoEntry
from annie.core.state import state
from annie.dataset.scanning import ScanResult
from annie.dataset.storage import ReviewStore
from annie.pages import annotator, convert
from tests.pages._nicegui import ui_client


def _disabled(tab: ui.tab) -> bool:
    return bool(tab.props.get("disable"))


class TestAnnotatorTabInEveryPage(unittest.TestCase):
    def setUp(self) -> None:
        saved = (state.store, state.scan, dict(annotator._tabs))
        self.addCleanup(self._restore, saved)
        annotator._tabs.clear()
        # A real file: ReviewStore opens a connection per call.
        self.tmp = Path(tempfile.mkdtemp())
        state.store = ReviewStore(self.tmp / "annie.db")
        self.entry = VideoEntry(video_id="clip", video_path=self.tmp / "clip.mp4")
        state.scan = ScanResult(entries=[self.entry])
        # No queue-free task is ready: only a queued video enables the tab.
        patch = mock.patch.object(annotator, "_ready_tasks", return_value=[])
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def _restore(saved: tuple) -> None:
        state.store, state.scan, tabs = saved
        annotator._tabs.clear()
        annotator._tabs.update(tabs)

    def _page(self, stack: list) -> ui.tab:
        """Open a page (a NiceGUI client) whose header holds an Annotator tab."""
        client_cm = ui_client()
        client = client_cm.__enter__()
        stack.append((client_cm, client))
        with ui.tabs():
            tab = ui.tab("annotator")
        annotator.set_tab(tab)
        annotator.sync_tab()
        return tab

    def _close_all(self, stack: list) -> None:
        for client_cm, _client in reversed(stack):
            client_cm.__exit__(None, None, None)

    def test_queueing_in_one_page_enables_the_tab_in_all_pages(self) -> None:
        stack: list = []
        self.addCleanup(self._close_all, stack)
        first = self._page(stack)
        second = self._page(stack)
        self.assertTrue(_disabled(first))
        self.assertTrue(_disabled(second))

        state.store.set_annotate(self.entry.key, self.entry.video_id, None, True)
        annotator.sync_tab()
        self.assertFalse(_disabled(first), "the page opened first must be enabled too")
        self.assertFalse(_disabled(second))

        state.store.set_annotate(self.entry.key, self.entry.video_id, None, False)
        annotator.sync_tab()
        self.assertTrue(_disabled(first))
        self.assertTrue(_disabled(second))

    def test_a_closed_page_is_forgotten(self) -> None:
        stack: list = []
        self.addCleanup(self._close_all, stack)
        self._page(stack)
        second = self._page(stack)
        _cm, first_client = stack[0]
        first_client.delete()
        stack.pop(0)
        self.assertEqual(list(annotator._tabs), [second.client.id])

        state.store.set_annotate(self.entry.key, self.entry.video_id, None, True)
        annotator.sync_tab()  # must not touch the deleted page
        self.assertFalse(_disabled(second))

    def test_a_dead_tab_is_dropped_on_sync(self) -> None:
        stack: list = []
        self.addCleanup(self._close_all, stack)
        tab = self._page(stack)
        tab.delete()  # e.g. the header was rebuilt, but the client lives on
        annotator.sync_tab()
        self.assertEqual(annotator._tabs, {})


class TestConvertProgressInEveryPage(unittest.TestCase):
    def setUp(self) -> None:
        saved = dict(convert._panels)
        self.addCleanup(lambda: (convert._panels.clear(), convert._panels.update(saved)))
        convert._panels.clear()

    def _page(self, stack: list) -> convert._Panel:
        client_cm = ui_client()
        client = client_cm.__enter__()
        stack.append((client_cm, client))
        convert._build_progress()
        return convert._panels[client.id]

    def test_progress_reaches_every_page(self) -> None:
        stack: list = []
        self.addCleanup(lambda: [cm.__exit__(None, None, None) for cm, _ in reversed(stack)])
        first = self._page(stack)
        second = self._page(stack)
        self.assertEqual(first.title.text, "No conversion running.")

        running = mock.Mock(
            status="running",
            title="Video",
            fraction=0.5,
            done=1,
            total=2,
            succeeded_count=1,
            failed=[],
            current="b.mp4",
            started_at=None,
            elapsed=3.0,
            eta_seconds=None,
        )
        with mock.patch.object(state.converter, "progress", return_value=running):
            convert._render_progress()
        for panel in (first, second):
            self.assertEqual(panel.title.text, "Video — running")
            self.assertEqual(panel.bar.value, 0.5)
            self.assertIn("1/2", panel.counts.text)

    def test_a_closed_page_is_forgotten(self) -> None:
        stack: list = []
        self.addCleanup(lambda: [cm.__exit__(None, None, None) for cm, _ in reversed(stack)])
        self._page(stack)
        second = self._page(stack)
        _cm, first_client = stack[0]
        first_client.delete()
        stack.pop(0)
        convert._render_progress()
        self.assertEqual(list(convert._panels.values()), [second])


if __name__ == "__main__":
    unittest.main()
