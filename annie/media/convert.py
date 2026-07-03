"""Re-encode an audio/video dataset to a consistent, validated form (service).

Some source clips are subtly broken: their container metadata disagrees with the
real frame count (torchcodec's ``approximate`` mode derives ``num_frames`` from
duration × fps, so a clip can report more frames than it has and blow up decoding
near the end). Re-encoding every file to constant-frame-rate H.264 — and
**validating** the result with torchcodec — makes the dataset consistent for Annie
and for downstream dataloaders / feature extraction.

The module is split into pure, unit-tested pieces (ffmpeg command builders, stem
pairing, length math, the batch runner with an injected work function) and thin
impure wrappers (``run_ffmpeg``, ``validate_video``, ``convert_*_file``) that need a
system FFmpeg and the ``media`` extra and are exercised manually.

Audio/video length contract when muxing: the audio filter ``apad`` pads the audio
with silence and ``-shortest`` trims the output to the (finite) video stream, so the
muxed audio is exactly the video's duration — short audio is padded, long audio is
cut — which is what :func:`audio_video_consistent` checks.
"""

from __future__ import annotations

import csv
import subprocess
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

AUDIO_INPUT_SUFFIXES: tuple[str, ...] = (".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus")
"""Accepted source-audio suffixes (lower-case, dotted)."""
VIDEO_INPUT_SUFFIXES: tuple[str, ...] = (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v")
"""Accepted source-video suffixes (lower-case, dotted)."""

AUDIO_OUTPUT_FORMATS: tuple[str, ...] = ("wav", "flac", "mp3")
"""Offered audio output containers/codecs."""
VIDEO_OUTPUT_FORMATS: tuple[str, ...] = ("mp4",)
"""Offered video containers/codecs."""

COMMON_SAMPLE_RATES: tuple[int, ...] = (8000, 16000, 22050, 44100, 48000)
"""Sample rates the UI offers as quick picks."""
COMMON_FPS: tuple[float, ...] = (10, 24, 25, 30)
"""Quick-pick constant frame rates."""


@dataclass(slots=True)
class AudioOptions:
    """Audio re-encode settings.

    Attributes:
        out_format: Output container/codec (``"wav"`` default, PCM 16-bit).
        sample_rate: Target sample rate in Hz.
        channels: Channel count (``1`` = mono, the default).
    """

    out_format: str = "wav"
    sample_rate: int = 16000
    channels: int = 1


@dataclass(slots=True)
class VideoOptions:
    """Video re-encode settings.

    Attributes:
        width: Target width in px, or ``None`` to keep the source width.
        height: Target height in px, or ``None`` to keep the source height.
        grayscale: Desaturate to black-and-white when ``True`` (still yuv420p).
        fps: Target constant frame rate, or ``None`` to keep the source fps.
        add_audio: Mux the matching-stem audio into the output when ``True``.
        min_frames: Reject (skip) any source whose output would have fewer than this
            many decodable frames. ``2`` (default) drops degenerate single-frame
            "videos" (audio-only files that carry one thumbnail-like frame); ``1``
            only requires that at least one frame decodes.
        out_format: Output container (``"mp4"``).
    """

    width: int | None = None
    height: int | None = None
    grayscale: bool = False
    fps: float | None = None
    add_audio: bool = True
    audio_only_black: bool = False
    min_frames: int = 2
    out_format: str = "mp4"

    @property
    def keep_resolution(self) -> bool:
        """Whether the source resolution is kept (no scaling)."""
        return self.width is None and self.height is None and not self.grayscale


BLACK_DEFAULT_SIZE = 256
"""Black-frame fallback size for a synthesised audio-only video.

Used when the options keep the original size/fps (of which there is none).
"""
BLACK_DEFAULT_FPS = 25.0
"""Fps of the synthesised black video when the source has none."""


# ── ffmpeg command builders (pure) ───────────────────────────────────────────


def audio_command(src: str | Path, dst: str | Path, opts: AudioOptions) -> list[str]:
    """Build the ffmpeg argv to re-encode one audio file.

    Args:
        src: Source audio (or a media file with an audio stream).
        dst: Destination path.
        opts: Audio settings.

    Returns:
        The ffmpeg argv list (no shell).
    """
    return [
        "ffmpeg", "-y", "-i", str(src),
        "-vn",
        "-ar", str(opts.sample_rate),
        "-ac", str(opts.channels),
        str(dst),
    ]  # fmt: skip


def video_filters(opts: VideoOptions) -> str:
    """Build the ``-vf`` filter chain string for the given video options.

    Args:
        opts: Video settings.

    Returns:
        A comma-joined filter chain, or ``""`` when no spatial filter is needed.
        A single given dimension scales the other by ``-2`` (keeps aspect, even).
    """
    parts: list[str] = []
    if opts.width is not None and opts.height is not None:
        parts.append(f"scale={opts.width}:{opts.height}")
    elif opts.width is not None:
        parts.append(f"scale={opts.width}:-2")
    elif opts.height is not None:
        parts.append(f"scale=-2:{opts.height}")
    if opts.grayscale:
        parts.append("hue=s=0")
    if opts.fps is not None:
        parts.append(f"fps={opts.fps:g}")
    return ",".join(parts)


def video_command(
    src: str | Path, dst: str | Path, opts: VideoOptions, audio_path: str | Path | None = None
) -> list[str]:
    """Build the ffmpeg argv to re-encode one video (constant frame rate H.264).

    Audio handling: when ``opts.add_audio`` and ``audio_path`` is given, the audio
    is muxed and matched to the video length via ``apad`` + ``-shortest``; otherwise
    the output has no audio (``-an``).

    Args:
        src: Source video.
        dst: Destination ``.mp4``.
        opts: Video settings.
        audio_path: Matching audio file to mux, or ``None``.

    Returns:
        The ffmpeg argv list (no shell).
    """
    use_audio = opts.add_audio and audio_path is not None
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if use_audio:
        cmd += ["-i", str(audio_path)]
    vf = video_filters(opts)
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-vsync", "cfr"]
    if use_audio:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-af", "apad", "-shortest"]
    else:
        cmd += ["-an"]
    cmd += [str(dst)]
    return cmd


def black_video_command(dst: str | Path, audio: str | Path, opts: VideoOptions) -> list[str]:
    """Build the ffmpeg argv for an audio-only item: a black-frame video + audio.

    The black ``lavfi`` source is infinite and ``-shortest`` trims the output to the
    (finite) audio, so the synthesised video has exactly the audio's duration. The
    frame size / fps come from ``opts`` or fall back to :data:`BLACK_DEFAULT_SIZE` /
    :data:`BLACK_DEFAULT_FPS` when the options keep the (non-existent) original.

    Args:
        dst: Destination ``.mp4``.
        audio: The audio to carry.
        opts: Video settings (used for size / fps).

    Returns:
        The ffmpeg argv list (no shell).
    """
    width = opts.width or BLACK_DEFAULT_SIZE
    height = opts.height or BLACK_DEFAULT_SIZE
    fps = opts.fps or BLACK_DEFAULT_FPS
    return [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps:g}",
        "-i", str(audio),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(dst),
    ]  # fmt: skip


