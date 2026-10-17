"""The preview loader: decodes and encodes preview frames off the event loop (service).

Browse and the Annotator show decoded frames for every visible row. Each row used to
start its own decode the moment it was built, with no limit, and encode the frames on the
event loop. This module puts all of that behind one small interface:

- **Bounded:** at most ``max_workers`` decodes run at once, on the loader's own threads.
- **Newest batch first:** requests that arrive together (one page of rows being built)
  form a batch and start in order, top row first; a newer batch (rows just scrolled
  into view) goes ahead of older ones, so what is on screen fills first.
- **Skips the unwanted:** each request carries a ``wanted`` check, asked just before its
  decode starts. A row that was refreshed or closed while it waited is skipped and
  gets :data:`SKIPPED`, without decoding anything.
- **Encoded in the worker:** results are WebP data URIs sized for their box, ready to
  show, so the event loop only assigns them.
- **Cached:** a bounded LRU keeps recent results, so a row scrolled back into view shows
  at once. :meth:`PreviewLoader.clear` empties it (e.g. on rescan). Errors are never
  cached.

The decoders are injected (defaulting to :mod:`annie.media.preview` and
:func:`annie.media.probe.has_audio`), so tests drive the loader with plain fakes.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from annie.media.preview import build_band_strip, build_grid_preview, build_preview, to_data_uri
from annie.media.probe import has_audio

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable
    from pathlib import Path

    from PIL import Image

    from annie.core.models import VideoEntry

Box = tuple[int, int]
"""A display box ``(width, height)`` in CSS px; frames are encoded to fit it."""

BATCH_WINDOW = 0.05
"""Seconds within which consecutive requests count as one batch (one page being built)."""


class _Skipped:
    """Type of :data:`SKIPPED`."""

    def __repr__(self) -> str:
        return "SKIPPED"


SKIPPED: Final = _Skipped()
"""Returned instead of a result when the request was no longer wanted when its turn came."""


@dataclass(frozen=True, slots=True)
class Strip:
    """A row's frame strip, ready to show.

    Attributes:
        uris: One WebP data URI per strip frame, sized for the requested box.
        num_frames: The video's total frame count.
    """

    uris: list[str]
    num_frames: int


@dataclass(frozen=True, slots=True)
class Tile:
    """A grid tile's single frame, ready to show.

    Attributes:
        uri: The WebP data URI of the annotated middle frame.
        num_frames: The video's total frame count.
    """

    uri: str
    num_frames: int


@dataclass(slots=True)
class _Job:
    """One queued request: the work to run, its wanted check, and where the result goes."""

    work: Callable[[], Any]
    wanted: Callable[[], bool]
    future: asyncio.Future[Any]
    key: Hashable | None


class PreviewLoader:
    """Bounded, newest-batch-first, cached decoding of preview frames (see the module docs).

    Every public method must be awaited on the event loop that serves the UI.

    Args:
        max_workers: How many decodes may run at once.
        cache_size: How many results the LRU keeps.
        strip_fn: Decodes a video's annotated strip (default
            :func:`~annie.media.preview.build_preview`).
        tile_fn: Decodes a video's annotated middle frame (default
            :func:`~annie.media.preview.build_grid_preview`).
        band_fn: Decodes frames across a time span (default
            :func:`~annie.media.preview.build_band_strip`).
        audio_fn: Probes for an audio stream (default :func:`~annie.media.probe.has_audio`).
    """

    def __init__(
        self,
        max_workers: int,
        *,
        cache_size: int = 300,
        strip_fn: Callable[
            [VideoEntry], tuple[Image.Image, list[Image.Image], int]
        ] = build_preview,
        tile_fn: Callable[[VideoEntry], tuple[Image.Image, int]] = build_grid_preview,
        band_fn: Callable[[Path, float, float, int], list[Image.Image]] = build_band_strip,
        audio_fn: Callable[[Path], bool] = has_audio,
    ) -> None:
        self._max_workers = max(1, max_workers)
        self._pool = ThreadPoolExecutor(self._max_workers, thread_name_prefix="annie-preview")
        self._cache_size = cache_size
        self._cache: OrderedDict[Hashable, Any] = OrderedDict()
        self._batches: list[list[_Job]] = []
        self._last_submit = float("-inf")
        self._running = 0
        self._strip_fn = strip_fn
        self._tile_fn = tile_fn
        self._band_fn = band_fn
        self._audio_fn = audio_fn

    async def strip(
        self, entry: VideoEntry, box: Box, *, wanted: Callable[[], bool]
    ) -> Strip | _Skipped:
        """Decode ``entry``'s annotated strip, encoded for ``box``.

        Args:
            entry: The video; its ``active_track_id`` is part of the cache key.
            box: The display box of one strip frame.
            wanted: Asked just before decoding; ``False`` skips the request.

        Returns:
            The :class:`Strip`, or :data:`SKIPPED`.

        Raises:
            Exception: Whatever the decoder raised (e.g. an unreadable file).
        """

        def work() -> Strip:
            _thumbnail, frames, num_frames = self._strip_fn(entry)
            return Strip([to_data_uri(frame, box) for frame in frames], num_frames)

        key = ("strip", entry.key, entry.active_track_id, box)
        return await self._submit(key, work, wanted)

    async def tile(
        self, entry: VideoEntry, box: Box, *, wanted: Callable[[], bool]
    ) -> Tile | _Skipped:
        """Decode ``entry``'s annotated middle frame for a grid tile, encoded for ``box``.

        Args:
            entry: The video.
            box: The tile's display box.
            wanted: Asked just before decoding; ``False`` skips the request.

        Returns:
            The :class:`Tile`, or :data:`SKIPPED`.
        """

        def work() -> Tile:
            image, num_frames = self._tile_fn(entry)
            return Tile(to_data_uri(image, box), num_frames)

        key = ("tile", entry.key, entry.active_track_id, box)
        return await self._submit(key, work, wanted)

    async def band(
        self,
        video_path: Path,
        start: float,
        end: float,
        count: int,
        box: Box,
        *,
        wanted: Callable[[], bool],
    ) -> list[str] | _Skipped:
        """Decode ``count`` frames spread over ``[start, end)`` seconds, encoded for ``box``.

        Args:
            video_path: The video file.
            start: Span start in seconds.
            end: Span end in seconds.
            count: How many frames to sample.
            box: The display box of one frame.
            wanted: Asked just before decoding; ``False`` skips the request.

        Returns:
            One data URI per decoded frame, or :data:`SKIPPED`.
        """

        def work() -> list[str]:
            return [
                to_data_uri(frame, box) for frame in self._band_fn(video_path, start, end, count)
            ]

        key = ("band", str(video_path), start, end, count, box)
        return await self._submit(key, work, wanted)

    async def audio(self, video_path: Path, *, wanted: Callable[[], bool]) -> bool | _Skipped:
        """Probe whether ``video_path`` has an audio stream (bounded like a decode, not cached).

        Args:
            video_path: The video file.
            wanted: Asked just before probing; ``False`` skips the request.

        Returns:
            Whether an audio stream exists, or :data:`SKIPPED`.
        """
        return await self._submit(None, lambda: self._audio_fn(video_path), wanted)

    def clear(self) -> None:
        """Forget every cached result (call when the files may have changed)."""
        self._cache.clear()

    async def _submit(
        self, key: Hashable | None, work: Callable[[], Any], wanted: Callable[[], bool]
    ) -> Any:
        """Serve ``key`` from the cache, or queue ``work`` and wait for its turn."""
        if key is not None and key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        loop = asyncio.get_running_loop()
        job = _Job(work, wanted, loop.create_future(), key)
        now = loop.time()
        if self._batches and now - self._last_submit <= BATCH_WINDOW:
            self._batches[-1].append(job)
        else:
            self._batches.append([job])
        self._last_submit = now
        self._pump(loop)
        return await job.future

    def _pump(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start queued jobs while a worker is free: newest batch first, in order within it."""
        while self._running < self._max_workers and self._batches:
            batch = self._batches[-1]
            job = batch.pop(0)
            if not batch:
                self._batches.pop()
            if job.future.done():  # the caller stopped waiting (e.g. cancelled)
                continue
            try:
                still_wanted = job.wanted()
            except Exception:  # noqa: BLE001 - a dead row may raise; treat as unwanted
                still_wanted = False
            if not still_wanted:
                job.future.set_result(SKIPPED)
                continue
            self._running += 1
            running = loop.run_in_executor(self._pool, job.work)
            running.add_done_callback(lambda done, job=job: self._finish(loop, job, done))

    def _finish(
        self, loop: asyncio.AbstractEventLoop, job: _Job, done: asyncio.Future[Any]
    ) -> None:
        """Deliver a finished job's result (caching it) or error, then start the next."""
        self._running -= 1
        if not done.cancelled():
            error = done.exception()
            if error is None:
                result = done.result()
                if job.key is not None:
                    self._remember(job.key, result)
                if not job.future.done():
                    job.future.set_result(result)
            elif not job.future.done():
                job.future.set_exception(error)
        self._pump(loop)

    def _remember(self, key: Hashable, result: Any) -> None:
        """Store ``result`` under ``key``, evicting the least recently used beyond the cap."""
        self._cache[key] = result
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
