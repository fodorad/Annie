"""Render pipeline: burn annotations into a browser-playable preview clip.

A render is submitted as a **job** and runs on a background thread, so the
unpredictable, CPU-bound work of decoding and re-encoding never stalls the UI.
The submit → poll → path contract is identical whether the work is backed by a
thread pool (now) or an external queue (later), so the seam costs nothing today
and upgrades cleanly.

The job tracker (submit / status / sweep) is pure Python and unit-tested with an
injected render function. The default render function (:func:`burn_clip`) needs
the ``media`` extra and a system FFmpeg; it decodes frames, draws the overlay
(:func:`annie.color.draw_overlay`), pipes raw RGB into an ``ffmpeg`` subprocess
(libx264), and muxes the source audio back. Outputs land in a temp dir that a TTL
sweeper purges, so rendered clips never accumulate on disk.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import Lock

from annie.core.config import settings
from annie.core.models import VideoEntry

RenderFn = Callable[[VideoEntry, Path], None]
"""Signature of a render function.

Given a video entry and an output path, produce a browser-playable annotated clip.
"""


class JobStatus(StrEnum):
    """Lifecycle states of a render job."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(slots=True)
class RenderJob:
    """A single render job and its current state.

    Attributes:
        job_id: Unique opaque id used to poll for status.
        row_key: The Browse row this render belongs to.
        status: Current :class:`JobStatus`.
        output_path: Path to the finished clip once ``status`` is ``DONE``.
        error: Error message when ``status`` is ``FAILED``.
        created_at: Monotonic-ish wall-clock submit time (epoch seconds).
    """

    job_id: str
    row_key: str
    status: JobStatus = JobStatus.PENDING
    output_path: Path | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)


class RenderService:
    """Thread-pool-backed render-job tracker with a TTL temp sweeper.

    Args:
        render_fn: The function that actually produces a clip. Defaults to
            :func:`burn_clip`; tests inject a fake to exercise the lifecycle
            without a media backend.
        temp_dir: Directory for rendered clips. Defaults to the configured temp
            dir.
        max_workers: Maximum concurrent renders. Defaults to the configured value.
        ttl_seconds: Age after which a finished clip is eligible for sweeping.
    """

    def __init__(
        self,
        render_fn: RenderFn | None = None,
        *,
        temp_dir: str | Path | None = None,
        max_workers: int | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self._render_fn: RenderFn = render_fn or burn_clip
        self.temp_dir = Path(temp_dir) if temp_dir is not None else settings.temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        # None → read settings.temp_ttl_seconds live at sweep time (so UI changes take effect).
        # An explicit int → fixed override (used by tests and callers that need a known TTL).
        self.ttl_seconds: int | None = ttl_seconds
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers if max_workers is not None else settings.render_max_workers
        )
        self._jobs: dict[str, RenderJob] = {}
        self._lock = Lock()

    def submit(self, entry: VideoEntry) -> str:
        """Enqueue a render for ``entry`` and return its job id immediately.

        Args:
            entry: The video to render. Must have a video file to decode.

        Returns:
            The job id to poll with :meth:`get`.

        Raises:
            ValueError: If the entry has no video path to render.
        """
        if entry.video_path is None:
            raise ValueError("cannot render a video-less entry")
        job_id = uuid.uuid4().hex
        job = RenderJob(job_id=job_id, row_key=entry.key)
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._run, job, entry)
        return job_id

    def _run(self, job: RenderJob, entry: VideoEntry) -> None:
        """Execute one job on a worker thread, recording its outcome."""
        self._set_status(job.job_id, JobStatus.RUNNING)
        output = self.temp_dir / f"{job.job_id}.mp4"
        try:
            self._render_fn(entry, output)
        except Exception as exc:  # noqa: BLE001 - jobs must never crash the pool
            with self._lock:
                job.status = JobStatus.FAILED
                job.error = str(exc)
            return
        with self._lock:
            job.status = JobStatus.DONE
            job.output_path = output

    def _set_status(self, job_id: str, status: JobStatus) -> None:
        """Atomically update a job's status."""
        with self._lock:
            self._jobs[job_id].status = status

    def get(self, job_id: str) -> RenderJob | None:
        """Return the job for ``job_id``, or ``None`` if unknown.

        Args:
            job_id: The id returned by :meth:`submit`.

        Returns:
            The :class:`RenderJob`, or ``None``.
        """
        with self._lock:
            return self._jobs.get(job_id)

    def jobs(self) -> list[RenderJob]:
        """Return a snapshot of all tracked jobs.

        Returns:
            A list copy of the current jobs.
        """
        with self._lock:
            return list(self._jobs.values())

    def sweep(self, *, now: float | None = None) -> int:
        """Delete finished clips older than the TTL and forget their jobs.

        Args:
            now: Override for the current epoch time (for testing).

        Returns:
            The number of jobs swept.
        """
        ttl = self.ttl_seconds if self.ttl_seconds is not None else settings.temp_ttl_seconds
        cutoff = (now if now is not None else time.time()) - ttl
        swept = 0
        with self._lock:
            for job_id in list(self._jobs):
                job = self._jobs[job_id]
                if job.status in (JobStatus.DONE, JobStatus.FAILED) and job.created_at < cutoff:
                    if job.output_path is not None and job.output_path.exists():
                        job.output_path.unlink()
                    del self._jobs[job_id]
                    swept += 1
        return swept

    def clear_all(self) -> tuple[int, int]:
        """Delete every finished clip and forget all non-running jobs.

        Unlike :meth:`sweep`, this ignores the TTL and also removes any orphaned
        ``.mp4`` files in :attr:`temp_dir` that are not tracked by a job (e.g.
        left over from a previous process). Running jobs are left untouched so an
        in-flight render is not corrupted.

        Returns:
            A ``(jobs_cleared, files_deleted)`` pair — the number of jobs removed
            from the tracker and the number of files actually unlinked from disk.
        """
        jobs_cleared = 0
        files_deleted = 0
        with self._lock:
            for job_id in list(self._jobs):
                job = self._jobs[job_id]
                if job.status is JobStatus.RUNNING:
                    continue
                if job.output_path is not None and job.output_path.exists():
                    job.output_path.unlink()
                    files_deleted += 1
                del self._jobs[job_id]
                jobs_cleared += 1
        # Remove any orphaned mp4s not tracked by a current job.
        tracked = {job.output_path for job in self._jobs.values() if job.output_path}
        for f in self.temp_dir.iterdir():
            if f.suffix == ".mp4" and f not in tracked:
                f.unlink(missing_ok=True)
                files_deleted += 1
        return jobs_cleared, files_deleted

    def shutdown(self) -> None:
        """Shut the worker pool down, waiting for in-flight jobs to finish."""
        self._executor.shutdown(wait=True)


