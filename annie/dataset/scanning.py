"""Dataset scanning: aggregate each video with its annotations into the manifest.

The scanner turns a :class:`~annie.sources.SourceRegistry` into **one row per
video** (a :class:`~annie.models.VideoEntry`) carrying that video's raw detections,
all of its tracks, its resolved protagonist track, and any label values from
CSV label sources. Browse consumes the sorted manifest; the Dataset tab shows the
overview counts; the Browse filter bar is built from the discovered label columns.

Because there is no Scan button any more, this runs whenever the set of sources
changes (add/remove on the Dataset tab). It stays linear in the number of files:

* A video is matched by its **stem** (filename minus extension).
* A ``.vdet`` matches by exact stem; a ``__track{N}.csv`` matches by the part
  before ``__track``. Both fall back to a longest-stem-first prefix scan only on
  a miss.
* A label/protagonist CSV row matches a video by exact key-column value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from annie.core.models import NO_ACTIVE_TRACK, RowStatus, VideoEntry
from annie.dataset.sources import (
    TRACK_GLOB,
    VDET_SUFFIXES,
    VIDEO_SUFFIXES,
    DataSource,
    SourceKind,
    SourceRegistry,
    _is_junk,
)
from annie.parsers.csvmeta import load_value_map
from annie.parsers.participants import DEFAULT_VALUE_COLUMN, resolved_mapping
from annie.parsers.track import track_id_from_name

#: A label source's value map: ``video_id -> {column: value}``.
_LabelMap = dict[str, dict[str, str]]


def _iter_files(
    directory: Path | None, *, suffixes: tuple[str, ...] | None, glob: str | None
) -> list[Path]:
    """List non-junk files in ``directory`` matching suffixes or a glob.

    Args:
        directory: Folder to scan (non-recursive), or ``None``.
        suffixes: Lower-case dotted suffixes to accept, or ``None`` to use ``glob``.
        glob: Glob pattern to match, or ``None`` to use ``suffixes``.

    Returns:
        Matching file paths, junk excluded. Empty if the folder is missing/None.
    """
    if directory is None or not directory.is_dir():
        return []
    candidates = directory.glob(glob) if glob is not None else directory.iterdir()
    out: list[Path] = []
    for path in candidates:
        if not path.is_file() or _is_junk(path.name):
            continue
        if suffixes is not None and path.suffix.lower() not in suffixes:
            continue
        out.append(path)
    return out


@dataclass(slots=True)
class ScanResult:
    """The outcome of a dataset scan.

    Attributes:
        entries: The sorted, per-video manifest the Browse tab renders.
        num_vdet_files: Total vdet files discovered.
        num_track_files: Total track files discovered.
        protagonist_available: Whether protagonist assignments were found.
        label_columns: The label-column names available for tags/filters.
    """

    entries: list[VideoEntry] = field(default_factory=list)
    num_vdet_files: int = 0
    num_track_files: int = 0
    protagonist_available: bool = False
    label_columns: list[str] = field(default_factory=list)
    label_column_types: dict[str, str] = field(default_factory=dict)
    #: Lazily-built ``video_id -> entry`` index; see :attr:`by_video_id`.
    _by_video_id: dict[str, VideoEntry] | None = field(default=None, repr=False, compare=False)

    @property
    def by_video_id(self) -> dict[str, VideoEntry]:
        """The manifest indexed by ``video_id``, built once on first access.

        Callers that resolve a video by id inside a re-rendered UI (the Segment-review
        task does it on every keystroke) would otherwise walk the whole manifest per
        lookup. Where two entries share a ``video_id`` — the same video with different
        annotation suffixes — the first in manifest order wins, matching the behaviour of
        the linear ``next(...)`` search this replaced.

        Returns:
            A mapping of video id to its first :class:`~annie.core.models.VideoEntry`.
        """
        if self._by_video_id is None:
            index: dict[str, VideoEntry] = {}
            for entry in self.entries:
                index.setdefault(entry.video_id, entry)
            self._by_video_id = index
        return self._by_video_id

    @property
    def counts(self) -> dict[str, int | bool]:
        """Overview metrics for the Dataset cards.

        Returns:
            Counts keyed by ``num_videos``, ``num_vdet_files``, ``num_track_files``,
            ``videos_vdet_and_track``, ``videos_with_vdet``, ``videos_with_track``,
            and ``protagonist_available`` (bool).
        """
        num_videos = sum(1 for entry in self.entries if entry.has_video)
        return {
            "num_videos": num_videos,
            "num_vdet_files": self.num_vdet_files,
            "num_track_files": self.num_track_files,
            "videos_vdet_and_track": sum(
                1 for e in self.entries if e.has_video and e.has_vdet and e.has_track
            ),
            "videos_with_vdet": sum(1 for e in self.entries if e.has_video and e.has_vdet),
            "videos_with_track": sum(1 for e in self.entries if e.has_video and e.has_track),
            "protagonist_available": self.protagonist_available,
        }

    def label_values(self, column: str) -> list[str]:
        """Return the sorted distinct values a label column takes across entries.

        Args:
            column: The label column name.

        Returns:
            Sorted distinct non-empty values for that column.
        """
        seen = {e.labels[column] for e in self.entries if e.labels.get(column)}
        return sorted(seen)


def resolve_video_stem(annotation_stem: str, video_stems_longest_first: list[str]) -> str | None:
    """Find the video stem an annotation belongs to, longest-stem-first.

    A video stem ``s`` matches when the annotation stem equals ``s`` exactly or
    begins with ``s`` followed by an underscore separator. Checking longest-first
    ensures ``X2`` wins over ``X`` for ``X2__track0``.

    Args:
        annotation_stem: The annotation filename minus extension.
        video_stems_longest_first: Known video stems, pre-sorted longest-first.

    Returns:
        The matching video stem, or ``None`` if no video matches.
    """
    for stem in video_stems_longest_first:
        if annotation_stem == stem or annotation_stem.startswith(stem + "_"):
            return stem
    return None


def _resolve_fast(
    candidate: str, stem: str, video_stems: set[str], video_stems_longest_first: list[str]
) -> str:
    """Resolve to a video stem, exact-first (O(1)) then longest-prefix fallback."""
    if candidate in video_stems:
        return candidate
    return resolve_video_stem(stem, video_stems_longest_first) or candidate


def _primary_vdet(video_id: str, paths: list[Path]) -> Path:
    """Pick the single ``.vdet`` for a video when several resolve to the same id.

    A video carries one raw-detection file, but distinct stems can resolve to the
    same ``video_id`` (an exact match plus an underscore-prefix match, say). The
    filesystem iteration order is arbitrary, so choose deterministically: prefer
    the exact-stem file, then break ties alphabetically.

    Args:
        video_id: The resolved video stem the files matched.
        paths: The one-or-more ``.vdet`` paths that resolved to ``video_id``.

    Returns:
        The chosen ``.vdet`` path.
    """
    exact = sorted(p for p in paths if p.stem == video_id)
    return exact[0] if exact else sorted(paths)[0]


def _status_for(*, has_video: bool, has_annotation: bool) -> RowStatus:
    """Map presence flags to the tri-state row status (kept for internal grouping).

    Args:
        has_video: Whether the source video exists.
        has_annotation: Whether any annotation (vdet or track) exists.

    Returns:
        ``"linked"`` (video + annotation), ``"video_only"`` (video, no annotation),
        or ``"annotation_only"`` (annotation, no video).
    """
    if has_video and has_annotation:
        return "linked"
    if has_video:
        return "video_only"
    return "annotation_only"


def _active_mapping(source: DataSource | None) -> dict[str, int]:
    """Resolve the protagonist ``video_id -> track_id`` map from its source."""
    if source is None or source.key_column is None or not source.value_columns:
        return {}
    return resolved_mapping(source.path, source.key_column, source.value_columns[0])


def _label_maps(sources: list[DataSource]) -> tuple[list[_LabelMap], list[str]]:
    """Load each label source's value map; return ``(maps, ordered_columns)``."""
    maps: list[_LabelMap] = []
    columns: list[str] = []
    for source in sources:
        if source.key_column is None or not source.value_columns:
            continue
        value_map = load_value_map(source.path, source.key_column, source.value_columns)
        maps.append(value_map)
        for col in source.value_columns:
            if col not in columns:
                columns.append(col)
    return maps, columns


