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
  ``protagonist`` (one value column is the active-track id; singleton).

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
    #: One value column holds each video's active protagonist track id.
    PROTAGONIST = "protagonist"
    #: Rows are per-clip segments of a long video, reviewed (accept/drop) in the
    #: Annotator's Segment-review task. See :class:`SegmentationBand`.
    SEGMENTATION = "segmentation"

    @classmethod
    def _missing_(cls, value: object) -> CsvRole | None:
        """Resolve the pre-rename ``"main_character"`` role stored in older configs."""
        return cls.PROTAGONIST if value == "main_character" else None


@dataclass(slots=True, frozen=True)
class SegmentationBand:
    """A named start/end column pair defining one segmentation of a clip.

    A segmentation CSV may carry several competing segmentations of the same clip
    (e.g. a ground-truth span and a WhisperX forced-alignment span); each is one
    band, rendered side by side in the Segment-review task for comparison.

    Attributes:
        name: Human label shown beside the band's preview (e.g. ``"GT"``, ``"cut"``).
        start_column: CSV column holding the band's start time, in seconds.
        end_column: CSV column holding the band's end time, in seconds.
    """

    name: str
    start_column: str
    end_column: str


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
            or a single protagonist track-id column).
        column_types: For a CSV source, the chosen data type per value column
            (``"str"`` / ``"int"`` / ``"float"``); columns absent here default to str.
        segment_column: For a :attr:`CsvRole.SEGMENTATION` CSV, the column that tells
            apart the several rows sharing one ``key_column`` value: the two combine
            into the clip identity ``{video_id}_{segment_id}`` that each accept/drop is
            saved under. ``None`` for other roles.
        bands: For a :attr:`CsvRole.SEGMENTATION` CSV, the ordered start/end column
            pairs to render and compare (see :class:`SegmentationBand`). Empty
            otherwise.
    """

    kind: SourceKind
    path: Path
    role: CsvRole = CsvRole.LABELS
    key_column: str | None = None
    value_columns: tuple[str, ...] = ()
    column_types: dict[str, str] = field(default_factory=dict)
    segment_column: str | None = None
    bands: tuple[SegmentationBand, ...] = ()

    @property
    def is_folder(self) -> bool:
        """Whether this source points at a folder (vs a single CSV file)."""
        return self.kind in FOLDER_KINDS

    @property
    def is_protagonist(self) -> bool:
        """Whether this is the CSV that assigns each video's protagonist track."""
        return self.kind is SourceKind.CSV and self.role is CsvRole.PROTAGONIST

    @property
    def is_segmentation(self) -> bool:
        """Whether this CSV holds per-clip segments for the Segment-review task."""
        return self.kind is SourceKind.CSV and self.role is CsvRole.SEGMENTATION

    @property
    def available(self) -> bool:
        """Whether the source path currently exists (folder for folders, file for CSV)."""
        return self.path.is_dir() if self.is_folder else self.path.is_file()

    @property
    def label(self) -> str:
        """A short label for the Dataset source list."""
        if self.kind is SourceKind.CSV:
            if self.is_protagonist:
                return "Protagonist file"
            if self.is_segmentation:
                return "Segmentation file"
            return "Label file"
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

    Folder kinds (video/vdet/track) and the protagonist CSV are singletons:
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
        elif source.is_protagonist:
            self.sources = [s for s in self.sources if not s.is_protagonist]
        else:  # a labels or segmentation CSV: replace any existing source on the same file
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
    def protagonist(self) -> DataSource | None:
        """The protagonist CSV source, or ``None``."""
        return next((s for s in self.sources if s.is_protagonist), None)

    @property
    def label_sources(self) -> list[DataSource]:
        """All label CSV sources (excludes the protagonist and segmentation CSVs)."""
        return [s for s in self.sources if s.kind is SourceKind.CSV and s.role is CsvRole.LABELS]

    @property
    def segmentation_sources(self) -> list[DataSource]:
        """All segmentation CSV sources (drive the Segment-review task)."""
        return [s for s in self.sources if s.is_segmentation]

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


class TaskKind(StrEnum):
    """A supervision task the Annotator can offer, driven by the sources present."""

    #: Keep/drop plus notes on whole videos (needs only videos).
    CURATION = "curation"
    #: Correct each video's protagonist track (needs a protagonist CSV).
    PROTAGONIST = "protagonist"
    #: Accept/drop per-clip segments of long videos (needs a segmentation CSV).
    SEGMENT_REVIEW = "segment_review"


TASK_LABELS: dict[TaskKind, str] = {
    TaskKind.CURATION: "Curation",
    TaskKind.PROTAGONIST: "Protagonist review",
    TaskKind.SEGMENT_REVIEW: "Segment review",
}
"""Human label per task, for the Dataset task-wise sections and the Annotator switch."""


@dataclass(slots=True, frozen=True)
class TaskRequirement:
    """One prerequisite for a task, and whether the registry satisfies it.

    Attributes:
        label: Human name of the required source (e.g. ``"videos"``).
        present: Whether an available source satisfying it is configured.
    """

    label: str
    present: bool


@dataclass(slots=True, frozen=True)
class TaskReadiness:
    """Whether a task's sources are all present, with the per-requirement detail.

    Attributes:
        task: The :class:`TaskKind`.
        requirements: Each prerequisite and its present/absent state, in order.
    """

    task: TaskKind
    requirements: tuple[TaskRequirement, ...]

    @property
    def ready(self) -> bool:
        """Whether every requirement is satisfied (the Annotator may offer the task)."""
        return all(req.present for req in self.requirements)


def task_readiness(registry: SourceRegistry) -> list[TaskReadiness]:
    """Report, per task, which required sources are present in ``registry``.

    This is the single source of truth behind both the Dataset tab's task-wise
    readiness sections and the Annotator's decision of which tasks to offer.

    Args:
        registry: The configured sources.

    Returns:
        One :class:`TaskReadiness` per :class:`TaskKind`, requirements in order.
    """
    has_video = registry.has_video
    protagonist = registry.protagonist
    has_protagonist = protagonist is not None and protagonist.available
    has_segmentation = any(s.available for s in registry.segmentation_sources)
    video_req = TaskRequirement("videos", has_video)
    return [
        TaskReadiness(TaskKind.CURATION, (video_req,)),
        TaskReadiness(
            TaskKind.PROTAGONIST,
            (video_req, TaskRequirement("protagonist CSV", has_protagonist)),
        ),
        TaskReadiness(
            TaskKind.SEGMENT_REVIEW,
            (video_req, TaskRequirement("segmentation CSV", has_segmentation)),
        ),
    ]
