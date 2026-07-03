"""Cheap media probing via ffprobe (service).

Browse shows ``video`` / ``audio`` tags per sample. Whether a file *has frames* is
known from the scan (a video file is present); whether it *has an audio stream* is
not, so it is probed on demand with ``ffprobe`` — much cheaper than decoding. The
command builder is pure and unit-tested; the thin runner spawns it.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def _stream_probe_command(path: str | Path, kind: str) -> list[str]:
    """Build the ffprobe argv that lists a file's streams of ``kind`` (``a``/``v``)."""
    return [
        "ffprobe", "-v", "error",
        "-select_streams", kind,
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        str(path),
    ]  # fmt: skip


def audio_probe_command(path: str | Path) -> list[str]:
    """Build the ffprobe argv that lists a file's audio streams.

    Args:
        path: The media file to inspect.

    Returns:
        The ffprobe argv (no shell). Non-empty stdout means an audio stream exists.
    """
    return _stream_probe_command(path, "a")


def video_probe_command(path: str | Path) -> list[str]:
    """Build the ffprobe argv that lists a file's video streams.

    Args:
        path: The media file to inspect.

    Returns:
        The ffprobe argv (no shell). Empty stdout means the file has no video stream
        (e.g. an ``.mp4`` that is really audio-only and yields no frames).
    """
    return _stream_probe_command(path, "v")


def _has_stream(cmd: list[str]) -> bool:  # pragma: no cover - needs system ffprobe
    """Run an ffprobe stream-listing command; ``True`` if it reports any stream."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    except OSError:
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def has_audio(path: str | Path) -> bool:  # pragma: no cover - needs system ffprobe
    """Return whether ``path`` has at least one audio stream.

    Args:
        path: The media file to inspect.

    Returns:
        ``True`` if ffprobe reports an audio stream, ``False`` otherwise (including
        on any ffprobe error, so a missing ffprobe degrades to "no audio").
    """
    return _has_stream(audio_probe_command(path))


def has_video(path: str | Path) -> bool:  # pragma: no cover - needs system ffprobe
    """Return whether ``path`` has at least one video stream.

    Args:
        path: The media file to inspect.

    Returns:
        ``True`` if ffprobe reports a video stream, ``False`` otherwise.
    """
    return _has_stream(video_probe_command(path))