def build_manifest(registry: SourceRegistry) -> ScanResult:
    """Scan all sources in ``registry`` into a per-video manifest.

    The video source is the spine: with none configured the manifest is empty.
    Every other source attaches to a video by stem (vdet/track) or key column
    (label/protagonist CSV).

    Args:
        registry: The configured data sources.

    Returns:
        A :class:`ScanResult` with the per-video manifest, overview counts, and the
        discovered label columns. Missing/unset sources contribute nothing rather
        than raising.
    """
    video_src = registry.video
    vdet_src = registry.vdet
    track_src = registry.track

    videos_path = video_src.path if video_src is not None else None
    videos = {p.stem: p for p in _iter_files(videos_path, suffixes=VIDEO_SUFFIXES, glob=None)}
    video_stems = set(videos)
    longest_first = sorted(videos, key=len, reverse=True)

    vdet_path = vdet_src.path if vdet_src is not None else None
    vdets: dict[str, list[Path]] = {}
    num_vdet_files = 0
    for path in _iter_files(vdet_path, suffixes=VDET_SUFFIXES, glob=None):
        stem = path.stem
        video_id = _resolve_fast(stem, stem, video_stems, longest_first)
        vdets.setdefault(video_id, []).append(path)
        num_vdet_files += 1

    track_path = track_src.path if track_src is not None else None
    tracks: dict[str, list[tuple[int, Path]]] = {}
    num_track_files = 0
    for path in _iter_files(track_path, suffixes=None, glob=TRACK_GLOB):
        stem = path.stem
        base = stem.split("__track", 1)[0]
        video_id = _resolve_fast(base, stem, video_stems, longest_first)
        try:
            track_id = track_id_from_name(path)
        except ValueError:
            continue
        tracks.setdefault(video_id, []).append((track_id, path))
        num_track_files += 1

    active = _active_mapping(registry.protagonist)
    label_maps, label_columns = _label_maps(registry.label_sources)
    label_column_types: dict[str, str] = {}
    for source in registry.label_sources:
        for column in source.value_columns:
            label_column_types[column] = source.column_types.get(column, "str")

    entries: list[VideoEntry] = []
    # Numbered over the full sorted manifest, before any filtering, so a row's id
    # identifies the sample rather than its position in whatever list is on screen.
    for row_id, video_id in enumerate(sorted(set(videos) | set(vdets) | set(tracks)), start=1):
        video_tracks = sorted(tracks.get(video_id, []), key=lambda item: item[0])
        track_ids = [tid for tid, _ in video_tracks]
        track_paths = [p for _, p in video_tracks]
        vdet_matches = vdets.get(video_id)
        vdet_file = _primary_vdet(video_id, vdet_matches) if vdet_matches else None
        labels: dict[str, str] = {}
        for value_map in label_maps:
            if video_id in value_map:
                labels.update(value_map[video_id])
        entries.append(
            VideoEntry(
                video_id=video_id,
                video_path=videos.get(video_id),
                vdet_path=vdet_file,
                track_paths=track_paths,
                track_ids=track_ids,
                active_track_id=active.get(video_id, NO_ACTIVE_TRACK),
                status=_status_for(
                    has_video=video_id in videos,
                    has_annotation=vdet_file is not None or bool(track_paths),
                ),
                labels=labels,
                row_id=row_id,
            )
        )

    return ScanResult(
        entries=entries,
        num_vdet_files=num_vdet_files,
        num_track_files=num_track_files,
        protagonist_available=bool(active),
        label_columns=label_columns,
        label_column_types=label_column_types,
    )


