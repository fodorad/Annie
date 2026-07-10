"""Tests for preview data-URI encoding (the Browse/Annotator memory hot spot)."""

from __future__ import annotations

import base64
import io
import unittest

from PIL import Image

from annie.media.preview import HIDPI_SCALE, to_data_uri

_PREFIX = "data:image/webp;base64,"


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


if __name__ == "__main__":
    unittest.main()
