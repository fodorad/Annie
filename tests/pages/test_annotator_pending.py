"""Annotator strips and band rows show spinners while they load, then frames or errors."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nicegui import core, ui
from PIL import Image

from annie.app_state import state
from annie.core.models import VideoEntry
from annie.media.previews import PreviewLoader
from annie.pages import annotator, pending
from tests.pages._nicegui import quiet_slow_callback_warnings, ui_client


def _images(box: ui.element) -> int:
    return sum(1 for el in box.descendants() if isinstance(el, ui.image))


def _frame() -> Image.Image:
    return Image.new("RGB", (32, 18), (10, 200, 10))


def _fake_loader() -> PreviewLoader:
    """A real loader whose decoders return solid frames (no video needed)."""
    return PreviewLoader(
        1,
        strip_fn=lambda entry: (_frame(), [_frame()] * 5, 50),
        band_fn=lambda path, start, end, count: [_frame()] * count,
    )


def _icons(box: ui.element) -> int:
    return sum(1 for el in box.descendants() if el.tag == "q-icon")


class TestAnnotatorPending(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved_loop = core.loop
        core.loop = asyncio.get_running_loop()
        quiet_slow_callback_warnings()
        self.missing = Path(tempfile.mkdtemp()) / "missing.mp4"

        self._saved_previews = state.previews

    async def asyncTearDown(self) -> None:
        core.loop = self._saved_loop
        state.previews = self._saved_previews

    async def test_strip_frames_arrive_through_the_preview_loader(self) -> None:
        state.previews = _fake_loader()
        with ui_client():
            strip = [pending.media_box(80, 45) for _ in range(5)]
            for slot in strip:
                pending.pending(slot)
            await annotator._decode_strip(VideoEntry("v", self.missing), strip, 1.0)  # noqa: SLF001
            self.assertEqual([_images(slot) for slot in strip], [1] * 5)
            self.assertFalse(any(pending.is_pending(slot) for slot in strip))

    async def test_band_frames_arrive_through_the_preview_loader(self) -> None:
        state.previews = _fake_loader()
        with ui_client() as client:
            annotator._band_strip(self.missing, SimpleNamespace(start=0.0, end=1.0))  # noqa: SLF001
            for _ in range(100):
                if _images(client.layout) >= annotator._BAND_STRIP_COUNT:  # noqa: SLF001
                    break
                await asyncio.sleep(0.02)
            imgs = _images(client.layout)
            self.assertEqual(imgs, annotator._BAND_STRIP_COUNT)  # noqa: SLF001

    async def test_track_change_dims_old_frames_under_spinners(self) -> None:
        with ui_client():
            strip = [pending.media_box(80, 45) for _ in range(5)]
            for slot in strip:
                with slot:
                    ui.label("old frame")
            entry = VideoEntry("v", self.missing)
            task = asyncio.create_task(
                annotator._populate_strip(entry, strip, keep=True)  # noqa: SLF001
            )
            await asyncio.sleep(0.01)  # spinners go up at once, before the tab even connects
            self.assertTrue(all(pending.is_pending(slot) for slot in strip))
            self.assertTrue(
                all(any(isinstance(el, ui.label) for el in s.descendants()) for s in strip)
            )
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_a_failed_decode_shows_the_error_in_every_slot(self) -> None:
        with ui_client():
            strip = [pending.media_box(80, 45) for _ in range(5)]
            for slot in strip:
                pending.pending(slot)
            entry = VideoEntry("v", self.missing)
            await asyncio.wait_for(annotator._decode_strip(entry, strip, 1.0), 10)  # noqa: SLF001
            self.assertFalse(any(pending.is_pending(slot) for slot in strip))
            self.assertEqual([_icons(slot) for slot in strip], [1] * 5)

    async def test_band_row_starts_with_spinner_boxes_then_shows_failure(self) -> None:
        with ui_client() as client:
            band = SimpleNamespace(start=0.0, end=1.0)
            annotator._band_strip(self.missing, band)  # noqa: SLF001
            boxes = [
                el
                for el in client.layout.descendants()
                if el.tag == "div" and pending.is_pending(el)
            ]
            self.assertEqual(len(boxes), annotator._BAND_STRIP_COUNT)  # noqa: SLF001
            for _ in range(200):  # wait for the off-loop decode to fail
                if not any(pending.is_pending(b) for b in boxes):
                    break
                await asyncio.sleep(0.05)
            self.assertEqual([_icons(b) for b in boxes], [1] * len(boxes))


if __name__ == "__main__":
    unittest.main()
