"""Tests for preview data-URI encoding (the Browse/Annotator memory hot spot)."""

from __future__ import annotations

import base64
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from annie.core.models import VideoEntry
from annie.media.decode import media_available
from annie.media.preview import (
    HIDPI_SCALE,
    band_frame_indices,
    build_band_strip,
    build_grid_preview,
    to_data_uri,
)

_PREFIX = "data:image/webp;base64,"

#: Distinct gray levels, one per generated frame, so a decoded frame's mean pixel
#: value reveals which index it came from.
_LEVELS = [10, 60, 110, 160, 210]


def _make_clip(directory: Path, levels: list[int]) -> Path:
    """Encode a lossless clip whose frame *i* is a solid ``levels[i]`` gray."""
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


def _decode(uri: str) -> Image.Image:
    """Round-trip a data URI back into a PIL image."""
    payload = uri.removeprefix(_PREFIX)
    return Image.open(io.BytesIO(base64.b64decode(payload)))


def _noisy(width: int, height: int) -> Image.Image:
    """A non-uniform image, so encoded size actually depends on pixel count."""
    image = Image.new("RGB", (width, height))
    image.putdata(
        [
            ((x * 7) % 256, (y * 13) % 256, (x * y) % 256)
            for y in range(height)
            for x in range(width)
        ]
    )
    return image


class TestBandFrameIndices(unittest.TestCase):
    def test_samples_span_inclusive_of_both_ends(self) -> None:
        # 2s..4s at 10 fps → frames 20..40; 5 samples evenly spaced.
        indices = band_frame_indices(2.0, 4.0, fps=10.0, num_frames=100, count=5)
        self.assertEqual(indices, [20, 25, 30, 35, 40])

    def test_clamps_to_available_frames(self) -> None:
        indices = band_frame_indices(0.0, 100.0, fps=10.0, num_frames=30, count=5)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 29)  # never past the last frame
        self.assertTrue(all(0 <= i <= 29 for i in indices))

    def test_degenerate_span_collapses_to_single_frame(self) -> None:
        self.assertEqual(band_frame_indices(3.0, 3.0, fps=10.0, num_frames=100), [30])
        # reversed span is treated as degenerate, not an error
        self.assertEqual(band_frame_indices(4.0, 2.0, fps=10.0, num_frames=100), [40])

    def test_zero_fps_or_no_frames_yields_nothing(self) -> None:
        self.assertEqual(band_frame_indices(1.0, 2.0, fps=0.0, num_frames=100), [])
        self.assertEqual(band_frame_indices(1.0, 2.0, fps=10.0, num_frames=0), [])


class TestToDataUri(unittest.TestCase):
    def test_emits_a_webp_data_uri(self) -> None:
        uri = to_data_uri(_noisy(64, 48))
        self.assertTrue(uri.startswith(_PREFIX), uri[:40])
        self.assertEqual(_decode(uri).format, "WEBP")

    def test_without_a_box_the_full_resolution_is_kept(self) -> None:
        uri = to_data_uri(_noisy(470, 360))
        self.assertEqual(_decode(uri).size, (470, 360))

    def test_a_box_downscales_to_hidpi_multiple(self) -> None:
        uri = to_data_uri(_noisy(470, 360), (240, 135))
        width, height = _decode(uri).size
        self.assertLessEqual(width, 240 * HIDPI_SCALE)
        self.assertLessEqual(height, 135 * HIDPI_SCALE)

    def test_a_box_preserves_aspect_ratio(self) -> None:
        source = _noisy(400, 200)  # 2:1
        width, height = _decode(to_data_uri(source, (100, 100))).size
        self.assertAlmostEqual(width / height, 2.0, places=1)

    def test_an_image_smaller_than_the_box_is_not_upscaled(self) -> None:
        uri = to_data_uri(_noisy(80, 60), (240, 135))
        self.assertEqual(_decode(uri).size, (80, 60))

    def test_boxing_shrinks_the_payload_substantially(self) -> None:
        """The whole point: a Browse strip frame must not ship at full resolution."""
        source = _noisy(470, 360)
        full = len(to_data_uri(source))
        boxed = len(to_data_uri(source, (240, 135)))
        self.assertLess(boxed, full)

    def test_does_not_mutate_the_source_image(self) -> None:
        source = _noisy(470, 360)
        to_data_uri(source, (240, 135))
        self.assertEqual(source.size, (470, 360))


@unittest.skipUnless(
    media_available() and shutil.which("ffmpeg"), "needs the media extra and ffmpeg"
)
class TestBuildGridPreview(unittest.TestCase):
    """The grid view stakes its speed on decoding one *middle* frame per video."""

    def test_returns_the_middle_frame_and_total_count(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        clip = _make_clip(tmp, _LEVELS)  # 5 frames, means 10..210
        # No vdet/track, so no overlay is drawn and the pixels are the raw frame.
        entry = VideoEntry("vid", video_path=clip)

        image, num_frames = build_grid_preview(entry)

        self.assertEqual(num_frames, len(_LEVELS))
        mean = float(np.asarray(image.convert("L")).mean())
        # The middle level (110) — not the first (10) or last (210).
        self.assertAlmostEqual(mean, _LEVELS[len(_LEVELS) // 2], delta=8)

    def test_rejects_a_video_less_entry(self) -> None:
        with self.assertRaises(ValueError):
            build_grid_preview(VideoEntry("no-video"))


@unittest.skipUnless(
    media_available() and shutil.which("ffmpeg"), "needs the media extra and ffmpeg"
)
class TestBuildBandStrip(unittest.TestCase):
    """The Segment-review strip decodes the raw frames of one band's time span."""

    def test_decodes_frames_within_the_requested_span(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        clip = _make_clip(tmp, _LEVELS)  # 5 frames at 5 fps → spans 0.0..1.0s

        # 0.4..0.8s at 5 fps → frames 2..4; 3 samples land on 2, 3, 4 (levels 110/160/210).
        frames = build_band_strip(clip, 0.4, 0.8, count=3)

        self.assertEqual(len(frames), 3)
        means = [float(np.asarray(f.convert("L")).mean()) for f in frames]
        for mean, level in zip(means, _LEVELS[2:5], strict=True):
            self.assertAlmostEqual(mean, level, delta=8)

    def test_full_span_covers_first_and_last_frame(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        clip = _make_clip(tmp, _LEVELS)

        frames = build_band_strip(clip, 0.0, 1.0, count=5)

        self.assertEqual(len(frames), 5)
        first = float(np.asarray(frames[0].convert("L")).mean())
        last = float(np.asarray(frames[-1].convert("L")).mean())
        self.assertAlmostEqual(first, _LEVELS[0], delta=8)
        self.assertAlmostEqual(last, _LEVELS[-1], delta=8)


if __name__ == "__main__":
    unittest.main()