# ── planning (pure) ──────────────────────────────────────────────────────────


def _is_junk(name: str) -> bool:
    """Return whether a filename is OS junk (any dotfile)."""
    return name.startswith(".")


def _list_inputs(directory: Path | None, suffixes: tuple[str, ...]) -> list[Path]:
    """List non-junk files in ``directory`` whose suffix is in ``suffixes``, sorted."""
    if directory is None or not directory.is_dir():
        return []
    out = [
        p
        for p in directory.iterdir()
        if p.is_file() and not _is_junk(p.name) and p.suffix.lower() in suffixes
    ]
    return sorted(out, key=lambda p: p.name.lower())


@dataclass(slots=True)
class AudioItem:
    """One planned audio conversion (source → destination)."""

    src: Path
    dst: Path

    @property
    def label(self) -> str:
        """Display name for progress (the source filename)."""
        return self.src.name


@dataclass(slots=True)
class VideoItem:
    """One planned video conversion.

    ``src`` is the source video, or ``None`` for an **audio-only** item that should
    be synthesised as a black-frame video carrying ``audio``.
    """

    src: Path | None
    dst: Path
    audio: Path | None = None

    @property
    def label(self) -> str:
        """Display name for progress (the source filename, or the output name)."""
        return (self.src or self.dst).name

    @property
    def is_black(self) -> bool:
        """Whether this item synthesises a black-frame video (no source video)."""
        return self.src is None


def plan_audio(
    in_dir: str | Path | None, out_dir: str | Path, opts: AudioOptions
) -> list[AudioItem]:
    """Plan the audio batch: one :class:`AudioItem` per input file.

    Args:
        in_dir: Folder of source audio files.
        out_dir: Destination folder.
        opts: Audio settings (its ``out_format`` sets the destination suffix).

    Returns:
        The planned items (empty if the input folder is missing).
    """
    out = Path(out_dir)
    return [
        AudioItem(src, out / f"{src.stem}.{opts.out_format}")
        for src in _list_inputs(Path(in_dir) if in_dir else None, AUDIO_INPUT_SUFFIXES)
    ]


