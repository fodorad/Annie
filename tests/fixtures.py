"""Synthetic dataset fixtures for the test suite.

Helpers that write the real on-disk MOSEI shapes (17-column CRLF CSVs, the
``__track{N}`` naming, the ``uuid,track_id`` participant files) into a temp dir,
so tests exercise the parsers and scanner without touching the private dataset.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

CSV_HEADER = (
    "frame_id,source,score,x,y,w,h,"
    "left_eye_x,left_eye_y,right_eye_x,right_eye_y,"
    "nose_x,nose_y,left_mouth_x,left_mouth_y,right_mouth_x,right_mouth_y"
)


def _row(
    frame_id: int,
    *,
    source: str = "data/raw/MOSEI/V3/video/x.mp4",
    score: float = 0.9,
    x: int = 10,
    y: int = 20,
    w: int = 100,
    h: int = 120,
) -> str:
    """Build one 17-column detection row with simple landmark placements."""
    cx, cy = x + w // 2, y + h // 2
    landmarks = [x + 20, y + 30, x + 60, y + 30, cx, cy, x + 25, y + 90, x + 65, y + 90]
    fields = [frame_id, source, score, x, y, w, h, *landmarks]
    return ",".join(str(f) for f in fields)


def write_csv(path: Path, rows: list[str]) -> Path:
    """Write a header + rows as a CRLF-terminated 17-column CSV.

    Args:
        path: Destination path (parent dirs created as needed).
        rows: Pre-formatted data row strings (e.g. from :func:`_row`).

    Returns:
        The written path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\r\n".join([CSV_HEADER, *rows]) + "\r\n"
    path.write_text(body, encoding="utf-8")
    return path


def write_vdet(directory: Path, video_id: str, frames: int = 3, faces_per_frame: int = 1) -> Path:
    """Write a ``{video_id}.vdet`` with ``faces_per_frame`` rows per frame."""
    rows: list[str] = []
    for frame_id in range(frames):
        for face in range(faces_per_frame):
            rows.append(_row(frame_id, x=10 + 200 * face, y=20))
    return write_csv(directory / f"{video_id}.vdet", rows)


def write_track(directory: Path, video_id: str, track_id: int, frames: int = 3) -> Path:
    """Write a ``{video_id}__track{track_id}.csv`` with one row per frame."""
    rows = [_row(frame_id, x=10 + 5 * track_id) for frame_id in range(frames)]
    return write_csv(directory / f"{video_id}__track{track_id}.csv", rows)


def write_video(directory: Path, video_id: str) -> Path:
    """Write a placeholder ``{video_id}.mp4`` file (content is irrelevant)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{video_id}.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return path


def write_participants(path: Path, mapping: dict[str, int]) -> Path:
    """Write a ``uuid,track_id`` participant CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["uuid,track_id", *(f"{uuid},{tid}" for uuid, tid in mapping.items())]
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return path


def write_table(path: Path, header: list[str], rows: list[dict[str, str]]) -> Path:
    """Write an arbitrary-schema CSV (header + dict rows) for label-source tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(header)]
    lines.extend(",".join(row.get(col, "") for col in header) for row in rows)
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return path


def write_appledouble_junk(directory: Path, name: str) -> Path:
    """Write a macOS AppleDouble ``._name`` junk file the scanner must skip."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"._{name}"
    path.write_bytes(b"\x00\x05\x16\x07")
    return path
