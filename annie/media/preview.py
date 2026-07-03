"""Build Browse preview images for a video (service; needs the ``media`` extra).

Produces the static thumbnail (clean first frame) and the annotated five-frame
strip (vdet boxes in blue, the main-character track in green) shown on each Browse
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


def to_data_uri(image: Image.Image) -> str:
    """Encode a PIL image as a self-contained ``data:`` PNG URI.

    Embedding the pixels in the element (rather than serving a per-client temp
    file) means a thumbnail/strip frame never produces an orphaned static route
    that 404s after a reconnect.

    Args:
        image: The image to encode.

    Returns:
        A ``data:image/png;base64,…`` string usable as a ``ui.image`` source.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


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