def plan_video(
    in_dir: str | Path | None,
    out_dir: str | Path,
    opts: VideoOptions,
    audio_dir: str | Path | None = None,
) -> list[VideoItem]:
    """Plan the video batch: one :class:`VideoItem` per input file.

    When ``opts.add_audio`` and ``audio_dir`` is given, each video is paired by stem
    with an audio file of any supported format in ``audio_dir``. When
    ``opts.audio_only_black`` is also set, an audio file with **no** matching video
    becomes a black-frame video carrying that audio; otherwise such audio yields no
    video at all (it stays only in the audio folder).

    Args:
        in_dir: Folder of source videos.
        out_dir: Destination folder.
        opts: Video settings (its ``out_format`` sets the destination suffix).
        audio_dir: Folder of audio to mux (typically the audio output folder).

    Returns:
        The planned items (empty if the input folder is missing).
    """
    out = Path(out_dir)
    audio_by_stem: dict[str, Path] = {}
    if (opts.add_audio or opts.audio_only_black) and audio_dir:
        for audio in _list_inputs(Path(audio_dir), AUDIO_INPUT_SUFFIXES):
            audio_by_stem.setdefault(audio.stem, audio)

    videos = _list_inputs(Path(in_dir) if in_dir else None, VIDEO_INPUT_SUFFIXES)
    video_stems = {v.stem for v in videos}
    items: list[VideoItem] = []
    for src in videos:
        audio_match = audio_by_stem.get(src.stem) if opts.add_audio else None
        items.append(VideoItem(src, out / f"{src.stem}.{opts.out_format}", audio_match))

    if opts.add_audio and opts.audio_only_black:
        for stem, audio in audio_by_stem.items():
            if stem not in video_stems:
                items.append(VideoItem(None, out / f"{stem}.{opts.out_format}", audio))
    return items


# ── length consistency (pure) ────────────────────────────────────────────────


def expected_frame_count(duration_s: float, fps: float) -> int:
    """Return the number of frames a ``duration_s`` clip at ``fps`` should have."""
    return round(duration_s * fps)


def expected_sample_count(duration_s: float, sample_rate: int) -> int:
    """Return the number of audio samples a ``duration_s`` clip at ``sample_rate`` has."""
    return round(duration_s * sample_rate)


def audio_video_consistent(
    num_frames: int, fps: float, num_samples: int, sample_rate: int, *, tolerance_s: float = 0.04
) -> bool:
    """Whether an audio track and a video stream have matching durations.

    Args:
        num_frames: Decoded video frame count.
        fps: Video frame rate.
        num_samples: Decoded audio sample count (per channel).
        sample_rate: Audio sample rate.
        tolerance_s: Allowed absolute difference in seconds (≈ one frame at 25 fps).

    Returns:
        ``True`` if the two durations agree within ``tolerance_s``.
    """
    if fps <= 0 or sample_rate <= 0:
        return False
    video_s = num_frames / fps
    audio_s = num_samples / sample_rate
    return abs(video_s - audio_s) <= tolerance_s


# ── impure: ffmpeg + torchcodec validation ───────────────────────────────────


