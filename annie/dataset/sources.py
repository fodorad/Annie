"""Generic, extensible data-source registry (domain).

Annie is dataset-agnostic: a dataset is described as an ordered list of
**data sources** the user adds on the Dataset tab, rather than four fixed
folders. Each source is one of:

* :attr:`SourceKind.VIDEO` — a folder of source videos (the mandatory spine; one
  Browse row per video). Singleton.
* :attr:`SourceKind.VDET` — a folder of raw-detection ``.vdet`` files. Singleton.
* :attr:`SourceKind.TRACK` — a folder of ``__track{N}.csv`` files. Singleton.
* :attr:`SourceKind.CSV` — a label/metadata CSV joined to videos by a chosen
  **key column**. A CSV plays one of two roles (:class:`CsvRole`): ``labels``
  (its selected value columns become Browse tags/filters; many allowed) or
  ``main_character`` (one value column is the active-track id; singleton).

This module is pure domain — no NiceGUI, no config, no torch. The registry is the
single source of truth the scanner (:mod:`annie.scanning`) consumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from annie.parsers.csvmeta import count_rows

if TYPE_CHECKING:
    from pathlib import Path


class SourceKind(StrEnum):
    """The kind of a data source."""

    VIDEO = "video"
    VDET = "vdet"
    TRACK = "track"
    CSV = "csv"


class CsvRole(StrEnum):
    """How a CSV source is interpreted."""

    #: Value columns become Browse tags and filter facets.
    LABELS = "labels"
    #: One value column holds each video's active main-character track id.
    MAIN_CHARACTER = "main_character"


FOLDER_KINDS: tuple[SourceKind, ...] = (SourceKind.VIDEO, SourceKind.VDET, SourceKind.TRACK)
"""Folder-backed kinds. Each is a **singleton** in a registry."""

VIDEO_SUFFIXES: tuple[str, ...] = (".mp4",)
"""Accepted source-video suffixes (lower-case, dotted)."""
VDET_SUFFIXES: tuple[str, ...] = (".vdet",)
"""Suffix identifying a raw-detection file."""
TRACK_GLOB = "*__track*.csv"
"""Glob identifying derived single-track files."""

KIND_LABELS: dict[SourceKind, str] = {
    SourceKind.VIDEO: "Videos folder",
    SourceKind.VDET: "Vdet folder",
    SourceKind.TRACK: "Track folder",
    SourceKind.CSV: "CSV file",
}
"""Short human label per kind, for the Dataset source list and the add menu."""

KIND_ICONS: dict[SourceKind, str] = {
    SourceKind.VIDEO: "movie",
    SourceKind.VDET: "center_focus_strong",
    SourceKind.TRACK: "timeline",
    SourceKind.CSV: "table_chart",
}
"""Tabler icon per kind for the Dataset cards."""


def _is_junk(name: str) -> bool:
    """Return whether a filename is OS junk (any dotfile: ``._*``, ``.DS_Store``)."""
    return name.startswith(".")


@dataclass(slots=True)
class DataSource:
    """One configured data source.

    Attributes:
        kind: The :class:`SourceKind`.
        path: Folder (for folder kinds) or file (for CSV) the source points at.
        role: For a CSV source, its :class:`CsvRole`; ignored otherwise.
        key_column: For a CSV source, the column joined to the video id.
        value_columns: For a CSV source, the selected value columns (label tags,
            or a single main-character track-id column).
        column_types: For a CSV source, the chosen data type per value column
            (``"str"`` / ``"int"`` / ``"float"``); columns absent here default to str.
    """

    kind: SourceKind
    path: Path
    role: CsvRole = CsvRole.LABELS
    key_column: str | None = None
    value_columns: tuple[str, ...] = ()
    column_types: dict[str, str] = field(default_factory=dict)

    @property
    def is_folder(self) -> bool:
        """Whether this source points at a folder (vs a single CSV file)."""
        return self.kind in FOLDER_KINDS

    @property
    def is_main_character(self) -> bool:
        """Whether this is the CSV that assigns each video's main-character track."""
        return self.kind is SourceKind.CSV and self.role is CsvRole.MAIN_CHARACTER

    @property
    def available(self) -> bool:
        """Whether the source path currently exists (folder for folders, file for CSV)."""
        return self.path.is_dir() if self.is_folder else self.path.is_file()

    @property
    def label(self) -> str:
        """A short label for the Dataset source list."""
        if self.kind is SourceKind.CSV:
            return "Main character file" if self.is_main_character else "Label file"
        return KIND_LABELS[self.kind]

    def count(self) -> int:
        """Count the items the source contributes (cheap; no matching).

        Returns:
            The number of videos / vdet / track files, or CSV data rows. ``0`` if
            the path is missing.
        """
        if not self.available:
            return 0
        if self.kind is SourceKind.VIDEO:
            return _count_suffixes(self.path, VIDEO_SUFFIXES)
        if self.kind is SourceKind.VDET:
            return _count_suffixes(self.path, VDET_SUFFIXES)
        if self.kind is SourceKind.TRACK:
            tracks = self.path.glob(TRACK_GLOB)
            return sum(1 for p in tracks if p.is_file() and not _is_junk(p.name))
        return count_rows(self.path)


