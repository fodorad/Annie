"""A small real dataset on disk, loaded into the app state, for page-level tests."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from nicegui import core, ui

from annie.app_state import state
from annie.core.config import settings
from annie.dataset.sources import CsvRole, DataSource, SourceKind, SourceRegistry
from annie.dataset.storage import ReviewStore
from tests.fixtures import write_participants, write_table, write_track, write_vdet, write_video
from tests.pages._nicegui import quiet_slow_callback_warnings

VIDEOS = ("alpha", "bravo", "charlie", "delta")
"""The video ids of the test dataset."""


def fire(element: object, event: str, args: object = None) -> None:
    """Dispatch a browser event to ``element`` the way NiceGUI does (its listeners run)."""
    for listener_id, listener in list(element._event_listeners.items()):  # type: ignore[attr-defined]  # noqa: SLF001
        if listener.type == event:
            element._handle_event({"listener_id": listener_id, "args": args})  # type: ignore[attr-defined]  # noqa: SLF001


def texts(root: ui.element) -> list[str]:
    """Every text shown under ``root`` (labels, badges, buttons)."""
    return [str(getattr(el, "text", "")) for el in root.descendants() if getattr(el, "text", "")]


def buttons(root: ui.element, text: str) -> list[ui.button]:
    """The buttons under ``root`` whose text is ``text``."""
    return [el for el in root.descendants() if isinstance(el, ui.button) and el.text == text]


class DatasetCase(unittest.IsolatedAsyncioTestCase):
    """Videos, vdet, tracks, a protagonist file and a label table, scanned into the app."""

    async def asyncSetUp(self) -> None:
        self._saved = (
            core.loop,
            state.registry,
            state.scan,
            state.store,
            state.active_config,
            settings.sessions_dir,
        )
        core.loop = asyncio.get_running_loop()
        quiet_slow_callback_warnings()
        self.root = Path(tempfile.mkdtemp())
        settings.sessions_dir = self.root / "sessions"
        videos, vdet, track = self.root / "video", self.root / "vdet", self.root / "track"
        for name in VIDEOS:
            write_video(videos, name)
            write_vdet(vdet, name)
            write_track(track, name, 0)
        labels = write_table(
            self.root / "label.csv",
            ["video_id", "mood", "score"],
            [
                {"video_id": n, "mood": m, "score": s}
                for n, m, s in zip(VIDEOS, ("calm", "calm", "tense", "tense"), "1234", strict=True)
            ],
        )
        protagonist = write_participants(self.root / "protagonist.csv", dict.fromkeys(VIDEOS, 0))
        registry = SourceRegistry()
        registry.add(DataSource(SourceKind.VIDEO, videos))
        registry.add(DataSource(SourceKind.VDET, vdet))
        registry.add(DataSource(SourceKind.TRACK, track))
        registry.add(
            DataSource(
                SourceKind.CSV,
                protagonist,
                role=CsvRole.PROTAGONIST,
                key_column="uuid",
                value_columns=("track_id",),
            )
        )
        registry.add(
            DataSource(
                SourceKind.CSV,
                labels,
                key_column="video_id",
                value_columns=("mood", "score"),
                column_types={"mood": "str", "score": "int"},
            )
        )
        state.registry = registry
        state.store = ReviewStore(self.root / "annie.db")
        state.active_config = None
        state.rescan()

    async def asyncTearDown(self) -> None:
        (
            core.loop,
            state.registry,
            state.scan,
            state.store,
            state.active_config,
            settings.sessions_dir,
        ) = self._saved

    async def settle(self, seconds: float = 0.1) -> None:
        """Let scheduled refreshes and background tasks run."""
        await asyncio.sleep(seconds)