def run_ffmpeg(cmd: list[str]) -> None:  # pragma: no cover - needs system ffmpeg
    """Run an ffmpeg command, raising :class:`RuntimeError` with its tail on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-500:]}")


def decodable_frames(path: str | Path) -> int:  # pragma: no cover - needs media
    """Return the torchcodec frame count, verified by actually decoding, or ``0``.

    A file with a declared video stream but **no real frames** (an audio-only
    ``.mp4`` that VLC plays as audio) can still report ``num_frames >= 1`` in its
    metadata, so the count alone is not enough — we decode the first (and last)
    frame to confirm they really exist. Any decode failure yields ``0``.

    Args:
        path: The media file to inspect.

    Returns:
        The number of decodable frames (``0`` if the frames cannot be decoded).
    """
    from annie.media import decode

    try:
        decoder = decode._decoder(path, "exact")  # noqa: SLF001 - service owns decode
        n = int(decoder.metadata.num_frames)
        if n < 1:
            return 0
        _ = decoder[0]  # raises if the declared frame is not actually decodable
        _ = decoder[n - 1]
    except Exception:  # noqa: BLE001 - any decode failure means "no usable frames"
        return 0
    return n


def validate_video(  # pragma: no cover - needs media
    path: str | Path, min_frames: int = 1
) -> tuple[bool, str]:
    """Validate a re-encoded video with torchcodec.

    Requires at least ``min_frames`` frames (so a degenerate single-frame, audio-only
    result is rejected), decodes **every** frame in ``exact`` mode (so an unreadable
    frame raises), and confirms the ``exact`` and ``approximate`` frame counts agree —
    the discrepancy that makes some source clips fail late in decoding.

    Args:
        path: The video to validate.
        min_frames: Minimum decodable frame count required to pass.

    Returns:
        An ``(ok, message)`` pair.
    """
    from annie.media import decode

    exact = decode._decoder(path, "exact")  # noqa: SLF001 - service owns decode
    n_exact = int(exact.metadata.num_frames)
    if n_exact < min_frames:
        return False, f"only {n_exact} decodable frame(s) (need >= {min_frames})"
    for i in range(n_exact):
        _ = exact[i]  # raises if a frame is unreadable
    n_approx = int(decode._decoder(path, "approximate").metadata.num_frames)  # noqa: SLF001
    if n_exact != n_approx:
        return False, f"frame-count mismatch (exact={n_exact}, approx={n_approx})"
    return True, f"{n_exact} frames OK"


def _remove(path: Path) -> None:  # pragma: no cover - filesystem cleanup
    """Delete ``path`` if it exists, ignoring errors (so failures leave no output)."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def convert_audio_file(  # pragma: no cover - needs system ffmpeg
    item: AudioItem, opts: AudioOptions
) -> None:
    """Re-encode one audio file, skipping it if a non-empty output already exists.

    On any failure the (possibly partial) output is removed, so the output folder
    only ever contains good files.
    """
    if item.dst.is_file() and item.dst.stat().st_size > 0:
        return  # resume: already converted
    item.dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_ffmpeg(audio_command(item.src, item.dst, opts))
        if not item.dst.is_file() or item.dst.stat().st_size == 0:
            raise RuntimeError("output missing or empty")
    except Exception:
        _remove(item.dst)
        raise


def convert_video_file(  # pragma: no cover - needs media + ffmpeg
    item: VideoItem, opts: VideoOptions
) -> None:
    """Re-encode one video (or synthesise a black-frame one), then validate it.

    Grace handling: a source ``.mp4`` is skipped with an error (and never lands in
    the output folder) when it has **no video stream** or yields fewer than
    ``opts.min_frames`` decodable frames — which drops degenerate single-frame
    "videos" (audio-only files carrying one thumbnail-like frame, which VLC plays as
    audio-only). A stale bad output from a previous run is removed in those cases too.
    If a torchcodec-valid output already exists it is skipped (resume); any failure
    removes a partial output.
    """
    from annie.media import probe

    if item.src is not None:
        if not probe.has_video(item.src):
            _remove(item.dst)
            raise RuntimeError("no video stream — skipped (audio-only file)")
        frames = decodable_frames(item.src)
        if frames < opts.min_frames:
            _remove(item.dst)
            raise RuntimeError(
                f"only {frames} decodable frame(s) (need >= {opts.min_frames}) — "
                "skipped (audio-only / degenerate video)"
            )

    if item.dst.is_file():
        try:
            already_ok, _ = validate_video(item.dst, opts.min_frames)
        except Exception:  # noqa: BLE001 - a corrupt existing output is not valid
            already_ok = False
        if already_ok:
            return  # resume: a valid output already exists
        _remove(item.dst)  # stale/corrupt output from a previous run → re-encode

    item.dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if item.src is None:
            if item.audio is None:
                raise RuntimeError("audio-only item has no audio to carry")
            run_ffmpeg(black_video_command(item.dst, item.audio, opts))
        else:
            run_ffmpeg(video_command(item.src, item.dst, opts, item.audio))
        ok, message = validate_video(item.dst, opts.min_frames)
        if not ok:
            raise RuntimeError(f"validation failed: {message}")
    except Exception:
        _remove(item.dst)
        raise


def export_ids(path: str | Path, ids: list[str]) -> Path:
    """Write a one-column CSV (``id``) of the given filenames' stems, sorted.

    Args:
        path: Destination CSV path.
        ids: Filenames (or ids); their stems are written, de-duplicated and sorted.

    Returns:
        The path written.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    stems = sorted({Path(name).stem for name in ids})
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("id",))
        writer.writerows((stem,) for stem in stems)
    return out


def export_failures(path: str | Path, failures: list[tuple[str, str]]) -> Path:
    """Write a two-column CSV (``id,error``) for the failed files, sorted by id.

    Args:
        path: Destination CSV path.
        failures: ``(filename, error)`` pairs.

    Returns:
        The path written.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted((Path(name).stem, error) for name, error in failures)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("id", "error"))
        writer.writerows(rows)
    return out


