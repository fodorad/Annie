"""The composition root: the one place that wires the backend together for the UI.

It sits above every package: it builds the source registry, the scan, the review store
and the background services (rendering, conversion, previews) from :mod:`annie.dataset`,
:mod:`annie.media` and :mod:`annie.parsers`, and the UI (:mod:`annie.pages`, ``app.py``)
reaches them through :data:`state`. Nothing below it imports it.

Holds the configured :class:`~annie.sources.SourceRegistry`, the cached scan
manifest (Browse/Annotator are pure consumers of it), the review store, the render
service, and session-only UI settings (row heights). Adding or removing a source
re-runs the scan in place, so there is no Scan button.

Sources are **session-only**: the registry is seeded from the ``ANNIE_*``
environment variables on startup and otherwise lives for the process lifetime.
Curation (verdicts, notes, annotator selection) and protagonist corrections
persist independently in SQLite / the ``_manual`` CSV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from annie.core import logbook
from annie.core.config import settings
from annie.dataset import datasets
from annie.dataset.filtering import ReviewState
from annie.dataset.manipulate import detect_type
from annie.dataset.scanning import ScanResult, build_manifest
from annie.dataset.sources import CsvRole, DataSource, SourceKind, SourceRegistry
from annie.dataset.storage import ReviewRecord, ReviewStore
from annie.media.convert import ConversionRunner
from annie.media.previews import PreviewLoader
from annie.media.rendering import RenderService
from annie.parsers.csvmeta import read_header, read_rows
from annie.parsers.participants import DEFAULT_KEY_COLUMN, DEFAULT_VALUE_COLUMN

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(slots=True)
class UiSettings:
    """Session-only UI preferences set on the Settings tab.

    Attributes:
        browse_row_height: Media height (px) of a Browse row's thumbnail/strip/render.
        annotator_row_height: Max media height (px) of an Annotator row; its boxes
            flex to share the row width and only hit this cap on very wide screens.
        auto_scroll: Whether reaching the bottom of Browse / Annotator reveals the
            next page automatically, instead of waiting for a "Show more" click.
        page_size: How many rows one page reveals, whether auto-scrolled or clicked.
        embed_ttl_seconds: How long an embedded clip stays on a row before it
            reverts to its placeholder, freeing the memory the browser holds for it.
        unload_after_seconds: How long a row may sit off screen before its preview
            frames are dropped; they are decoded again when it scrolls back.
        grid_thumb_height: Media height (px) of a Browse **grid** box (Quick
            selection view); its width is derived 16:9.
        grid_page_size: How many grid boxes one page reveals — larger than
            ``page_size`` because a grid box is a single cheap frame.
        grid_unload_after_seconds: Off-screen TTL for grid boxes; shorter than
            ``unload_after_seconds`` since the grid packs many videos on screen.
    """

    browse_row_height: int = 110
    annotator_row_height: int = 200
    auto_scroll: bool = True
    page_size: int = 10
    embed_ttl_seconds: int = 60
    unload_after_seconds: int = 20
    grid_thumb_height: int = 90
    grid_page_size: int = 60
    grid_unload_after_seconds: float = 5.0


def _review_state(record: ReviewRecord) -> ReviewState:
    """The filter-facing state of a stored review record (no verdict reads as liked)."""
    return ReviewState(
        verdict=record.verdict or "good",
        note=record.note,
        in_annotator=record.annotate,
    )


def seed_registry() -> SourceRegistry:
    """Build the initial registry from the ``ANNIE_*`` environment variables."""
    registry = SourceRegistry()
    if settings.videos_dir is not None:
        registry.add(DataSource(SourceKind.VIDEO, Path(settings.videos_dir)))
    if settings.vdet_dir is not None:
        registry.add(DataSource(SourceKind.VDET, Path(settings.vdet_dir)))
    if settings.track_dir is not None:
        registry.add(DataSource(SourceKind.TRACK, Path(settings.track_dir)))
    if settings.participants_file is not None:
        registry.add(
            DataSource(
                SourceKind.CSV,
                Path(settings.participants_file),
                role=CsvRole.PROTAGONIST,
                key_column=DEFAULT_KEY_COLUMN,
                value_columns=(DEFAULT_VALUE_COLUMN,),
            )
        )
    if settings.labels_csv is not None:
        csv_path = Path(settings.labels_csv)
        header = read_header(csv_path)
        key = settings.labels_key or (header[0] if header else None)
        if settings.labels_values is not None:
            values = settings.labels_values
        else:
            values = tuple(col for col in header if col != key)
        rows = read_rows(csv_path)[:500]
        column_types: dict[str, str] = {
            col: detect_type(row.get(col, "") for row in rows) for col in values
        }
        registry.add(
            DataSource(
                SourceKind.CSV,
                csv_path,
                role=CsvRole.LABELS,
                key_column=key,
                value_columns=values,
                column_types=column_types,
            )
        )
    return registry


@dataclass(slots=True)
class AppState:
    """Mutable application state shared between tabs.

    Attributes:
        registry: The configured data sources (the dataset definition).
        scan: The most recent scan result, recomputed on every source change.
        store: The SQLite review-status store.
        renderer: The render-job service.
        converter: The audio/video re-encode batch runner.
        audio_cache: Probed audio-stream presence per video id (lazy; see Browse).
        frames_cache: Decoded frame count per video id (lazy; see Browse).
        ui: Session-only UI preferences.
        active_config: The dataset config file currently loaded, or ``None`` for the
            env-seeded / blank dataset. Lets the Dataset tab show it as selected.
        scanned_tracks: Each video's protagonist as scanned (the heuristic CSV), before
            the review DB's corrections are layered over it; lets a DB switch start over.
        previews: The shared preview loader (bounded, newest-first, cached decoding).
        scanning: Whether a scan is running right now (start-up scan included), so every
            page can show it as busy.
    """

    registry: SourceRegistry = field(default_factory=seed_registry)
    scan: ScanResult | None = None
    store: ReviewStore = field(default_factory=lambda: ReviewStore(settings.db_path))
    renderer: RenderService = field(default_factory=RenderService)
    converter: ConversionRunner = field(default_factory=ConversionRunner)
    audio_cache: dict[str, bool] = field(default_factory=dict)
    frames_cache: dict[str, int] = field(default_factory=dict)
    ui: UiSettings = field(default_factory=UiSettings)
    active_config: Path | None = None
    scanned_tracks: dict[str, int] = field(default_factory=dict)
    scanning: bool = False
    previews: PreviewLoader = field(
        default_factory=lambda: PreviewLoader(settings.preview_max_workers)
    )

    def restore_last_config(self) -> Path | None:
        """Reopen the config remembered from the previous run, with its review database.

        This is what lets a user close Annie and, on the next start, land straight back
        in their dataset with all their progress — no need to re-pick the config. A
        config that has gone missing or fails to load is skipped (and logged), leaving
        the current dataset in place.

        Returns:
            The config path that was restored, or ``None`` if nothing was.
        """
        path = datasets.last_config()
        if path is None:
            return None
        try:
            _name, registry, db_path = datasets.load_config(path)
        except Exception as exc:  # noqa: BLE001 - a bad config must never block startup
            logbook.report_exception(f"Could not reopen the last config: {path}", exc)
            return None
        self.registry = registry
        if db_path is not None:
            self.set_store(db_path)
        self.active_config = path
        return path

    def rescan(self) -> None:
        """Rebuild the manifest from the current registry (after a source change).

        Also clears the audio and frame-count caches so that any replaced or
        renamed video files are re-probed on the next Browse render.
        """
        self.audio_cache.clear()
        self.frames_cache.clear()
        self.previews.clear()
        self.scanning = True
        try:
            self.scan = build_manifest(self.registry)
        finally:
            self.scanning = False
        self.scanned_tracks = {e.key: e.active_track_id for e in self.scan.entries}
        self._apply_active_track_overrides()

    def _apply_active_track_overrides(self) -> None:
        """Layer this session's protagonist corrections over the scanned heuristic.

        Manual track choices are stored in the review DB (not the source CSV), so a
        corrected video shows its chosen protagonist after any re-scan or restart on
        the same database, while uncorrected videos keep their heuristic value.

        Every video starts from its scanned value first, so after a switch to another
        DB the previous DB's corrections do not linger on videos the new one leaves alone.
        """
        if self.scan is None:
            return
        overrides = self.store.active_tracks()
        for entry in self.scan.entries:
            scanned = self.scanned_tracks.get(entry.key, entry.active_track_id)
            entry.active_track_id = overrides.get(entry.key, scanned)

    def set_store(self, db_path: Path) -> None:
        """Replace the active review store with one backed by ``db_path``.

        The file is created (with the schema applied) if it does not exist yet.
        Call this whenever the user changes the persistence target — either by
        loading a config that embeds a ``"db"`` path, or by choosing a DB file
        in the Persistence block on the Dataset tab.

        The new DB's protagonist corrections replace the previous DB's on the scanned
        videos straight away, instead of only after the next rescan or restart.

        Args:
            db_path: Absolute path to the SQLite file to open.
        """
        self.store = ReviewStore(db_path)
        # The session log is paired with its DB — keep their names in lockstep.
        logbook.LOG.retarget(db_path)
        self._apply_active_track_overrides()

    def review_lookup(self) -> Callable[[str], ReviewState]:
        """Read every review state in one query and return a per-key lookup.

        For a pass over many videos (e.g. the Browse filter); the lookup answers exactly
        like :meth:`review_state` but from a snapshot taken now.

        Returns:
            A function mapping a video key to its :class:`~annie.filtering.ReviewState`.
        """
        records = self.store.records_by_key()
        default = ReviewState()

        def lookup(key: str) -> ReviewState:
            record = records.get(key)
            if record is None:
                return default
            return _review_state(record)

        return lookup

    def review_state(self, key: str) -> ReviewState:
        """Return the filtering-facing review state for a video (default liked).

        Args:
            key: The video key.

        Returns:
            A :class:`~annie.filtering.ReviewState`; videos with no stored row are
            liked (good) with no note and not queued for the Annotator.
        """
        record = self.store.get(key)
        if record is None:
            return ReviewState()
        return _review_state(record)


state = AppState()
"""The single shared state instance.

The initial scan is deferred to app startup (see :mod:`annie.app`) so imports
stay fast.
"""
