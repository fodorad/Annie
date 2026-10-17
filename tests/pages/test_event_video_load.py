"""Event annotation reads a video's metadata off the event loop, and drops stale results."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from annie.core.models import VideoEntry
from annie.media.decode import media_available
from annie.pages import event_task


def _clip(directory: Path, frames: int = 6) -> Path:
    """Encode a tiny 5 fps clip with ``frames`` frames."""
    pngs = directory / "frames"
    pngs.mkdir()
    for i in range(frames):
        Image.new("RGB", (32, 24), (20 * i, 0, 0)).save(pngs / f"f{i:03d}.png")
    out = directory / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", "5", "-i", str(pngs / "f%03d.png"),
         "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )  # fmt: skip
    return out


@unittest.skipUnless(media_available() and shutil.which("ffmpeg"), "needs torchcodec + ffmpeg")
class TestLoadMetadata(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.entry = VideoEntry("clip", _clip(Path(tempfile.mkdtemp())))
        self.state = event_task._EventState()  # noqa: SLF001
        self.state.row_key = self.entry.key

    async def test_metadata_is_read_and_applied_once(self) -> None:
        self.assertTrue(event_task.needs_metadata(self.state, self.entry))
        self.assertTrue(await event_task.load_metadata(self.state, self.entry))
        self.assertEqual(self.state.num_frames, 6)
        self.assertAlmostEqual(self.state.fps, 5.0)
        self.assertEqual(self.state.view.total_frames, 6)
        self.assertFalse(event_task.needs_metadata(self.state, self.entry))
        self.assertFalse(await event_task.load_metadata(self.state, self.entry))  # no re-read

    async def test_a_result_for_a_video_left_behind_is_dropped(self) -> None:
        read = asyncio.create_task(event_task.load_metadata(self.state, self.entry))
        await asyncio.sleep(0)
        self.state.row_key = "another video"  # the reviewer stepped on meanwhile
        self.assertFalse(await read)
        self.assertEqual(self.state.num_frames, 0)
        self.assertIsNone(self.state.meta_key)

    async def test_a_second_read_while_one_runs_is_not_started(self) -> None:
        first = asyncio.create_task(event_task.load_metadata(self.state, self.entry))
        await asyncio.sleep(0)
        self.assertFalse(await event_task.load_metadata(self.state, self.entry))
        self.assertTrue(await first)


class TestUnreadableVideo(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(media_available(), "needs torchcodec")
    async def test_a_bad_file_is_marked_read_so_it_is_not_retried(self) -> None:
        bad = Path(tempfile.mkdtemp()) / "bad.mp4"
        bad.write_bytes(b"not a video")
        entry = VideoEntry("bad", bad)
        state = event_task._EventState(row_key=entry.key)  # noqa: SLF001
        self.assertTrue(await event_task.load_metadata(state, entry))
        self.assertEqual(state.num_frames, 0)
        self.assertFalse(event_task.needs_metadata(state, entry))

    def test_entries_without_a_file_need_no_metadata(self) -> None:
        entry = VideoEntry("none", None)
        self.assertFalse(event_task.needs_metadata(event_task._EventState(), entry))  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