def _count_suffixes(directory: Path, suffixes: tuple[str, ...]) -> int:
    """Count non-junk files in ``directory`` whose suffix is in ``suffixes``."""
    return sum(
        1
        for p in directory.iterdir()
        if p.is_file() and not _is_junk(p.name) and p.suffix.lower() in suffixes
    )


@dataclass(slots=True)
class SourceRegistry:
    """An ordered set of data sources with singleton rules.

    Folder kinds (video/vdet/track) and the main-character CSV are singletons:
    adding one replaces any existing source of that role. Label CSVs are
    unlimited, keyed by path (re-adding the same file replaces it).
    """

    sources: list[DataSource] = field(default_factory=list)

    def add(self, source: DataSource) -> None:
        """Add ``source``, enforcing the singleton rules in place of duplicates.

        Args:
            source: The source to add.
        """
        if source.kind in FOLDER_KINDS:
            self.sources = [s for s in self.sources if s.kind != source.kind]
        elif source.is_main_character:
            self.sources = [s for s in self.sources if not s.is_main_character]
        else:  # a labels CSV: replace any existing source on the same file
            self.sources = [s for s in self.sources if s.path != source.path]
        self.sources.append(source)

    def remove(self, source: DataSource) -> None:
        """Remove ``source`` if present (identity by path + kind)."""
        self.sources = [
            s for s in self.sources if not (s.path == source.path and s.kind == source.kind)
        ]

    def get(self, kind: SourceKind) -> DataSource | None:
        """Return the first source of ``kind``, or ``None``."""
        return next((s for s in self.sources if s.kind == kind), None)

    @property
    def video(self) -> DataSource | None:
        """The video-folder source, or ``None``."""
        return self.get(SourceKind.VIDEO)

    @property
    def vdet(self) -> DataSource | None:
        """The vdet-folder source, or ``None``."""
        return self.get(SourceKind.VDET)

    @property
    def track(self) -> DataSource | None:
        """The track-folder source, or ``None``."""
        return self.get(SourceKind.TRACK)

    @property
    def main_character(self) -> DataSource | None:
        """The main-character CSV source, or ``None``."""
        return next((s for s in self.sources if s.is_main_character), None)

    @property
    def label_sources(self) -> list[DataSource]:
        """All label CSV sources (excludes the main-character CSV)."""
        return [s for s in self.sources if s.kind is SourceKind.CSV and s.role is CsvRole.LABELS]

    @property
    def has_video(self) -> bool:
        """Whether a usable (existing) video folder is configured."""
        video = self.video
        return video is not None and video.available

    def available_kinds_to_add(self) -> list[SourceKind]:
        """Kinds offered by the ``+`` menu (singletons drop out once present)."""
        present = {s.kind for s in self.sources}
        offered = [k for k in FOLDER_KINDS if k not in present]
        offered.append(SourceKind.CSV)  # CSVs are always addable
        return offered
