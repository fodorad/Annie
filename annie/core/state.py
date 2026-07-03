"""Process-wide UI state shared across the NiceGUI tabs (service-facing glue).

Holds the configured :class:`~annie.sources.SourceRegistry`, the cached scan
manifest (Browse/Annotator are pure consumers of it), the review store, the render
service, and session-only UI settings (row heights). Adding or removing a source
re-runs the scan in place, so there is no Scan button.

Sources are **session-only**: the registry is seeded from the ``ANNIE_*``
environment variables on startup and otherwise lives for the process lifetime.
Curation (verdicts, notes, annotator selection) and main-character corrections
persist independently in SQLite / the ``_manual`` CSV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from annie.core.config import settings
from annie.dataset.filtering import ReviewState
from annie.dataset.manipulate import detect_type
from annie.dataset.scanning import ScanResult, build_manifest
from annie.dataset.sources import CsvRole, DataSource, SourceKind, SourceRegistry
from annie.dataset.storage import ReviewStore
from annie.media.convert import ConversionRunner
from annie.media.rendering import RenderService
from annie.parsers.csvmeta import read_header, read_rows
from annie.parsers.participants import DEFAULT_KEY_COLUMN, DEFAULT_VALUE_COLUMN


@dataclass(slots=True)
class UiSettings:
    """Session-only UI preferences set on the Settings tab.

    Attributes:
        browse_row_height: Media height (px) of a Browse row's thumbnail/strip/render.
        annotator_row_height: Max media height (px) of an Annotator row; its boxes
            flex to share the row width and only hit this cap on very wide screens.
    """

    browse_row_height: int = 135
    annotator_row_height: int = 200


def _seed_registry() -> SourceRegistry:
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
                role=CsvRole.MAIN_CHARACTER,
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
    """

    registry: SourceRegistry = field(default_factory=_seed_registry)
    scan: ScanResult | None = None
    store: ReviewStore = field(default_factory=lambda: ReviewStore(settings.db_path))
    renderer: RenderService = field(default_factory=RenderService)
    converter: ConversionRunner = field(default_factory=ConversionRunner)
    audio_cache: dict[str, bool] = field(default_factory=dict)
    frames_cache: dict[str, int] = field(default_factory=dict)
    ui: UiSettings = field(default_factory=UiSettings)

    def rescan(self) -> None:
        """Rebuild the manifest from the current registry (after a source change).

        Also clears the audio and frame-count caches so that any replaced or
        renamed video files are re-probed on the next Browse render.
        """
        self.audio_cache.clear()
        self.frames_cache.clear()
        self.scan = build_manifest(self.registry)

    def set_store(self, db_path: Path) -> None:
        """Replace the active review store with one backed by ``db_path``.

        The file is created (with the schema applied) if it does not exist yet.
        Call this whenever the user changes the persistence target — either by
        loading a config that embeds a ``"db"`` path, or by choosing a DB file
        in the Persistence block on the Dataset tab.

        Args:
            db_path: Absolute path to the SQLite file to open.
        """
        self.store = ReviewStore(db_path)

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
        return ReviewState(
            verdict=record.verdict or "good",
            note=record.note,
            in_annotator=record.annotate,
        )


state = AppState()
"""The single shared state instance.

The initial scan is deferred to app startup (see :mod:`annie.app`) so imports
stay fast.
"""
