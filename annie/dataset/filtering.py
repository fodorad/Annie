"""Browse filtering (service): pure predicates over the manifest.

The Browse tab's always-visible filter bar narrows the per-video manifest by
annotation coverage, review verdict, notes, annotator selection, label values, and
an explicit id list loaded from a CSV column. The logic lives here — independent of
NiceGUI and SQLite — so it is unit-tested directly: the UI supplies a
:class:`ReviewState` per row (read from the store) and a :class:`FilterSpec` built
from the controls.

Facets combine with **AND**; within a single label column the selected values
combine with **OR** (e.g. Sentiment in {negative, neutral}).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

    from annie.core.models import VideoEntry

PresenceFilter = Literal["any", "has", "missing"]
"""Tri-state presence facet (vdet, video frames, audio stream)."""
VdetFilter = PresenceFilter
"""Backwards-compatible alias for the vdet facet."""
TrackFilter = Literal["any", "none", "one", "multi"]
"""Track-count facet: any / none (0) / exactly one / multiple (2+)."""
ReviewFilter = Literal["any", "liked", "disliked"]
"""Review facet: any / liked (good, the default) / disliked (bad)."""
FramesFilter = Literal["any", "lt", "gt"]
"""Frame-count facet: any / less-than-threshold / greater-than-threshold."""


@dataclass(slots=True)
class ReviewState:
    """The per-video review state a filter needs, defaulted to "liked".

    Every video is **liked (good) by default**; a record only exists once the user
    interacts. Attributes mirror the persisted columns.

    Attributes:
        verdict: ``"good"`` (liked, default) or ``"bad"`` (disliked).
        note: Free-text note (empty when none).
        in_annotator: Whether the video is queued for the Annotator tab.
    """

    verdict: Literal["good", "bad"] = "good"
    note: str = ""
    in_annotator: bool = False


@dataclass(slots=True)
class FilterSpec:
    """A snapshot of the Browse filter bar.

    Attributes:
        name_prefix: keep only videos whose id starts with this (case-insensitive).
        video: video-frames presence facet.
        audio: audio-stream presence facet (uses the probed cache; see ``audio_of``).
        vdet: vdet-presence facet.
        tracks: track-count facet.
        frames: frame-count facet (uses the probed cache; see ``frames_of``).
        frames_threshold: the threshold for the ``frames`` facet.
        review: review-verdict facet.
        has_note: keep only videos with a non-empty note.
        in_annotator: keep only videos queued for the Annotator.
        labels: per-column allowed value sets (OR within a column, AND across).
        id_list: keep only videos whose id is in this set — the ids read from a CSV
            column (see :func:`~annie.parsers.csvmeta.distinct_column_values`).
            ``None`` means the facet is off; an empty set is never set by the UI.
        id_source: the name of the CSV the ``id_list`` came from (for display).
        id_column: the CSV column the ids were read from (for display).
    """

    name_prefix: str = ""
    video: PresenceFilter = "any"
    audio: PresenceFilter = "any"
    vdet: VdetFilter = "any"
    tracks: TrackFilter = "any"
    frames: FramesFilter = "any"
    frames_threshold: int = 0
    review: ReviewFilter = "any"
    has_note: bool = False
    in_annotator: bool = False
    labels: dict[str, set[str]] = field(default_factory=dict)
    id_list: set[str] | None = None
    id_source: str = ""
    id_column: str = ""

    @property
    def is_active(self) -> bool:
        """Whether any facet is set (so the UI can show a "clear" affordance)."""
        return (
            bool(self.name_prefix.strip())
            or self.video != "any"
            or self.audio != "any"
            or self.vdet != "any"
            or self.tracks != "any"
            or self.frames != "any"
            or self.review != "any"
            or self.has_note
            or self.in_annotator
            or any(self.labels.values())
            or self.id_list is not None
        )


def _presence_ok(present: bool | None, facet: PresenceFilter) -> bool:
    """Whether a present/absent/unknown flag satisfies a presence facet.

    ``None`` means "not yet known" (e.g. audio not probed); it only passes ``any``.
    """
    if facet == "has":
        return present is True
    if facet == "missing":
        return present is False
    return True


def _tracks_ok(n: int, facet: TrackFilter) -> bool:
    """Whether a video's track count satisfies the track facet."""
    if facet == "none":
        return n == 0
    if facet == "one":
        return n == 1
    if facet == "multi":
        return n >= 2
    return True


