"""Dataset config files: save, load, and discover a :class:`SourceRegistry` (service).

A *config* is a small JSON file describing a dataset as a list of data sources, so a
set of folders/CSVs can be loaded in one click instead of re-picking them every
restart. Paths may be **relative** (resolved against the config file's directory, so
configs that reference the bundled examples travel with the repo) or absolute.

When running from a source checkout, example configs under ``data/config/`` are
auto-discovered and listed in the Dataset tab dropdown. When installed from PyPI
the ``data/`` directory is absent and discovery returns an empty list silently;
users load configs via the file picker or save their own.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from annie.core.config import settings
from annie.dataset.manipulate import detect_type
from annie.dataset.sources import CsvRole, DataSource, SourceKind, SourceRegistry
from annie.parsers.csvmeta import read_rows


def repo_data_dir() -> Path:
    """Return the bundled ``data`` directory.

    Prefers the explicit ``ANNIE_DATA_DIR`` override (``settings.data_dir``), which
    non-editable installs (Docker, PyPI) need because the package lives in
    ``site-packages`` and ``data`` does not. Otherwise falls back to three levels up
    from this file, which is valid only from a ``git clone``. Callers must handle a
    non-existent path gracefully.
    """
    if settings.data_dir is not None:
        return settings.data_dir
    return Path(__file__).resolve().parent.parent.parent / "data"


def bundled_config_dir() -> Path:
    """Return the source-checkout config directory (``data/config``).

    May not exist when Annie is installed from PyPI.
    """
    return repo_data_dir() / "config"


def discover_configs(directory: str | Path | None = None) -> list[Path]:
    """List ``*.json`` config files for the Dataset tab dropdown.

    Returns an empty list silently when a directory does not exist, so PyPI
    installs degrade gracefully without bundled examples.

    With no argument this scans the bundled config directory and, when
    ``ANNIE_CONFIG_DIR`` is set (e.g. in Docker to a persistent volume path),
    appends that directory's configs deduplicated by stem. When an explicit
    ``directory`` is passed it is treated as the sole search root — the
    ``ANNIE_CONFIG_DIR`` merge is not applied — so callers get exactly what they ask for.

    Args:
        directory: Explicit directory to scan exclusively; defaults to the bundled
            dir merged with ``ANNIE_CONFIG_DIR``.

    Returns:
        Sorted config file paths, or ``[]`` if the directories are absent.
    """
    if directory is not None:
        base = Path(directory)
        return sorted(base.glob("*.json")) if base.is_dir() else []

    base = bundled_config_dir()
    results: list[Path] = sorted(base.glob("*.json")) if base.is_dir() else []

    extra = settings.config_dir
    if extra is not None and extra != base and extra.is_dir():
        seen = {p.stem for p in results}
        results += [p for p in sorted(extra.glob("*.json")) if p.stem not in seen]

    return results


def config_name(path: str | Path) -> str:
    """Return a config's display name (its ``name`` field, or the file stem).

    Args:
        path: The config file.

    Returns:
        The configured name, or the filename stem on any read/parse error.
    """
    file = Path(path)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return file.stem
    return str(data.get("name") or file.stem)


def _auto_detect_column_types(csv_path: Path, value_columns: tuple[str, ...]) -> dict[str, str]:
    """Detect int/float/str for each value column by sampling up to 500 rows.

    Args:
        csv_path: Path to the CSV file.
        value_columns: The columns whose types should be detected.

    Returns:
        A ``{column: type_str}`` mapping where type is ``"int"``, ``"float"``,
        or ``"str"``.
    """
    rows = read_rows(csv_path)[:500]
    return {col: detect_type(row.get(col, "") for row in rows) for col in value_columns}


def load_config(path: str | Path) -> tuple[str, SourceRegistry, Path | None]:
    """Load a config file into a ``(name, registry, db_path)`` triple.

    Relative source paths — and the optional ``db`` path — are resolved against
    the config file's directory. Column types for CSV sources are auto-detected
    from a 500-row sample when not declared in the config.

    Args:
        path: The config file to load.

    Returns:
        The dataset name, a populated :class:`SourceRegistry`, and an optional
        :class:`~pathlib.Path` to a pre-configured review database (``None`` if
        the config has no ``"db"`` field).

    Raises:
        ValueError: If the JSON is malformed or a source kind is unknown.
    """
    file = Path(path)
    data = json.loads(file.read_text(encoding="utf-8"))
    base = file.resolve().parent
    registry = SourceRegistry()
    for entry in data.get("sources", []):
        kind = SourceKind(entry["kind"])
        raw = Path(entry["path"])
        resolved = (raw if raw.is_absolute() else base / raw).resolve()
        if kind is SourceKind.CSV:
            value_columns = tuple(entry.get("value_columns", []))
            declared_types: dict[str, str] = dict(entry.get("column_types", {}))
            if declared_types:
                column_types = declared_types
            else:
                column_types = _auto_detect_column_types(resolved, value_columns)
            registry.add(
                DataSource(
                    kind,
                    resolved,
                    role=CsvRole(entry.get("role", CsvRole.LABELS.value)),
                    key_column=entry.get("key_column"),
                    value_columns=value_columns,
                    column_types=column_types,
                )
            )
        else:
            registry.add(DataSource(kind, resolved))
    db_path: Path | None = None
    if "db" in data:
        raw_db = Path(data["db"])
        db_path = (raw_db if raw_db.is_absolute() else base / raw_db).resolve()
    return str(data.get("name") or file.stem), registry, db_path


def to_config_dict(
    registry: SourceRegistry,
    name: str,
    *,
    relative_to: str | Path | None = None,
    db_path: str | Path | None = None,
) -> dict:
    """Serialise a registry to a config dict.

    Args:
        registry: The sources to serialise.
        name: The dataset name to store.
        relative_to: If given, store paths relative to this directory where possible.
        db_path: Optional path to a review database to embed in the config so that
            loading it re-opens the same DB automatically.

    Returns:
        A JSON-serialisable config dict.
    """
    base = Path(relative_to) if relative_to is not None else None
    sources: list[dict] = []
    for source in registry.sources:
        path_str = str(source.path)
        if base is not None:
            try:
                path_str = os.path.relpath(source.path, base)
            except ValueError:  # different drive on Windows
                path_str = str(source.path)
        entry: dict = {"kind": source.kind.value, "path": path_str}
        if source.kind is SourceKind.CSV:
            entry["role"] = source.role.value
            entry["key_column"] = source.key_column
            entry["value_columns"] = list(source.value_columns)
            if source.column_types:
                entry["column_types"] = dict(source.column_types)
        sources.append(entry)
    result: dict = {"name": name, "sources": sources}
    if db_path is not None:
        db = Path(db_path)
        if base is not None:
            try:
                result["db"] = os.path.relpath(db, base)
            except ValueError:
                result["db"] = str(db)
        else:
            result["db"] = str(db)
    return result


def save_config(
    path: str | Path,
    registry: SourceRegistry,
    name: str,
    *,
    relative_to: str | Path | None = None,
    db_path: str | Path | None = None,
) -> Path:
    """Write a registry to a config file as JSON.

    Args:
        path: Destination ``.json`` path.
        registry: The sources to save.
        name: The dataset name to store.
        relative_to: If given, store paths relative to this directory where possible.
        db_path: Optional review-database path to embed in the config.

    Returns:
        The path written.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            to_config_dict(registry, name, relative_to=relative_to, db_path=db_path), indent=2
        ),
        encoding="utf-8",
    )
    return out
