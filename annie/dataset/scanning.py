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
* A label/protagonist table row matches a video by exact key-column value — or, for a
  label table, by a nested-layout path field (``match_field``) and optionally by
  substring (``match_substring``, see :func:`match_keys`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from annie.core.models import NO_ACTIVE_TRACK, RowStatus, VideoEntry
from annie.dataset.layout import contains_nested_videos
from annie.dataset.sources import (
    TRACK_GLOB,
    VDET_SUFFIXES,
    VIDEO_SUFFIXES,
    DataSource,
    SourceKind,
    SourceRegistry,
    _is_junk,
)
from annie.parsers.csvmeta import load_value_map, short_name
from annie.parsers.participants import DEFAULT_VALUE_COLUMN, resolved_mapping
from annie.parsers.track import track_id_from_name

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(slots=True)
class _LabelTable:
    """One loaded label table, ready to join.

    Attributes:
        source: The configured source.
        values: Key to ``{tag name: value}`` (winning row per key).
        origin: Provenance prefix, e.g. ``"intake.xlsx › visits · subject"``.
    """

    source: DataSource
    values: dict[str, dict[str, str]]
    origin: str


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
        layout_collisions: Video ids that more than one file produced under a nested
            layout (only the first file of each was kept).
        layout_error: Why a configured nested layout could not be used, else ``None``.
        nested_videos_hint: The flat video folder holds no videos itself but has some in
            sub-folders, so a nested layout is probably needed. Found during the scan so
            the Dataset tab never walks the folder while it redraws.
    """

    entries: list[VideoEntry] = field(default_factory=list)
    num_vdet_files: int = 0
    num_track_files: int = 0
    protagonist_available: bool = False
    label_columns: list[str] = field(default_factory=list)
    label_column_types: dict[str, str] = field(default_factory=dict)
    layout_collisions: list[str] = field(default_factory=list)
    layout_error: str | None = None
    nested_videos_hint: bool = False
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


def _label_tables(sources: list[DataSource]) -> tuple[list[_LabelTable], list[str]]:
    """Load each label table, values keyed by tag name; return ``(tables, ordered_tags)``."""
    tables: list[_LabelTable] = []
    tags: list[str] = []
    for source in sources:
        if source.key_column is None or not source.value_columns:
            continue
        raw = load_value_map(
            source.path,
            source.key_column,
            source.value_columns,
            sheet=source.sheet,
            order_column=source.order_column,
            descending=source.order_descending,
        )
        values = {
            key: {source.tag_name(col): value for col, value in row.items()}
            for key, row in raw.items()
        }
        where = f"{source.path.name} › {source.sheet}" if source.sheet else source.path.name
        tables.append(_LabelTable(source, values, f"{where} · {short_name(source.key_column)}"))
        for col in source.value_columns:
            if source.tag_name(col) not in tags:
                tags.append(source.tag_name(col))
    return tables, tags


def match_keys(
    keys: Iterable[str], targets: Iterable[str], *, substring: bool
) -> tuple[dict[str, str], set[str]]:
    """Link table keys to match targets (video ids or path-field values).

    Exact matching (the default) is a set lookup. With ``substring`` a target links to
    a key it *contains*: keys are grouped by length and each target is slid over once
    per distinct key length, so the cost stays near-linear rather than keys × targets.
    When a target contains several keys the **longest** wins; an equal-length tie goes
    to the key that comes first in ``keys`` (the table's winning-row order). Such
    targets are reported as ambiguous.

    Args:
        keys: The table's keys, in priority order.
        targets: The strings to link.
        substring: Match keys contained in a target instead of equal to it.

    Returns:
        ``(target -> key, ambiguous targets)``; unmatched targets are absent.
    """
    ordered = list(dict.fromkeys(keys))
    if not substring:
        known = set(ordered)
        return {t: t for t in targets if t in known}, set()
    rank = {key: index for index, key in enumerate(ordered)}
    by_length: dict[int, set[str]] = {}
    for key in ordered:
        by_length.setdefault(len(key), set()).add(key)
    lengths = sorted(by_length, reverse=True)
    matched: dict[str, str] = {}
    ambiguous: set[str] = set()
    for target in targets:
        found: list[str] = []
        for length in lengths:
            if length > len(target):
                continue
            windows = {target[i : i + length] for i in range(len(target) - length + 1)}
            hits = sorted(windows & by_length[length], key=rank.__getitem__)
            found.extend(hits)
            if len(found) > 1:
                break  # the winner is already fixed; one more hit proves ambiguity
        if found:
            matched[target] = found[0]
            if len(found) > 1:
                ambiguous.add(target)
    return matched, ambiguous


@dataclass(slots=True, frozen=True)
class JoinSummary:
    """How well a table's keys link to the videos (shown while configuring a table).

    Attributes:
        videos_matched: Videos that get a row.
        videos_total: All videos considered.
        keys_used: Distinct keys that link to at least one video.
        keys_total: Distinct non-empty keys in the table.
        ambiguous: Matched videos whose target contained more than one key.
    """

    videos_matched: int
    videos_total: int
    keys_used: int
    keys_total: int
    ambiguous: int


def join_summary(
    keys: Iterable[str], video_targets: dict[str, str], *, substring: bool
) -> JoinSummary:
    """Summarise a prospective join for the table dialog.

    Args:
        keys: The table's keys in priority order (see :func:`match_keys`).
        video_targets: Video id to the string its key must match (the id itself or a
            path-field value); an empty string means the video has no target.
        substring: Whether keys match by substring.

    Returns:
        A :class:`JoinSummary`.
    """
    distinct = [key for key in dict.fromkeys(keys) if key]
    matched, ambiguous = match_keys(
        distinct, {t for t in video_targets.values() if t}, substring=substring
    )
    linked = [vid for vid, target in video_targets.items() if target in matched]
    return JoinSummary(
        videos_matched=len(linked),
        videos_total=len(video_targets),
        keys_used=len({matched[video_targets[vid]] for vid in linked}),
        keys_total=len(distinct),
        ambiguous=sum(1 for vid in linked if video_targets[vid] in ambiguous),
    )


def _key_by_video(
    table: _LabelTable, video_ids: list[str], layout_fields: dict[str, dict[str, str]]
) -> dict[str, str]:
    """Map each video id to the table key it joins to (absent when none)."""
    field_name = table.source.match_field
    targets = {
        video_id: video_id
        if field_name is None
        else layout_fields.get(video_id, {}).get(field_name)
        for video_id in video_ids
    }
    matched, _ambiguous = match_keys(
        table.values,
        {t for t in targets.values() if t},
        substring=table.source.match_substring,
    )
    return {vid: matched[t] for vid, t in targets.items() if t and t in matched}


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
    layout_fields: dict[str, dict[str, str]] = {}
    layout_collisions: list[str] = []
    layout_error: str | None = None
    if video_src is not None and video_src.layout is not None and videos_path is not None:
        # A nested layout: ids and per-video fields come from the path template, not the stem.
        try:
            report = video_src.layout.discover(videos_path)
        except ValueError as exc:  # a bad saved layout must not break the whole scan
            layout_error = str(exc)
            videos = {}
        else:
            videos = {v.video_id: v.path for v in report.videos}
            layout_fields = {v.video_id: v.fields for v in report.videos}
            layout_collisions = report.collisions
    else:
        videos = {p.stem: p for p in _iter_files(videos_path, suffixes=VIDEO_SUFFIXES, glob=None)}
    nested_videos_hint = (
        video_src is not None
        and video_src.layout is None
        and videos_path is not None
        and not videos
        and contains_nested_videos(videos_path, VIDEO_SUFFIXES)
    )
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
    label_tables, label_columns = _label_tables(registry.label_sources)
    for fields in layout_fields.values():  # path-derived fields are tags/filters too
        for name in fields:
            if name not in label_columns:
                label_columns.append(name)
    label_column_types: dict[str, str] = {}
    for source in registry.label_sources:
        for column in source.value_columns:
            label_column_types[source.tag_name(column)] = source.column_types.get(column, "str")

    video_ids = sorted(set(videos) | set(vdets) | set(tracks))
    joins = [(table, _key_by_video(table, video_ids, layout_fields)) for table in label_tables]
    entries: list[VideoEntry] = []
    # Numbered over the full sorted manifest, before any filtering, so a row's id
    # identifies the sample rather than its position in whatever list is on screen.
    for row_id, video_id in enumerate(video_ids, start=1):
        video_tracks = sorted(tracks.get(video_id, []), key=lambda item: item[0])
        track_ids = [tid for tid, _ in video_tracks]
        track_paths = [p for _, p in video_tracks]
        vdet_matches = vdets.get(video_id)
        vdet_file = _primary_vdet(video_id, vdet_matches) if vdet_matches else None
        labels: dict[str, str] = dict(layout_fields.get(video_id, {}))
        origins = {name: f"path field {{{name}}}" for name in labels}
        for table, key_of in joins:
            key = key_of.get(video_id)
            if key is None:
                continue
            labels.update(table.values[key])
            origins.update({tag: f"{table.origin} = {key}" for tag in table.values[key]})
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
                label_origins=origins,
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
        layout_collisions=layout_collisions,
        layout_error=layout_error,
        nested_videos_hint=nested_videos_hint,
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
