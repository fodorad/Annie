"""Cut a browser-playable sub-clip of a video for one segment band (service).

The Segment-review task lets a reviewer watch a band's exact ``[start, end)`` span
before accepting or dropping it. :func:`cut_clip` re-encodes that span to a small MP4
the browser can play inline. The cut is **frame-accurate** — the input seek is placed
*after* ``-i`` and the stream is re-encoded (libx264) rather than stream-copied — so the
embedded clip lines up with the preview strip's boundaries instead of snapping to the
nearest keyframe.

Cuts land in a ``clips/`` subdirectory of :attr:`~annie.core.config.Settings.temp_dir`
and are cached by ``(video, start, end)`` so re-opening a band does not re-cut. The
command mirrors :func:`annie.media.rendering.burn_clip`'s web-friendly encode
(preview-grade CRF + faststart muxing).

The subdirectory is what keeps a cut alive: the render sweeper deletes any ``.mp4``
sitting *directly* in ``temp_dir`` once it is older than the TTL (three minutes by
default), and it protects only in-flight render jobs — a band clip is neither a job nor
refreshed while it plays, so a reviewer studying one card for a few minutes would have
had the file unlinked out from under the embedded player. The sweeper's ``iterdir()``
scan does not recurse, so clips in ``clips/`` are simply out of its reach.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path

from annie.core.config import settings
from annie.media.rendering import CRF, FASTSTART, PRESET

#: In-process cache of ``(video, start, end) → output path`` so a band opened twice in
#: one session re-uses its cut. An entry whose file has gone missing is re-cut.
_clip_cache: dict[tuple[str, float, float], Path] = {}

#: Name of the ``temp_dir`` subdirectory holding band cuts, kept out of the flat
#: directory the render sweeper walks (see the module docstring).
_CLIPS_SUBDIR = "clips"


def clips_dir() -> Path:
    """The directory band cuts are written to (``<temp_dir>/clips``).

    Resolved at call time rather than import time so a test or a runtime settings change
    that repoints :attr:`~annie.core.config.Settings.temp_dir` is honoured.

    Returns:
        The clips directory path (not created — :func:`cut_clip` makes it on demand).
    """
    return settings.temp_dir / _CLIPS_SUBDIR


def _clip_name(video_path: Path, start: float, end: float) -> str:
    """Build a stable, collision-resistant output filename for one span.

    The span is hashed with the resolved source path so two different videos with the
    same ``video_id`` stem never share a cache file, while the stem stays in the name
    for readability when browsing the temp dir.

    Args:
        video_path: The resolved source video path.
        start: Span start, in seconds.
        end: Span end, in seconds.

    Returns:
        A ``<stem>_<hash>.mp4`` filename.
    """
    payload = f"{video_path.resolve()}|{start:.3f}|{end:.3f}".encode()
    return f"{video_path.stem}_{hashlib.sha256(payload).hexdigest()[:12]}.mp4"


def cut_clip(video_path: str | Path, start: float, end: float) -> Path:
    """Cut ``[start, end)`` of ``video_path`` to a browser-playable MP4 (frame-accurate).

    The result is cached by ``(video, start, end)``: a cached file that still exists is
    returned as-is, so re-opening the same band never re-encodes.

    Args:
        video_path: The long source video the band belongs to.
        start: Span start, in seconds.
        end: Span end, in seconds (must be greater than ``start``).

    Returns:
        The path to the cut clip, under :func:`clips_dir`.

    Raises:
        ValueError: If ``end`` is not strictly after ``start``.
        RuntimeError: If the ffmpeg subprocess fails.
    """
    src = Path(video_path)
    if end <= start:
        raise ValueError(f"end ({end}) must be after start ({start})")

    key = (str(src.resolve()), round(start, 3), round(end, 3))
    cached = _clip_cache.get(key)
    if cached is not None and cached.exists():
        return cached

    destination = clips_dir()
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / _clip_name(src, start, end)

    cmd = [
        "ffmpeg", "-y",
        # -ss/-to *after* -i is the accurate (decode-then-trim) form: the span lines up
        # with the preview strip rather than snapping to the nearest keyframe.
        "-i", str(src),
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", PRESET, "-crf", str(CRF),
        "-movflags", FASTSTART,
        "-c:a", "aac",
        str(output),
    ]  # fmt: skip
    with tempfile.TemporaryFile() as errlog:
        process = subprocess.run(cmd, stdin=subprocess.DEVNULL, stderr=errlog, check=False)
        if process.returncode != 0:
            errlog.seek(0)
            raise RuntimeError(f"ffmpeg failed: {errlog.read().decode('utf-8', 'replace')[-500:]}")

    _clip_cache[key] = output
    return output