# ── batch runner with progress ───────────────────────────────────────────────

ConvertStatus = Literal["idle", "running", "done", "cancelled"]
"""Lifecycle state of a batch conversion run."""


@dataclass(slots=True)
class ConvertProgress:
    """A snapshot of a running (or finished) conversion batch.

    Attributes:
        title: Human label of the batch (e.g. ``"Audio"`` / ``"Video"``).
        total: Number of files in the batch.
        done: Number processed so far (succeeded or failed).
        current: The file currently being processed.
        succeeded: Filenames that converted (or were skipped as already done).
        failed: ``(filename, error)`` pairs for files that failed.
        started_at: Epoch seconds when the batch started, or ``None``.
        finished_at: Epoch seconds when it finished, or ``None`` while running.
        status: One of :data:`ConvertStatus`.
    """

    title: str = ""
    total: int = 0
    done: int = 0
    current: str = ""
    succeeded: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None
    status: ConvertStatus = "idle"

    @property
    def elapsed(self) -> float:
        """Seconds elapsed since the batch started (frozen once finished)."""
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(end - self.started_at, 0.0)

    @property
    def fraction(self) -> float:
        """Progress as a fraction in ``[0, 1]``."""
        return (self.done / self.total) if self.total else 0.0

    @property
    def eta_seconds(self) -> float | None:
        """Estimated seconds remaining, or ``None`` if not yet estimable."""
        if self.status != "running" or self.done == 0:
            return None
        return (self.elapsed / self.done) * (self.total - self.done)

    @property
    def succeeded_count(self) -> int:
        """Number of files processed without error."""
        return self.done - len(self.failed)


class ConversionRunner:
    """Runs one conversion batch at a time on a background thread, with progress.

    The batch loop is generic: it is given a list of items, a ``work`` callable that
    converts one item (raising on failure), and a ``label`` callable for display.
    Failures are recorded per item and never stop the batch.
    """

    def __init__(self) -> None:
        self._progress = ConvertProgress()
        self._lock = Lock()
        self._cancel = False
        self._executor = ThreadPoolExecutor(max_workers=1)

    def running(self) -> bool:
        """Whether a batch is currently in progress."""
        with self._lock:
            return self._progress.status == "running"

    def progress(self) -> ConvertProgress:
        """Return a consistent snapshot of the current progress."""
        with self._lock:
            p = self._progress
            return ConvertProgress(
                title=p.title,
                total=p.total,
                done=p.done,
                current=p.current,
                succeeded=list(p.succeeded),
                failed=list(p.failed),
                started_at=p.started_at,
                finished_at=p.finished_at,
                status=p.status,
            )

    def cancel(self) -> None:
        """Request cancellation; the batch stops after the current file."""
        self._cancel = True

    def start(
        self,
        title: str,
        items: list,  # list[AudioItem | VideoItem]
        work: Callable[[object], None],
        *,
        label: Callable[[object], str] = lambda it: getattr(it, "label", str(it)),
    ) -> Future:
        """Begin a batch on the worker thread.

        Args:
            title: Human label for the batch.
            items: The planned items to convert.
            work: Converts one item, raising on failure.
            label: Maps an item to its display name.

        Returns:
            The :class:`~concurrent.futures.Future` of the batch (mostly for tests).

        Raises:
            RuntimeError: If a batch is already running.
        """
        if self.running():
            raise RuntimeError("a conversion is already running")
        with self._lock:
            self._cancel = False
            self._progress = ConvertProgress(
                title=title, total=len(items), status="running", started_at=time.time()
            )
        return self._executor.submit(self._run, items, work, label)

    def _run(
        self, items: list, work: Callable[[object], None], label: Callable[[object], str]
    ) -> None:
        """Execute the batch sequentially, updating progress under the lock."""
        for item in items:
            if self._cancel:
                break
            name = label(item)
            with self._lock:
                self._progress.current = name
            try:
                work(item)
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
                with self._lock:
                    self._progress.failed.append((name, str(exc)))
            else:
                with self._lock:
                    self._progress.succeeded.append(name)
            with self._lock:
                self._progress.done += 1
        with self._lock:
            self._progress.finished_at = time.time()
            self._progress.current = ""
            self._progress.status = "cancelled" if self._cancel else "done"

    def shutdown(self) -> None:
        """Shut the worker pool down, waiting for an in-flight batch to finish."""
        self._executor.shutdown(wait=True)
