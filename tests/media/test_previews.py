"""Tests for the preview loader: bounded, newest-first, skips unwanted rows, caches results.

The decoders are injected as plain functions (fake adapters at the loader's seam) that
record their calls and can be held on a :class:`threading.Event`, so ordering and
concurrency are observed directly; one test decodes a real clip end to end.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image

from annie.core.models import VideoEntry
from annie.media.decode import media_available
from annie.media.previews import SKIPPED, PreviewLoader, Strip, Tile

_PREFIX = "data:image/webp;base64,"
_BOX = (32, 18)


def _image() -> Image.Image:
    return Image.new("RGB", (64, 36), (120, 30, 200))


class _Decoders:
    """Fake decoders: record which video each call decoded, optionally held on a gate."""

    def __init__(self, gate: threading.Event | None = None) -> None:
        self.gate = gate
        self.calls: list[str] = []
        self.running = 0
        self.peak = 0
        self._lock = threading.Lock()

    def _enter(self, name: str) -> None:
        with self._lock:
            self.calls.append(name)
            self.running += 1
            self.peak = max(self.peak, self.running)
        if self.gate is not None:
            self.gate.wait(5)

    def _leave(self) -> None:
        with self._lock:
            self.running -= 1

    def strip(self, entry: VideoEntry) -> tuple[Image.Image, list[Image.Image], int]:
        self._enter(entry.video_id)
        try:
            if entry.video_id == "bad":
                raise ValueError("cannot decode")
            return _image(), [_image()] * 5, 120
        finally:
            self._leave()

    def tile(self, entry: VideoEntry) -> tuple[Image.Image, int]:
        self._enter(entry.video_id)
        self._leave()
        return _image(), 90

    def band(self, path: Path, start: float, end: float, count: int) -> list[Image.Image]:
        self._enter(f"{path.name}@{start}")
        self._leave()
        return [_image()] * count

    def audio(self, path: Path) -> bool:
        self._enter(f"audio:{path.name}")
        self._leave()
        return True

    def loader(self, workers: int = 2, cache_size: int = 300) -> PreviewLoader:
        return PreviewLoader(
            workers,
            cache_size=cache_size,
            strip_fn=self.strip,
            tile_fn=self.tile,
            band_fn=self.band,
            audio_fn=self.audio,
        )


def _entry(name: str, track: int = 0) -> VideoEntry:
    return VideoEntry(name, Path(f"{name}.mp4"), active_track_id=track)


def _yes() -> bool:
    return True


class TestPreviewLoader(unittest.IsolatedAsyncioTestCase):
    async def test_strip_is_encoded_in_the_worker_with_its_frame_count(self) -> None:
        loader = _Decoders().loader()
        result = await loader.strip(_entry("a"), _BOX, wanted=_yes)
        assert isinstance(result, Strip)
        self.assertEqual(result.num_frames, 120)
        self.assertEqual(len(result.uris), 5)
        self.assertTrue(all(uri.startswith(_PREFIX) for uri in result.uris))

    async def test_tile_and_band_and_audio(self) -> None:
        loader = _Decoders().loader()
        tile = await loader.tile(_entry("a"), _BOX, wanted=_yes)
        assert isinstance(tile, Tile)
        self.assertEqual(tile.num_frames, 90)
        self.assertTrue(tile.uri.startswith(_PREFIX))
        band = await loader.band(Path("v.mp4"), 1.0, 2.0, 4, _BOX, wanted=_yes)
        assert isinstance(band, list)
        self.assertEqual(len(band), 4)
        self.assertIs(await loader.audio(Path("v.mp4"), wanted=_yes), True)

    async def test_never_more_than_max_workers_at_once(self) -> None:
        fakes = _Decoders(gate=threading.Event())
        loader = fakes.loader(workers=2)
        jobs = [
            asyncio.create_task(loader.strip(_entry(f"v{i}"), _BOX, wanted=_yes)) for i in range(6)
        ]
        await asyncio.sleep(0.2)
        self.assertEqual(fakes.running, 2)
        fakes.gate.set()
        await asyncio.gather(*jobs)
        self.assertEqual(fakes.peak, 2)

    async def test_newest_batch_starts_first_and_runs_top_down(self) -> None:
        fakes = _Decoders(gate=threading.Event())
        loader = fakes.loader(workers=1)
        first = asyncio.create_task(loader.strip(_entry("first"), _BOX, wanted=_yes))
        await asyncio.sleep(0.1)  # "first" holds the only worker

        def batch(*names: str) -> list[asyncio.Task]:
            return [asyncio.create_task(loader.strip(_entry(n), _BOX, wanted=_yes)) for n in names]

        older = batch("a1", "a2")  # one page built ...
        await asyncio.sleep(0.2)
        newer = batch("b1", "b2")  # ... then the next page scrolled into view
        await asyncio.sleep(0.05)
        fakes.gate.set()
        await asyncio.gather(first, *older, *newer)
        self.assertEqual(fakes.calls, ["first", "b1", "b2", "a1", "a2"])

    async def test_unwanted_job_is_skipped_without_decoding(self) -> None:
        fakes = _Decoders(gate=threading.Event())
        loader = fakes.loader(workers=1)
        busy = asyncio.create_task(loader.strip(_entry("busy"), _BOX, wanted=_yes))
        await asyncio.sleep(0.1)
        gone = {"alive": True}
        skipped = asyncio.create_task(
            loader.strip(_entry("gone"), _BOX, wanted=lambda: gone["alive"])
        )
        await asyncio.sleep(0.05)
        gone["alive"] = False  # the row was scrolled/refreshed away while queued
        fakes.gate.set()
        await busy
        self.assertIs(await skipped, SKIPPED)
        self.assertNotIn("gone", fakes.calls)

    async def test_a_repeat_request_is_served_from_the_cache(self) -> None:
        fakes = _Decoders()
        loader = fakes.loader()
        first = await loader.strip(_entry("a"), _BOX, wanted=_yes)
        again = await loader.strip(_entry("a"), _BOX, wanted=_yes)
        self.assertEqual(first, again)
        self.assertEqual(fakes.calls, ["a"])
        await loader.strip(_entry("a", track=3), _BOX, wanted=_yes)  # another track: new key
        self.assertEqual(fakes.calls, ["a", "a"])

    async def test_cache_is_bounded_and_clearable(self) -> None:
        fakes = _Decoders()
        loader = fakes.loader(cache_size=2)
        for name in ("a", "b", "c"):
            await loader.strip(_entry(name), _BOX, wanted=_yes)
        await loader.strip(_entry("a"), _BOX, wanted=_yes)  # evicted: decoded again
        self.assertEqual(fakes.calls, ["a", "b", "c", "a"])
        loader.clear()
        await loader.strip(_entry("c"), _BOX, wanted=_yes)
        self.assertEqual(fakes.calls[-1], "c")

    async def test_errors_reach_the_caller_and_are_not_cached(self) -> None:
        fakes = _Decoders()
        loader = fakes.loader()
        for _ in range(2):
            with self.assertRaises(ValueError):
                await loader.strip(_entry("bad"), _BOX, wanted=_yes)
        self.assertEqual(fakes.calls, ["bad", "bad"])


@unittest.skipUnless(media_available() and shutil.which("ffmpeg"), "needs torchcodec + ffmpeg")
class TestRealClip(unittest.IsolatedAsyncioTestCase):
    async def test_a_real_clip_decodes_to_five_encoded_frames(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        frames = tmp / "frames"
        frames.mkdir()
        for i in range(8):
            Image.new("RGB", (64, 48), (30 * i, 30 * i, 30 * i)).save(frames / f"f{i:03d}.png")
        clip = tmp / "clip.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-framerate", "5", "-i", str(frames / "f%03d.png"),
             "-pix_fmt", "yuv420p", str(clip)],
            check=True, capture_output=True,
        )  # fmt: skip
        loader = PreviewLoader(1)
        result = await loader.strip(VideoEntry("clip", clip), _BOX, wanted=_yes)
        assert isinstance(result, Strip)
        self.assertEqual(result.num_frames, 8)
        self.assertEqual(len(result.uris), 5)


if __name__ == "__main__":
    unittest.main()
