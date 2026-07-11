"""Build Browse preview images for a video (service; needs the ``media`` extra).

Produces the static thumbnail (clean first frame) and the annotated five-frame
strip (vdet boxes in blue, the protagonist track in green) shown on each Browse
row. Decoding is lazy via :mod:`annie.decode`, so importing this module never
requires torch; call :func:`annie.decode.media_available` before using it.
"""

from __future__ import annotations

import base64
import io
from typing import TYPE_CHECKING

from PIL import Image

from annie.media.compose import load_entry_annotations, merge_frame, strip_track_ids

if TYPE_CHECKING:  # pragma: no cover - typing only
    from annie.core.models import VideoEntry


#: Oversampling factor applied to a box's CSS size, so previews stay crisp on
#: HiDPI/Retina displays without paying for the full decoded frame.
HIDPI_SCALE = 2

#: WebP quality for preview frames. High enough that the thin overlay boxes stay
#: clean; low enough that a strip frame costs ~12 KB instead of ~180 KB as PNG.
_WEBP_QUALITY = 80


def to_data_uri(image: Image.Image, box: tuple[int, int] | None = None) -> str:
    """Encode a PIL image as a self-contained ``data:`` WebP URI.

    Embedding the pixels in the element (rather than serving a per-client temp
    file) means a thumbnail/strip frame never produces an orphaned static route
    that 404s after a reconnect. The flip side is that every embedded frame is
    held in memory twice — server-side in the element's props, and again in the
    browser tab — for as long as its row is on the page. So the image is first
    downscaled to the box it will actually be displayed in (times
    :data:`HIDPI_SCALE`) and encoded as lossy WebP rather than lossless PNG,
    which cuts a Browse row's payload by roughly 15x.

    Args:
        image: The image to encode.
        box: The ``(width, height)`` CSS size of the element that will show the
            image. The image is shrunk to fit ``HIDPI_SCALE`` times this,
            preserving aspect ratio. ``None`` encodes at full resolution.

    Returns:
        A ``data:image/webp;base64,…`` string usable as a ``ui.image`` source.
    """
    if box is not None:
        width, height = box
        image = image.copy()
        image.thumbnail((width * HIDPI_SCALE, height * HIDPI_SCALE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=_WEBP_QUALITY)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/webp;base64,{encoded}"


def build_preview(entry: VideoEntry, count: int = 5) -> tuple[Image.Image, list[Image.Image], int]:
    """Decode a video's strip and draw its annotations.

    Args:
        entry: The video to preview; ``entry.video_path`` must be set.
        count: Number of strip frames (first / ¼ / ½ / ¾ / last by default).

    Returns:
        A ``(thumbnail, strip, num_frames)`` triple: the clean first frame as a
        thumbnail, a list of annotated strip frames (vdet blue, active track green),
        and the video's total frame count.

    Raises:
        ValueError: If the entry has no video to decode.
        annie.decode.MediaUnavailableError: If the ``media`` extra is absent.
    """
    if entry.video_path is None:
        raise ValueError("cannot build a preview for a video-less entry")

    from annie.media import decode  # local import: optional media dependency
    from annie.media.color import draw_overlay

    indices, frames, num_frames = decode.read_strip(entry.video_path, count)
    vdet_by_frame, tracks_by_id = load_entry_annotations(entry)
    include = strip_track_ids(entry)

    thumbnail = Image.fromarray(frames[0]).convert("RGB")
    strip = [
        draw_overlay(
            frame,
            merge_frame(idx, vdet_by_frame, tracks_by_id, include),
            has_tracks=True,
            active_track_id=entry.active_track_id,
        )
        for idx, frame in zip(indices, frames, strict=True)
    ]
    return thumbnail, strip, num_frames


def build_grid_preview(entry: VideoEntry) -> tuple[Image.Image, int]:
    """Decode a video's middle (½) frame with its annotations drawn on it.

    The Browse grid view shows a single static frame per video, so this is the fast
    counterpart to :func:`build_preview`: it decodes only three sample frames in one
    pass and keeps the middle one. ``"approximate"`` seek is safe here because the
    middle index — unlike the strip's last frame — never risks the tail overshoot
    that :func:`annie.media.decode.read_strip` guards against with ``"exact"`` mode.

    Args:
        entry: The video to preview; ``entry.video_path`` must be set.

    Returns:
        An ``(image, num_frames)`` pair: the annotated middle frame (vdet blue,
        active track green) and the video's total frame count.

    Raises:
        ValueError: If the entry has no video to decode.
        annie.media.decode.MediaUnavailableError: If the ``media`` extra is absent.
    """
    if entry.video_path is None:
        raise ValueError("cannot build a preview for a video-less entry")

    from annie.media import decode  # local import: optional media dependency
    from annie.media.color import draw_overlay

    indices, frames, num_frames = decode.read_strip(entry.video_path, 3, seek_mode="approximate")
    if not frames:
        raise ValueError(f"no frames decoded for {entry.video_path}")
    vdet_by_frame, tracks_by_id = load_entry_annotations(entry)
    include = strip_track_ids(entry)

    mid = len(frames) // 2  # of [first, ½, last] the middle sample is the ½ frame
    image = draw_overlay(
        frames[mid],
        merge_frame(indices[mid], vdet_by_frame, tracks_by_id, include),
        has_tracks=True,
        active_track_id=entry.active_track_id,
    )
    return image, num_frames