def scan_dataset(
    videos_dir: str | Path | None = None,
    vdet_dir: str | Path | None = None,
    track_dir: str | Path | None = None,
    participants_file: str | Path | None = None,
) -> ScanResult:
    """Scan the classic four MOSEI paths into a per-video manifest (convenience).

    A thin wrapper that assembles a :class:`~annie.sources.SourceRegistry` from the
    given folders and protagonist file (with the default ``uuid``/``track_id``
    columns) and delegates to :func:`build_manifest`.

    Args:
        videos_dir: Folder of source videos.
        vdet_dir: Folder of ``.vdet`` files.
        track_dir: Folder of ``__track{N}.csv`` files.
        participants_file: Protagonist heuristic CSV (``uuid,track_id``).

    Returns:
        A :class:`ScanResult` with the per-video manifest and overview counts.
    """
    registry = SourceRegistry()
    if videos_dir is not None:
        registry.add(DataSource(SourceKind.VIDEO, Path(videos_dir)))
    if vdet_dir is not None:
        registry.add(DataSource(SourceKind.VDET, Path(vdet_dir)))
    if track_dir is not None:
        registry.add(DataSource(SourceKind.TRACK, Path(track_dir)))
    if participants_file is not None:
        from annie.dataset.sources import CsvRole
        from annie.parsers.participants import DEFAULT_KEY_COLUMN

        registry.add(
            DataSource(
                SourceKind.CSV,
                Path(participants_file),
                role=CsvRole.PROTAGONIST,
                key_column=DEFAULT_KEY_COLUMN,
                value_columns=(DEFAULT_VALUE_COLUMN,),
            )
        )
    return build_manifest(registry)