def _frames_ok(num_frames: int | None, facet: FramesFilter, threshold: int) -> bool:
    """Whether a video's frame count satisfies the frame-count facet.

    ``None`` (not yet decoded) only passes ``any``.
    """
    if facet == "lt":
        return num_frames is not None and num_frames < threshold
    if facet == "gt":
        return num_frames is not None and num_frames > threshold
    return True


def _review_ok(verdict: str, facet: ReviewFilter) -> bool:
    """Whether a video's verdict satisfies the review facet."""
    if facet == "liked":
        return verdict == "good"
    if facet == "disliked":
        return verdict == "bad"
    return True


def matches(
    entry,  # noqa: ANN001 - VideoEntry
    review: ReviewState,
    spec: FilterSpec,
    *,
    has_audio: bool | None = None,
    num_frames: int | None = None,
    label_of: Callable[[VideoEntry, str], str | None] | None = None,
) -> bool:
    """Return whether a single video passes every active facet.

    Args:
        entry: The :class:`~annie.models.VideoEntry` under test.
        review: Its :class:`ReviewState`.
        spec: The current filter snapshot.
        has_audio: The probed audio-presence flag (``None`` if not yet known).
        num_frames: The decoded frame count (``None`` if not yet known).
        label_of: Maps ``(entry, column)`` to the (possibly transformed) label value;
            defaults to the entry's raw label.

    Returns:
        ``True`` if the video matches the filter, ``False`` otherwise.
    """
    get_label = label_of or (lambda e, column: e.labels.get(column))
    prefix = spec.name_prefix.strip().lower()
    if prefix and not entry.video_id.lower().startswith(prefix):
        return False
    if spec.id_list is not None and entry.video_id not in spec.id_list:
        return False
    if not _presence_ok(entry.has_video, spec.video):
        return False
    if not _presence_ok(has_audio, spec.audio):
        return False
    if not _presence_ok(entry.has_vdet, spec.vdet):
        return False
    if not _tracks_ok(len(entry.track_ids), spec.tracks):
        return False
    if not _frames_ok(num_frames, spec.frames, spec.frames_threshold):
        return False
    if not _review_ok(review.verdict, spec.review):
        return False
    if spec.has_note and not review.note.strip():
        return False
    if spec.in_annotator and not review.in_annotator:
        return False
    for column, allowed in spec.labels.items():
        if allowed and get_label(entry, column) not in allowed:
            return False
    return True


def apply_filters(
    entries: list,  # list[VideoEntry]
    spec: FilterSpec,
    review_of: Callable[[str], ReviewState],
    audio_of: Callable[[str], bool | None] | None = None,
    frames_of: Callable[[str], int | None] | None = None,
    label_of: Callable[[VideoEntry, str], str | None] | None = None,
) -> list:
    """Filter a manifest, keeping the entries that pass ``spec``.

    Args:
        entries: The per-video manifest.
        spec: The current filter snapshot.
        review_of: Maps a video key to its :class:`ReviewState`.
        audio_of: Maps a video key to its probed audio-presence flag (or ``None``).
        frames_of: Maps a video key to its decoded frame count (or ``None``).
        label_of: Maps ``(entry, column)`` to its (possibly transformed) label value.

    Returns:
        The entries that match, in their original order.
    """
    audio_lookup = audio_of or (lambda _key: None)
    frames_lookup = frames_of or (lambda _key: None)
    return [
        e
        for e in entries
        if matches(
            e,
            review_of(e.key),
            spec,
            has_audio=audio_lookup(e.key),
            num_frames=frames_lookup(e.key),
            label_of=label_of,
        )
    ]
