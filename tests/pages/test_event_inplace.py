"""Event edits redraw the editor, chips and timeline in place: the video element survives."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from nicegui import core, ui
from PIL import Image

from annie.app_state import state
from annie.core.models import VideoEntry
from annie.dataset.scanning import ScanResult
from annie.dataset.storage import ReviewStore
from annie.media.decode import media_available
from annie.pages import event_task
from tests.pages._nicegui import quiet_slow_callback_warnings, ui_client


def _clip(directory: Path) -> Path:
    pngs = directory / "frames"
    pngs.mkdir()
    for i in range(10):
        Image.new("RGB", (32, 24), (20 * i, 0, 0)).save(pngs / f"f{i:03d}.png")
    out = directory / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", "5", "-i", str(pngs / "f%03d.png"),
         "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )  # fmt: skip
    return out


class TestChangedFields(unittest.TestCase):
    def test_only_differing_values_remain(self) -> None:
        store = ReviewStore(Path(tempfile.mkdtemp()) / "e.db")
        event = store.add_event("v", "v::", "Mother", 10, 20, label="Smile")
        fields = {"start_frame": 10, "end_frame": 25, "label": "Smile", "note": ""}
        self.assertEqual(event_task.changed_fields(event, fields), {"end_frame": 25})
        self.assertEqual(event_task.changed_fields(event, {"start_frame": 10}), {})


@unittest.skipUnless(media_available() and shutil.which("ffmpeg"), "needs torchcodec + ffmpeg")
class TestInPlace(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved = (core.loop, state.store, state.scan)
        core.loop = asyncio.get_running_loop()
        quiet_slow_callback_warnings()
        tmp = Path(tempfile.mkdtemp())
        state.store = ReviewStore(tmp / "annie.db")
        self.entry = VideoEntry("clip", _clip(tmp), row_id=1)
        state.scan = ScanResult(entries=[self.entry])
        state.store.set_annotate(self.entry.key, self.entry.video_id, None, True)
        event_task._event_states.clear()  # noqa: SLF001

    async def asyncTearDown(self) -> None:
        core.loop, state.store, state.scan = self._saved
        event_task._event_states.clear()  # noqa: SLF001
        event_task._gesture_bound.clear()  # noqa: SLF001

    async def _built(self, client) -> event_task._EventState:  # noqa: ANN001
        """Build the work area for the queued clip, metadata loaded."""
        event_task.event_annotation_task()
        st = event_task._event_state()  # noqa: SLF001
        self.assertTrue(await event_task.load_metadata(st, self.entry))
        event_task.event_annotation_task.refresh()
        await asyncio.sleep(0.05)
        return st

    def _video_ids(self, client) -> list[int]:  # noqa: ANN001
        return [el.id for el in client.layout.descendants() if isinstance(el, ui.video)]

    async def test_edits_keep_the_video_element(self) -> None:
        with ui_client() as client:
            st = await self._built(client)
            video = self._video_ids(client)
            self.assertEqual(len(video), 1)

            state.store.add_category("Mother")
            state.store.add_category("Baby")
            event_task._set_active_category(st, "Baby")  # noqa: SLF001 - keys 1-9
            event_task._finalise_at(st, "Baby", 1.0)  # noqa: SLF001 - capture (key o)
            event_task._update(st, note="looks away")  # noqa: SLF001 - an editor field
            event_task._set_color(st, "#ff0000")  # noqa: SLF001
            event_task._delete_event(st)  # noqa: SLF001
            await asyncio.sleep(0.05)
            self.assertEqual(self._video_ids(client), video)

    async def test_a_blur_without_change_writes_nothing(self) -> None:
        with ui_client():
            st = await self._built(None)
            state.store.add_category("Mother")
            event_task._finalise_at(st, "Mother", 1.0)  # noqa: SLF001
            before = state.store.get_event(st.selected_event or "")
            assert before is not None
            event_task._update(st, note=before.note, start_frame=before.start_frame)  # noqa: SLF001
            after = state.store.get_event(st.selected_event or "")
            self.assertEqual(after, before)  # updated_at untouched


if __name__ == "__main__":
    unittest.main()
