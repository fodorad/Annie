"""Tests for the Segment-review on-demand clip cut."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from annie.core.config import settings
from annie.media import clipping
from annie.media.clipping import _clip_name, cut_clip
from annie.media.decode import media_available
from annie.media.rendering import RenderService

#: Distinct gray levels, one per generated frame (mirrors the preview test helper).
_LEVELS = [10, 60, 110, 160, 210]


def _make_clip(directory: Path, levels: list[int]) -> Path:
    """Encode a lossless clip whose frame *i* is a solid ``levels[i]`` gray at 5 fps."""
    frames = directory / "frames"
    frames.mkdir()
    for i, level in enumerate(levels):
        Image.new("RGB", (64, 48), (level, level, level)).save(frames / f"f{i:03d}.png")
    out = directory / "vid.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", "5", "-i", str(frames / "f%03d.png"),
         "-c:v", "libx264rgb", "-qp", "0", "-pix_fmt", "rgb24", str(out)],
        check=True, capture_output=True,
    )  # fmt: skip
    return out


class TestClipName(unittest.TestCase):
    """The output filename is deterministic per (video, span) and span-sensitive."""

    def test_same_span_same_name(self) -> None:
        path = Path("/videos/movie.mp4")
        self.assertEqual(_clip_name(path, 0.4, 0.8), _clip_name(path, 0.4, 0.8))

    def test_different_span_different_name(self) -> None:
        path = Path("/videos/movie.mp4")
        self.assertNotEqual(_clip_name(path, 0.4, 0.8), _clip_name(path, 0.4, 0.9))

    def test_keeps_the_stem_for_readability(self) -> None:
        self.assertTrue(_clip_name(Path("/videos/movie.mp4"), 0.0, 1.0).startswith("movie_"))


class TestCutClipValidation(unittest.TestCase):
    """A reversed or empty span is rejected before ffmpeg is ever invoked."""

    def test_rejects_non_positive_span(self) -> None:
        with self.assertRaises(ValueError):
            cut_clip("/videos/movie.mp4", 1.0, 1.0)
        with self.assertRaises(ValueError):
            cut_clip("/videos/movie.mp4", 1.0, 0.5)


@unittest.skipUnless(
    media_available() and shutil.which("ffmpeg"), "needs the media extra and ffmpeg"
)
class TestCutClip(unittest.TestCase):
    """Cutting a span produces a playable file under the temp dir, cached on repeat."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._saved_temp = settings.temp_dir
        settings.temp_dir = self.tmp / "cuts"
        clipping._clip_cache.clear()

    def tearDown(self) -> None:
        settings.temp_dir = self._saved_temp
        clipping._clip_cache.clear()

    def test_cuts_a_span_to_a_file(self) -> None:
        clip = _make_clip(self.tmp, _LEVELS)  # 5 frames at 5 fps → spans 0.0..1.0s

        out = cut_clip(clip, 0.2, 0.8)

        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 0)
        self.assertEqual(out.parent, clipping.clips_dir())

    def test_reuses_the_cached_cut(self) -> None:
        clip = _make_clip(self.tmp, _LEVELS)

        first = cut_clip(clip, 0.2, 0.8)
        mtime = first.stat().st_mtime_ns
        second = cut_clip(clip, 0.2, 0.8)

        self.assertEqual(first, second)
        self.assertEqual(second.stat().st_mtime_ns, mtime)  # not re-encoded

    def test_the_render_sweeper_does_not_delete_a_cut(self) -> None:
        """A clip older than the TTL survives a sweep; a stale render beside it does not.

        The render sweeper walks ``temp_dir`` for expired ``.mp4`` files and protects only
        in-flight jobs. A band cut is neither a job nor touched while it plays, so before
        it moved into ``clips/`` a reviewer studying one card past the TTL (three minutes
        by default) had the file unlinked out from under the embedded player.
        """
        clip = _make_clip(self.tmp, _LEVELS)
        cut = cut_clip(clip, 0.2, 0.8)
        stale_render = settings.temp_dir / "stale_render.mp4"
        stale_render.parent.mkdir(parents=True, exist_ok=True)
        stale_render.write_bytes(b"x")

        expired = time.time() - 10_000
        for path in (cut, stale_render):
            os.utime(path, (expired, expired))
        service = RenderService(temp_dir=settings.temp_dir)

        self.assertEqual(service.sweep(), 1)  # only the render was old enough to matter
        self.assertFalse(stale_render.exists())
        self.assertTrue(cut.exists())

    def test_clear_all_does_not_delete_a_cut(self) -> None:
        """Clearing rendered clips is scoped to renders — it must not orphan a live cut."""
        clip = _make_clip(self.tmp, _LEVELS)
        cut = cut_clip(clip, 0.2, 0.8)

        RenderService(temp_dir=settings.temp_dir).clear_all()

        self.assertTrue(cut.exists())


if __name__ == "__main__":
    unittest.main()
