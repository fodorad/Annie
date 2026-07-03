"""Tests for the render-job tracker (with an injected fake render function)."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from annie.core.models import VideoEntry
from annie.media.rendering import JobStatus, RenderService


def _wait_for(service: RenderService, job_id: str, *, timeout: float = 5.0) -> None:
    """Block until a job leaves the running/pending states or the timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = service.get(job_id)
        if job is not None and job.status in (JobStatus.DONE, JobStatus.FAILED):
            return
        time.sleep(0.01)


class TestRenderService(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.row = VideoEntry("vid", video_path=self.tmp / "vid.mp4", track_ids=[0])

    def _service(self, render_fn) -> RenderService:  # noqa: ANN001
        service = RenderService(render_fn, temp_dir=self.tmp / "out", max_workers=2)
        self.addCleanup(service.shutdown)
        return service

    def test_successful_job_writes_output(self) -> None:
        def fake_render(row: VideoEntry, output: Path) -> None:
            output.write_bytes(b"clip")

        service = self._service(fake_render)
        job_id = service.submit(self.row)
        _wait_for(service, job_id)
        job = service.get(job_id)
        assert job is not None
        self.assertIs(job.status, JobStatus.DONE)
        assert job.output_path is not None
        self.assertTrue(job.output_path.exists())

    def test_failed_job_records_error(self) -> None:
        def boom(row: VideoEntry, output: Path) -> None:
            raise RuntimeError("ffmpeg exploded")

        service = self._service(boom)
        job_id = service.submit(self.row)
        _wait_for(service, job_id)
        job = service.get(job_id)
        assert job is not None
        self.assertIs(job.status, JobStatus.FAILED)
        self.assertIn("exploded", job.error or "")

    def test_submit_requires_video(self) -> None:
        service = self._service(lambda r, o: None)
        with self.assertRaises(ValueError):
            service.submit(VideoEntry("vid", video_path=None))

    def test_get_unknown_job_is_none(self) -> None:
        service = self._service(lambda r, o: None)
        self.assertIsNone(service.get("nope"))

    def test_sweep_removes_old_finished_jobs(self) -> None:
        def fake_render(row: VideoEntry, output: Path) -> None:
            output.write_bytes(b"clip")

        service = self._service(fake_render)
        service.ttl_seconds = 10
        job_id = service.submit(self.row)
        _wait_for(service, job_id)
        output = service.get(job_id).output_path  # type: ignore[union-attr]

        # Not yet old enough.
        self.assertEqual(service.sweep(now=time.time()), 0)
        # Far in the future -> swept.
        swept = service.sweep(now=time.time() + 10_000)
        self.assertEqual(swept, 1)
        self.assertIsNone(service.get(job_id))
        assert output is not None
        self.assertFalse(output.exists())

    def test_jobs_snapshot(self) -> None:
        service = self._service(lambda r, o: o.write_bytes(b"x"))
        job_id = service.submit(self.row)
        _wait_for(service, job_id)
        self.assertEqual(len(service.jobs()), 1)


if __name__ == "__main__":
    unittest.main()
