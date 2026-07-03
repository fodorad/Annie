"""Frame-reading wrapper over torchcodec (infrastructure).

torchcodec and the rest of the PyTorch ecosystem are an **optional** dependency
(the ``media`` extra). Everything here imports them lazily, so importing
:mod:`annie` — and running the bulk of the test suite — never requires a 2 GB
torch install. Call :func:`media_available` to check, and the loaders raise a
clear :class:`MediaUnavailableError` if torchcodec is missing.

Two seek modes are exposed (per the design):

* ``approximate`` — fast preview scrubbing (Browse thumbnails / strips).
* ``exact`` — frame-accurate reads for the Annotator (guarantees frame ``i`` is
  frame ``i``).

Frames are returned as ``numpy`` ``uint8`` arrays in HWC/RGB order, which is what
the Pillow-based overlay renderer in :mod:`annie.color` consumes.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

SeekMode = Literal["exact", "approximate"]
"""Frame-seek strategy: ``"exact"`` for frame accuracy, ``"approximate"`` for speed."""


class MediaUnavailableError(RuntimeError):
    """Raised when a frame-decode operation needs torchcodec but it is absent."""


def media_available() -> bool:
    """Return whether the optional torchcodec backend can be imported.

    Returns:
        ``True`` if ``torchcodec`` is importable, ``False`` otherwise.
    """
    return importlib.util.find_spec("torchcodec") is not None


def _require_media() -> None:
    """Raise :class:`MediaUnavailableError` if torchcodec is not installed."""
    if not media_available():
        raise MediaUnavailableError(
            "Frame decoding requires the 'media' extra. Install it with "
            '`uv pip install -e ".[media]"` and a system FFmpeg (4-8).'
        )


def _decoder(path: str | Path, seek_mode: SeekMode):  # noqa: ANN202 - external type
    """Construct a torchcodec ``VideoDecoder`` for ``path`` in the given seek mode."""
    _require_media()
    from torchcodec.decoders import VideoDecoder  # local import: optional dependency

    return VideoDecoder(str(Path(path)), seek_mode=seek_mode)


def _to_hwc_uint8(frame) -> np.ndarray:  # noqa: ANN001 - external tensor type
    """Convert a torchcodec CHW uint8 tensor to an HWC uint8 numpy array."""
    return frame.permute(1, 2, 0).contiguous().cpu().numpy()


def read_frame(path: str | Path, frame_idx: int, *, seek_mode: SeekMode = "exact") -> np.ndarray:
    """Read a single frame by index.

    Args:
        path: Path to the video file.
        frame_idx: Zero-based frame index to read.
        seek_mode: ``"exact"`` (frame-accurate) or ``"approximate"`` (fast).

    Returns:
        The frame as an HWC ``uint8`` RGB numpy array.

    Raises:
        MediaUnavailableError: If torchcodec is not installed.
    """
    decoder = _decoder(path, seek_mode)
    return _to_hwc_uint8(decoder[frame_idx])


def read_frames(
    path: str | Path, indices: list[int], *, seek_mode: SeekMode = "approximate"
) -> list[np.ndarray]:
    """Read several frames by index.

    Args:
        path: Path to the video file.
        indices: Zero-based frame indices to read.
        seek_mode: Seek mode; defaults to ``"approximate"`` for fast previews.

    Returns:
        One HWC ``uint8`` RGB numpy array per requested index, in order.

    Raises:
        MediaUnavailableError: If torchcodec is not installed.
    """
    decoder = _decoder(path, seek_mode)
    return [_to_hwc_uint8(decoder[i]) for i in indices]


def frame_count(path: str | Path) -> int:
    """Return the number of frames in a video.

    Args:
        path: Path to the video file.

    Returns:
        The total frame count.

    Raises:
        MediaUnavailableError: If torchcodec is not installed.
    """
    decoder = _decoder(path, "approximate")
    return int(decoder.metadata.num_frames)


@dataclass(slots=True)
class VideoMetadata:
    """Basic video metadata needed by the render/preview pipeline.

    Attributes:
        num_frames: Total decoded frame count.
        width: Frame width in pixels.
        height: Frame height in pixels.
        fps: Average frames per second.
    """

    num_frames: int
    width: int
    height: int
    fps: float


def video_metadata(path: str | Path) -> VideoMetadata:
    """Read frame count, dimensions, and fps for a video.

    Args:
        path: Path to the video file.

    Returns:
        The :class:`VideoMetadata`.

    Raises:
        MediaUnavailableError: If torchcodec is not installed.
    """
    meta = _decoder(path, "approximate").metadata
    return VideoMetadata(
        num_frames=int(meta.num_frames),
        width=int(meta.width),
        height=int(meta.height),
        fps=float(meta.average_fps or 25.0),
    )


def read_strip(
    path: str | Path, count: int = 5, *, seek_mode: SeekMode = "exact"
) -> tuple[list[int], list[np.ndarray], int]:
    """Read evenly spaced sample frames for the Browse strip in one pass.

    Defaults to ``"exact"`` seek mode: in ``"approximate"`` mode torchcodec derives
    ``num_frames`` from duration × fps, which can over-report the true count for
    some clips and make the last sample index point past the real end (raising
    "no more frames left to decode"). Exact mode scans the file so the count and
    every sampled index are valid; decoding is still guarded per frame.

    Args:
        path: Path to the video file.
        count: Number of evenly spaced frames (first / ¼ / ½ / ¾ / last by default).
        seek_mode: Seek mode; ``"exact"`` for an accurate frame count.

    Returns:
        An ``(indices, frames, num_frames)`` triple: the sampled frame indices that
        decoded, their HWC ``uint8`` RGB arrays (aligned), and the total frame count.

    Raises:
        MediaUnavailableError: If torchcodec is not installed.
    """
    decoder = _decoder(path, seek_mode)
    total = int(decoder.metadata.num_frames)
    indices: list[int] = []
    frames: list[np.ndarray] = []
    for raw in strip_indices(total, count):
        idx = min(raw, total - 1)
        try:
            frames.append(_to_hwc_uint8(decoder[idx]))
            indices.append(idx)
        except (RuntimeError, IndexError):
            break  # tolerate a frame-count overshoot at the tail
    return indices, frames, total


def strip_indices(total: int, count: int = 5) -> list[int]:
    """Compute evenly spaced sample indices for the Browse 5-frame strip.

    Returns indices at first / ¼ / ½ / ¾ / last by default. Pure arithmetic, so
    it needs no media backend and is unit-tested directly.

    Args:
        total: Total number of frames in the video (must be ``>= 1``).
        count: How many sample indices to return (``>= 1``).

    Returns:
        A sorted list of ``count`` distinct-where-possible frame indices.

    Raises:
        ValueError: If ``total < 1`` or ``count < 1``.
    """
    if total < 1:
        raise ValueError("total must be >= 1")
    if count < 1:
        raise ValueError("count must be >= 1")
    if count == 1:
        return [0]
    last = total - 1
    return [round(i * last / (count - 1)) for i in range(count)]
