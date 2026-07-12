"""Runtime configuration for Annie.

All settings are path-agnostic: nothing absolute is baked into the repository.
Defaults come from environment variables (prefixed ``ANNIE_``) so the same code
runs on any machine — set them on your dev box and your private paths never leak
into the public repo — and every folder can still be overridden at runtime from
the Dataset tab's pickers.

The infrastructure layer owns this module; higher layers read :data:`settings`
rather than touching the environment directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def _env_path(name: str) -> Path | None:
    """Return ``$name`` as an expanded :class:`~pathlib.Path`, or ``None`` if unset/empty."""
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


def _env_int(name: str, default: int) -> int:
    """Return ``$name`` parsed as an int, or ``default`` if unset or unparsable."""
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_tuple(name: str) -> tuple[str, ...] | None:
    """Return ``$name`` split on commas into a tuple, or ``None`` if unset/empty."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return tuple(col.strip() for col in raw.split(",") if col.strip())


def _default_db_path() -> Path:
    """Resolve the SQLite path for this session.

    Priority:
    1. ``ANNIE_DB_PATH`` — explicit persistent path (any valid file path).
    2. Otherwise: a per-session timestamped file under ``<ANNIE_HOME>/sessions/``
       so every restart starts with a fresh review state by default.
    """
    explicit = _env_path("ANNIE_DB_PATH")
    if explicit is not None:
        return explicit
    home = _env_path("ANNIE_HOME") or (Path.home() / ".annie")
    return home / "sessions" / f"annie_{datetime.now():%Y-%m-%d_%H-%M-%S}.db"


@dataclass(slots=True)
class Settings:
    """User-facing configuration, resolved once at import time.

    Attributes:
        videos_dir: Folder of source ``.mp4`` files (env ``ANNIE_VIDEO_DIR``).
        vdet_dir: Folder of raw-detection ``.vdet`` files (env ``ANNIE_VDET_DIR``).
        track_dir: Folder of ``__track{N}.csv`` files (env ``ANNIE_TRACK_DIR``).
        participants_file: The protagonist heuristic CSV (``uuid,track_id``); the
            Annotator's Export writes corrections to its ``_manual`` sibling
            (env ``ANNIE_PROTAGONIST_CSV``).
        labels_csv: A labels CSV file to seed as a label source on startup
            (env ``ANNIE_LABEL_CSV``).
        labels_key: The key column in :attr:`labels_csv` (env ``ANNIE_LABEL_KEY``);
            auto-detected from the header when omitted.
        labels_values: Comma-separated value columns from :attr:`labels_csv`
            (env ``ANNIE_LABEL_VALUES``); defaults to all non-key columns.
        db_path: SQLite file for this session's review status (good/bad/notes). Defaults
            to a per-session timestamped file; set ``ANNIE_DB_PATH`` to pin a persistent
            path instead.
        db_path_is_explicit: ``True`` when ``ANNIE_DB_PATH`` was set, so the UI can
            show "existing DB" mode instead of "new session DB" mode on startup.
        logs_dir: Directory that receives per-session log files
            (``<ANNIE_HOME>/logs``).
        sessions_dir: Directory that receives per-session SQLite databases
            (``<ANNIE_HOME>/sessions``).
        temp_dir: Scratch directory for rendered preview clips and exports. Defaults
            to ``<ANNIE_HOME>/tmp`` (falling back to ``~/.annie/tmp``) so it lands on
            the same persistent location as logs and sessions; override with
            ``ANNIE_TEMP_DIR``.
        config_dir: Optional extra directory scanned for user-saved dataset configs
            (env ``ANNIE_CONFIG_DIR``). Useful in Docker where the container-side
            ``data/config/`` is ephemeral; set this to a path on a persistent volume
            so saved configs survive restarts.
        data_dir: Optional override for the bundled ``data`` directory (env
            ``ANNIE_DATA_DIR``). Needed for non-editable installs (Docker, PyPI)
            where the package lives in ``site-packages`` and cannot locate ``data``
            by walking up from ``__file__``; point it at the checked-out ``data``.
        video_extensions: Accepted source-video suffixes (lower-case, dotted).
        vdet_extension: Suffix identifying a raw-detection file.
        track_glob: Glob identifying derived single-track files.
        render_max_workers: Maximum concurrent render jobs.
        temp_ttl_seconds: Age after which a rendered clip is swept (default 3 minutes).
        host: Interface the NiceGUI server binds to.
        port: Port the NiceGUI server listens on.
    """

    videos_dir: Path | None = field(default_factory=lambda: _env_path("ANNIE_VIDEO_DIR"))
    vdet_dir: Path | None = field(default_factory=lambda: _env_path("ANNIE_VDET_DIR"))
    track_dir: Path | None = field(default_factory=lambda: _env_path("ANNIE_TRACK_DIR"))
    participants_file: Path | None = field(
        default_factory=lambda: _env_path("ANNIE_PROTAGONIST_CSV")
    )
    labels_csv: Path | None = field(default_factory=lambda: _env_path("ANNIE_LABEL_CSV"))
    labels_key: str | None = field(
        default_factory=lambda: os.environ.get("ANNIE_LABEL_KEY", "").strip() or None
    )
    labels_values: tuple[str, ...] | None = field(
        default_factory=lambda: _env_tuple("ANNIE_LABEL_VALUES")
    )
    db_path: Path = field(default_factory=_default_db_path)
    db_path_is_explicit: bool = field(default_factory=lambda: bool(_env_path("ANNIE_DB_PATH")))
    logs_dir: Path = field(
        default_factory=lambda: (_env_path("ANNIE_HOME") or (Path.home() / ".annie")) / "logs"
    )
    sessions_dir: Path = field(
        default_factory=lambda: (_env_path("ANNIE_HOME") or (Path.home() / ".annie")) / "sessions"
    )
    temp_dir: Path = field(
        default_factory=lambda: (
            _env_path("ANNIE_TEMP_DIR")
            or (_env_path("ANNIE_HOME") or (Path.home() / ".annie")) / "tmp"
        )
    )
    config_dir: Path | None = field(default_factory=lambda: _env_path("ANNIE_CONFIG_DIR"))
    data_dir: Path | None = field(default_factory=lambda: _env_path("ANNIE_DATA_DIR"))
    video_extensions: tuple[str, ...] = (".mp4",)
    vdet_extension: str = ".vdet"
    track_glob: str = "*__track*.csv"
    render_max_workers: int = field(default_factory=lambda: _env_int("ANNIE_RENDER_WORKERS", 2))
    temp_ttl_seconds: int = field(default_factory=lambda: _env_int("ANNIE_TEMP_TTL", 180))
    host: str = field(default_factory=lambda: os.environ.get("ANNIE_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("ANNIE_PORT", 8080))


settings = Settings()
"""The process-wide settings instance.

Mutate its fields (e.g. from the Dataset tab) to change behaviour at runtime
without re-importing.
"""
