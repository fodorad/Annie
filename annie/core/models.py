"""Domain models for Annie.

These are pure data structures with no I/O and no framework dependencies. They
form the vocabulary shared by every other layer: parsers produce them, the
service layer arranges them into a manifest, and the UI renders them.

The geometry uses image-pixel coordinates with the origin at the top-left, ``x``
increasing rightwards and ``y`` increasing downwards — the convention used by the
underlying detection/tracking CSV files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

LANDMARK_NAMES: tuple[str, ...] = (
    "left_eye",
    "right_eye",
    "nose",
    "left_mouth",
    "right_mouth",
)
"""The five named facial landmark points carried by every detection row.

Ordered to match the shared 17-column CSV schema.
"""

RowStatus = Literal["linked", "video_only", "annotation_only"]
"""The three states a manifest row can be in. See :mod:`annie.scanning`."""


@dataclass(slots=True)
class BBox:
    """A single face bounding box with confidence and named landmarks.

    Attributes:
        x: Left edge of the box, in pixels.
        y: Top edge of the box, in pixels.
        w: Box width, in pixels.
        h: Box height, in pixels.
        score: Detector confidence in ``[0, 1]``.
        landmarks: Mapping of landmark name (see :data:`LANDMARK_NAMES`) to its
            ``(x, y)`` pixel coordinate.
        track_id: The owning track's id when the box came from a ``.track`` file,
            otherwise ``None`` (e.g. a raw ``.vdet`` detection).
    """

    x: int
    y: int
    w: int
    h: int
    score: float
    landmarks: dict[str, tuple[int, int]] = field(default_factory=dict)
    track_id: int | None = None

    @property
    def x2(self) -> int:
        """Right edge of the box (``x + w``), in pixels."""
        return self.x + self.w

    @property
    def y2(self) -> int:
        """Bottom edge of the box (``y + h``), in pixels."""
        return self.y + self.h

    @property
    def area(self) -> int:
        """Box area in square pixels (clamped to be non-negative)."""
        return max(self.w, 0) * max(self.h, 0)

    def contains(self, px: int, py: int) -> bool:
        """Return whether the pixel ``(px, py)`` lies inside this box (inclusive).

        Args:
            px: X coordinate of the query point, in pixels.
            py: Y coordinate of the query point, in pixels.

        Returns:
            ``True`` if the point is within the box bounds, ``False`` otherwise.
        """
        return self.x <= px <= self.x2 and self.y <= py <= self.y2


@dataclass(slots=True)
class FrameAnnotation:
    """All face boxes belonging to a single video frame.

    A ``.vdet`` frame may carry several boxes (one per detected face); a
    ``.track`` frame carries exactly one.

    Attributes:
        frame_idx: Zero-based frame index within the source video.
        boxes: The bounding boxes present on this frame.
    """

    frame_idx: int
    boxes: list[BBox] = field(default_factory=list)


@dataclass(slots=True)
class Event:
    """A temporal annotation produced by the Annotator tab.

    Attributes:
        event_id: Stable identifier, unique within a video's annotation file.
        name: Human-readable label.
        start: Start time in seconds.
        end: End time in seconds.
        metadata: Optional free-form key/value extras.
    """

    event_id: str
    name: str
    start: float
    end: float
    metadata: dict[str, str] = field(default_factory=dict)


NO_ACTIVE_TRACK = -1
"""Sentinel ``track_id`` meaning "no active / protagonist track"."""


@dataclass(slots=True)
class VideoEntry:
    """One Browse row: a single video with all of its annotations aggregated.

    Unlike a per-annotation fan-out, a video is represented once and carries its
    raw detections (``.vdet``) together with every derived track. This is what the
    Browse tab renders and what the overview metrics are computed from.

    Attributes:
        video_id: The video stem used for matching, labelling, and as the review key.
        video_path: Path to the source ``.mp4``, or ``None`` when annotation-only.
        vdet_path: Path to the raw-detection ``.vdet``, or ``None`` if absent.
        track_paths: Paths to the video's ``__track{N}.csv`` files, ordered by track id.
        track_ids: The track indices, aligned with ``track_paths``.
        active_track_id: The resolved protagonist track id, or ``-1`` if none.
        status: One of :data:`RowStatus`.
        labels: Per-video label values gathered from CSV label sources, keyed by
            column name (e.g. ``{"Sentiment": "negative", "Angry": "0.33"}``).
            Drives the Browse label tags and the label filters.
        row_id: The video's **1-based position in the whole scanned dataset**,
            assigned once by :mod:`annie.dataset.scanning` over the full sorted
            manifest. It identifies the sample, not its slot in whatever list is
            on screen: filtering the Browse tab or queueing a subset into the
            Annotator leaves each row's number untouched, so "I stopped at 3400"
            still means something after a restart. ``0`` when unassigned (an entry
            built by hand rather than by a scan).
    """

    video_id: str
    video_path: Path | None = None
    vdet_path: Path | None = None
    track_paths: list[Path] = field(default_factory=list)
    track_ids: list[int] = field(default_factory=list)
    active_track_id: int = NO_ACTIVE_TRACK
    status: RowStatus = "linked"
    labels: dict[str, str] = field(default_factory=dict)
    row_id: int = 0

    @property
    def has_video(self) -> bool:
        """Whether a source video file is present."""
        return self.video_path is not None

    @property
    def has_vdet(self) -> bool:
        """Whether a raw-detection ``.vdet`` is present."""
        return self.vdet_path is not None

    @property
    def has_track(self) -> bool:
        """Whether at least one track file is present."""
        return bool(self.track_paths)

    @property
    def has_active_track(self) -> bool:
        """Whether a valid protagonist track is assigned (``active_track_id >= 0``)."""
        return self.active_track_id >= 0

    @property
    def label(self) -> str:
        """The on-screen label (the video id)."""
        return self.video_id

    @property
    def key(self) -> str:
        """Stable per-video identity, used as the SQLite review-status key."""
        return self.video_id