def burn_clip(
    entry: VideoEntry, output_path: Path
) -> None:  # pragma: no cover - needs media + ffmpeg
    """Render a video's combined annotations into a browser-playable clip.

    Decodes **every** frame of the source (a frame with no detections is written
    through unchanged, so the render always has the same frame count as the
    original) and draws, on each: the vdet boxes in flat **blue**, every track in
    its own **stable unique colour** (never blue/green), and the **active /
    main-character** track in **green**. Frames are piped as raw RGB into an
    ``ffmpeg`` libx264 subprocess, and the source audio is muxed back (the encode is
    not ``-shortest``, so a shorter audio track never truncates the video).

    Args:
        entry: The video to render; ``entry.video_path`` must be set and at least
            one of vdet/tracks should be present for boxes to appear.
        output_path: Where to write the encoded ``.mp4``.

    Raises:
        annie.decode.MediaUnavailableError: If the ``media`` extra is absent.
        ValueError: If the entry has no video path.
        RuntimeError: If the ffmpeg subprocess fails.
    """
    from annie.media import decode  # local import: optional media dependency
    from annie.media.color import draw_overlay
    from annie.media.compose import load_entry_annotations, merge_frame

    if entry.video_path is None:
        raise ValueError("burn_clip requires a video path")

    vdet_by_frame, tracks_by_id = load_entry_annotations(entry)
    include = sorted(tracks_by_id)  # draw every track on the full render

    meta = decode.video_metadata(entry.video_path)  # width / height / fps
    decoder = decode._decoder(entry.video_path, "exact")  # noqa: SLF001 - service owns decode
    # The exact decoder's count is authoritative; approximate metadata can over-report
    # num_frames and walk the loop past the real end ("no more frames to decode").
    num_frames = int(decoder.metadata.num_frames)

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{meta.width}x{meta.height}", "-r", f"{meta.fps}",
        "-i", "-",
        "-i", str(entry.video_path),
        "-map", "0:v:0", "-map", "1:a:0?",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(output_path),
    ]  # fmt: skip
    # Drain ffmpeg's progress/error output to a temp file so a full stderr pipe
    # can never deadlock the frame-writing loop on a long clip.
    with tempfile.TemporaryFile() as errlog:
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errlog)
        assert process.stdin is not None
        try:
            for frame_idx in range(num_frames):
                frame = decode._to_hwc_uint8(decoder[frame_idx])  # noqa: SLF001
                merged = merge_frame(frame_idx, vdet_by_frame, tracks_by_id, include)
                image = draw_overlay(
                    frame, merged, has_tracks=True, active_track_id=entry.active_track_id
                )
                process.stdin.write(image.tobytes())
        except BrokenPipeError:
            pass  # ffmpeg exited early; the returncode check below reports why
        finally:
            process.stdin.close()
        process.wait()
        if process.returncode != 0:
            errlog.seek(0)
            raise RuntimeError(f"ffmpeg failed: {errlog.read().decode('utf-8', 'replace')[-500:]}")
