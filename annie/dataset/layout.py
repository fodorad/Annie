"""Nested video layouts: find videos below a root with a ``{field}`` path template (pure).

A flat folder needs no layout — one video per file, id = file stem. Real datasets are often
nested (``<root>/<group>/<subject>/clip_<part>.mp4``), where the file stem alone is neither
unique nor informative. A :class:`VideoLayout` describes such a tree once:

    pattern     ``{group}/{subject}/clip_{part}.mp4``   (path relative to the root)
    id_template ``{subject}_{part}``                    (how the unique video id is built)

``{name}`` captures one path segment, or part of a file name when mixed with literal text;
``**`` matches any number of directories. The captured fields become the video's labels, so
Browse can tag and filter by them. Nothing here knows any particular dataset.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_FIELD = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
"""A ``{name}`` placeholder (a valid identifier between braces)."""

_PREVIEW_IDS = 5
"""How many example ids a :class:`DiscoveryReport` summary shows."""


def _is_junk(name: str) -> bool:
    """Whether a file or folder name is OS junk (any dotfile: ``._*``, ``.DS_Store``)."""
    return name.startswith(".")


def contains_nested_videos(root: Path, suffixes: tuple[str, ...]) -> bool:
    """Whether any non-junk video file sits *below* ``root`` (not directly in it).

    Used to hint that a flat scan found nothing because the videos are nested. Stops at the
    first hit, so it is cheap on a tree that has videos and only walks fully when it has none.

    Args:
        root: The folder to look under.
        suffixes: Lower-case dotted video suffixes, e.g. ``(".mp4",)``.

    Returns:
        ``True`` if a video file exists in any sub-folder.
    """
    if not root.is_dir():
        return False
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not _is_junk(d)]
        if Path(current) == root:
            continue
        if any(not _is_junk(f) and f.lower().endswith(suffixes) for f in files):
            return True
    return False


@dataclass(slots=True, frozen=True)
class DiscoveredVideo:
    """One video found by a layout.

    Attributes:
        path: Absolute path of the file.
        video_id: The unique id built from the id template.
        fields: The captured template fields, e.g. ``{"subject": "s1", "part": "x"}``.
    """

    path: Path
    video_id: str
    fields: dict[str, str]


@dataclass(slots=True)
class DiscoveryReport:
    """The outcome of scanning a root with a layout.

    Attributes:
        videos: Discovered videos, sorted by id; a colliding id keeps its first file only.
        collisions: Ids that more than one file produced (the extras are dropped).
    """

    videos: list[DiscoveredVideo] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """One line for the Dataset tab: counts, per-field value counts, collisions, examples."""
        if not self.videos:
            return "No videos matched this layout."
        counts: dict[str, set[str]] = {}
        for video in self.videos:
            for name, value in video.fields.items():
                counts.setdefault(name, set()).add(value)
        parts = [f"{len(self.videos)} videos"]
        parts += [f"{name} ×{len(values)}" for name, values in counts.items()]
        parts.append(f"{len(self.collisions)} collisions")
        examples = ", ".join(v.video_id for v in self.videos[:_PREVIEW_IDS])
        return f"{' · '.join(parts)} · e.g. {examples}"


@dataclass(slots=True, frozen=True)
class VideoLayout:
    """A path template that finds and names the videos of a nested dataset.

    Attributes:
        pattern: Path of the videos relative to the root, with ``{field}`` placeholders.
        id_template: Template for the unique video id, using the same fields. ``None``
            joins every field with ``_`` in pattern order.
    """

    pattern: str
    id_template: str | None = None

    def field_names(self) -> list[str]:
        """The pattern's field names in order of appearance (unique)."""
        return list(dict.fromkeys(_FIELD.findall(self.pattern)))

    def validate(self) -> None:
        """Check the layout is usable.

        Raises:
            ValueError: If the pattern is empty, absolute, climbs out of the root (``..``),
                repeats a field, has no fields, or the id template uses an unknown field.
        """
        pattern = self.pattern.strip()
        if not pattern:
            raise ValueError("The layout pattern is empty.")
        if pattern.startswith(("/", "\\")) or ".." in PurePosixPath(pattern).parts:
            raise ValueError("The pattern must be relative to the root and stay inside it.")
        names = _FIELD.findall(pattern)
        if not names:
            raise ValueError("The pattern needs at least one {field} placeholder.")
        if len(names) != len(set(names)):
            raise ValueError("Each {field} may appear only once in the pattern.")
        if self.id_template is not None:
            unknown = set(_FIELD.findall(self.id_template)) - set(names)
            if unknown:
                raise ValueError(
                    f"The video id uses unknown field(s): {', '.join(sorted(unknown))}."
                )
            if not _FIELD.search(self.id_template):
                raise ValueError("The video id template needs at least one {field}.")

    def _regex(self) -> re.Pattern[str]:
        """Compile the pattern to an anchored regex over the posix relative path."""
        out: list[str] = []
        for segment in PurePosixPath(self.pattern.strip()).parts:
            if segment == "**":
                out.append("(?:[^/]+/)*")
                continue
            body = ""
            last = 0
            for found in _FIELD.finditer(segment):
                body += re.escape(segment[last : found.start()])
                body += f"(?P<{found.group(1)}>[^/]+?)"
                last = found.end()
            body += re.escape(segment[last:])
            out.append(body + "/")
        return re.compile("^" + "".join(out).removesuffix("/") + "$")

    def match(self, relative_path: str) -> dict[str, str] | None:
        """Return the captured fields if ``relative_path`` fits the pattern, else ``None``."""
        found = self._regex().match(relative_path)
        return found.groupdict() if found else None

    def video_id(self, fields: Mapping[str, str]) -> str:
        """Build the video id for a match's ``fields``."""
        if self.id_template is None:
            return "_".join(fields[name] for name in self.field_names())
        return _FIELD.sub(lambda m: fields[m.group(1)], self.id_template)

    def discover(self, root: Path) -> DiscoveryReport:
        """Scan ``root`` recursively for files matching the pattern.

        Junk (dotfiles/folders) is skipped. A missing root yields an empty report.

        Args:
            root: The dataset root the pattern is relative to.

        Returns:
            The sorted videos and any id collisions.

        Raises:
            ValueError: If the layout is invalid (see :meth:`validate`).
        """
        self.validate()
        report = DiscoveryReport()
        if not root.is_dir():
            return report
        regex = self._regex()
        by_id: dict[str, DiscoveredVideo] = {}
        seen: Counter[str] = Counter()
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if not path.is_file() or any(_is_junk(part) for part in relative.parts):
                continue
            found = regex.match(relative.as_posix())
            if found is None:
                continue
            fields = found.groupdict()
            video_id = self.video_id(fields)
            seen[video_id] += 1
            by_id.setdefault(video_id, DiscoveredVideo(path, video_id, fields))
        report.videos = [by_id[key] for key in sorted(by_id)]
        report.collisions = sorted(key for key, n in seen.items() if n > 1)
        return report
