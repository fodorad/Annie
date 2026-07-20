"""Dataset tab — define the dataset by adding data sources; see live metrics.

Annie is dataset-agnostic: instead of fixed folder fields, you build the dataset
from a list of **data sources** (the ``+`` box). A videos folder is the mandatory
spine; vdet/track folders, a protagonist CSV, and any number of label CSVs
attach to it. There is no Scan button — adding or removing a source re-scans in
place, and every metric and Browse row updates immediately. Each source shows an
Available / Unavailable chip and a live item count.

Each browser tab maintains its own scan-in-progress flag and database-mode
selection via a per-client :class:`_DatasetState` keyed by
:attr:`nicegui.Client.id`.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from nicegui import context, run, ui

from annie.core import logbook, theme
from annie.core.config import settings
from annie.core.state import _seed_registry, state
from annie.dataset import datasets
from annie.dataset.manipulate import detect_type
from annie.dataset.sources import (
    KIND_ICONS,
    KIND_LABELS,
    TASK_LABELS,
    CsvRole,
    DataSource,
    SourceKind,
    SourceRegistry,
    task_readiness,
)
from annie.pages import annotator, browse
from annie.pages.csv_dialog import configure_csv
from annie.pages.folder_picker import pick_directory, pick_file
from annie.pages.utils import notify_detached
from annie.parsers.csvmeta import read_header, read_rows
from annie.parsers.participants import DEFAULT_KEY_COLUMN, DEFAULT_VALUE_COLUMN

# ── per-client state ──────────────────────────────────────────────────────────


@dataclass
class _DatasetState:
    """Scan + persistence state isolated to one browser tab.

    Attributes:
        scanning: Whether a rescan is in progress for this client.
        db_mode: How the active DB may be edited — ``"session"`` means Annie created it
            as a throwaway, so retyping its path *renames* it (the data moves with it);
            ``"existing"`` means it is a named DB (env, example, or config-pinned) that
            a retyped path merely *switches away from*, never moves. The Persistence
            block shows no mode switch — this only decides what an edit means.
        db_path_custom: The user-typed or config-embedded DB path when mode is ``"existing"``.
        config_value: The active config selector value, so "Add data source" knows
            whether to seed the picker from the ANNIE_* env dirs ([ENV vars] config)
            or open at home / the last-selected parent (New config).
        session_db_path: The session-mode DB file (renamable); tracked separately
            from ``db_path_custom`` so switching modes restores each side's choice.
    """

    scanning: bool = False
    # The startup DB is always a *named* one (ANNIE_DB_PATH, or the stable annie_env.db),
    # so it is never renamed out from under whatever points at it; only "New config" and
    # the ＋ button mint throwaway session DBs.
    db_mode: str = "existing"
    db_path_custom: str = str(settings.db_path)
    config_value: str = field(
        default_factory=lambda: _ENV_CONFIG if _has_env_sources() else _NEW_CONFIG
    )
    session_db_path: str = field(
        default_factory=lambda: "" if settings.db_path_is_explicit else str(settings.db_path)
    )


#: Per-client state registry; cleaned up on disconnect in :func:`render`.
_dataset_state: dict[str, _DatasetState] = {}


def _ds() -> _DatasetState:
    """Return the :class:`_DatasetState` for the currently active client."""
    cid = context.client.id
    if cid not in _dataset_state:
        _dataset_state[cid] = _DatasetState()
    return _dataset_state[cid]


#: Overview metric key → its live :class:`nicegui.ui.label`, updated after each scan.
_METRICS: dict[str, ui.label] = {}


def _video_stems() -> set[str]:
    """Current video stems, used to auto-suggest a CSV key column."""
    if state.scan is None:
        return set()
    return {e.video_id for e in state.scan.entries if e.has_video}


async def _apply_changes() -> None:
    """Re-scan the filesystem and refresh every dependent view after a source change."""
    ds = _ds()
    if ds.scanning:
        return
    ds.scanning = True
    _source_list.refresh()  # redraw button in loading state
    try:
        await run.io_bound(state.rescan)
    finally:
        ds.scanning = False
    _metric_cards.refresh()
    _source_list.refresh()
    _task_sections.refresh()
    browse.refresh()
    annotator.update_availability()


async def _rescan() -> None:
    """Re-walk the configured folders (e.g. to pick up newly-converted files).

    The Rescan button lives inside ``_source_list``, which ``_apply_changes`` rebuilds
    — so by the time there is a count to report, this handler's own slot is gone and
    the toast has to be raised against the client captured beforehand.
    """
    client = context.client
    await _apply_changes()
    counts = state.scan.counts if state.scan is not None else {}
    message = f"Rescanned — {counts.get('num_videos', 0)} videos"
    notify_detached(client, message, color=theme.PRIMARY)


#: Sentinel select values for the two non-file config options.
_NEW_CONFIG = "__new__"
_ENV_CONFIG = "__env__"  #: the "sources seeded from ANNIE_* env vars" option


def _has_env_sources() -> bool:
    """True if any ANNIE_* environment variable seeds a data source."""
    return any(
        [
            settings.videos_dir,
            settings.vdet_dir,
            settings.track_dir,
            settings.participants_file,
            settings.labels_csv,
        ]
    )


# ── load / save a dataset config ─────────────────────────────────────────────


async def _apply_registry(
    name: str, registry: SourceRegistry, config_db: Path | None = None
) -> None:
    """Replace the active sources with ``registry`` and refresh everything.

    When the loaded config embeds a ``"db"`` path, the review store is switched
    to that file and the Persistence block updates to reflect "existing DB" mode.
    """
    ds = _ds()
    state.registry = registry
    state.audio_cache.clear()
    state.frames_cache.clear()
    if config_db is not None:
        ds.db_mode = "existing"
        ds.db_path_custom = str(config_db)
        state.set_store(config_db)
    await _apply_changes()
    _persistence_section.refresh()
    counts = state.scan.counts if state.scan is not None else {}
    ui.notify(f"Loaded '{name}' — {counts.get('num_videos', 0)} videos", color=theme.PRIMARY)


async def _load_config_path(path: str | Path) -> None:
    """Load a config file by path, surfacing any error to the log + a toast."""
    config_path = Path(path)
    try:
        name, registry, config_db = datasets.load_config(config_path)
    except Exception as exc:  # noqa: BLE001 - surface a bad config to the user
        logbook.report_exception(f"Failed to load config: {path}", exc)
        ui.notify(f"Could not load config: {exc}", color=theme.DANGER)
        return
    await _apply_registry(name, registry, config_db=config_db)


async def _new_config() -> None:
    """Reset the dataset to a blank slate: no sources, fresh timestamped session DB.

    A brand-new config gets a *throwaway* database, not the stable ``annie_env.db``:
    only the env config and saved/example configs own a named, reloadable DB. Renaming
    the path in the Persistence box is what promotes this throwaway into a kept one.
    """
    ds = _ds()
    state.registry = SourceRegistry()
    state.audio_cache.clear()
    state.frames_cache.clear()
    fresh = _fresh_session_db_path()
    ds.db_mode = "session"
    ds.db_path_custom = ""
    ds.session_db_path = str(fresh)
    state.set_store(fresh)
    await _apply_changes()
    _persistence_section.refresh()
    ui.notify("New config", color=theme.PRIMARY)


async def _env_config() -> None:
    """Reload sources from the ANNIE_* environment variables."""
    ds = _ds()
    state.registry = _seed_registry()
    state.audio_cache.clear()
    state.frames_cache.clear()
    # The env DB is a named, reloadable database (ANNIE_DB_PATH or annie_env.db), never
    # a throwaway — so it is pinned, not renamable.
    ds.db_mode = "existing"
    ds.db_path_custom = str(settings.db_path)
    ds.session_db_path = ""
    state.set_store(settings.db_path)
    await _apply_changes()
    _persistence_section.refresh()
    ui.notify("Loaded from environment variables", color=theme.PRIMARY)


def _config_slug(name: str) -> str:
    """Turn a config display name into a filesystem-safe stem for its files.

    Lower-cased, non-alphanumeric runs collapsed to a single underscore, trimmed.
    Falls back to ``"dataset"`` when nothing usable remains, so the config and its
    ``annie_<stem>.db`` always get a valid, predictable name.

    Args:
        name: The user-entered config name.

    Returns:
        A safe stem, e.g. ``"My Dataset!"`` → ``"my_dataset"``.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "dataset"


async def _save_current() -> None:
    """Show a name dialog, then pick a folder and write the config JSON."""
    if not state.registry.sources:
        ui.notify("Add some sources before saving a config.", color=theme.WARNING)
        return

    with ui.dialog() as dialog, ui.card().classes("w-80 gap-4"):
        ui.label("Save config").classes("text-lg font-medium")
        name_input = (
            ui.input("Config name", value="My dataset")
            .classes("w-full")
            .props("dense outlined autofocus")
        )
        with ui.row().classes("justify-end gap-2 w-full"):
            ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat")
            ui.button(
                "Choose folder…",
                icon="folder_open",
                on_click=lambda: dialog.submit((name_input.value or "").strip()),
            ).props("unelevated")

    config_name = await dialog
    if not config_name:
        return

    folder = await pick_directory(settings.config_dir)
    if not folder:
        return

    ds = _ds()
    stem = _config_slug(config_name)
    out = Path(folder) / f"{stem}.json"
    # Always pin a config-owned DB under ANNIE_HOME so this dataset reopens against the
    # same review database every time, and switch the live store to it now.
    db_to_save = (settings.annie_home / f"annie_{stem}.db").resolve()
    datasets.save_config(out, state.registry, config_name, relative_to=folder, db_path=db_to_save)
    ds.db_mode = "existing"
    ds.db_path_custom = str(db_to_save)
    state.set_store(db_to_save)
    _persistence_section.refresh()
    ui.notify(f"Saved '{config_name}' → {out.name} (DB: {db_to_save.name})", color=theme.PRIMARY)


@ui.refreshable
def _config_section() -> None:
    """Build the config selector: sentinel options + predefined configs, auto-loads on change."""
    configs = datasets.discover_configs()

    options: dict[str, str] = {_NEW_CONFIG: "New config…"}
    if _has_env_sources():
        options[_ENV_CONFIG] = "[ENV vars] Custom…"
    for p in configs:
        options[str(p)] = datasets.config_name(p)

    default = _ENV_CONFIG if _has_env_sources() else _NEW_CONFIG

    async def on_select(e: object) -> None:
        value = getattr(e, "value", None)
        if value:
            _ds().config_value = value  # remember for the picker's default directory
        if value == _NEW_CONFIG:
            await _new_config()
        elif value == _ENV_CONFIG:
            await _env_config()
        elif value:
            await _load_config_path(value)

    with ui.row().classes("w-full items-center gap-2 wrap"):
        ui.icon("bookmarks", color=theme.PRIMARY)
        ui.select(options, value=default, label="Config", on_change=on_select).props(
            "dense outlined"
        ).classes("w-[20rem]")
        ui.button("Load from file…", icon="folder_open", on_click=lambda: _load_from_file()).props(
            "unelevated"
        )
        ui.button("Save current…", icon="save", on_click=_save_current).props("outline")


async def _load_from_file() -> None:
    """Pick a config JSON anywhere on disk and load it."""
    chosen = await pick_file(settings.config_dir)
    if chosen:
        await _load_config_path(chosen)


async def _add_kind(kind: SourceKind) -> None:
    """Run the add flow for a chosen source kind."""
    # In the [ENV vars] config, seed the picker from the ANNIE_* dirs; otherwise (New
    # config, loaded configs) open at home first, then beside the siblings of the last
    # pick (see annie.pages.folder_picker), rather than jumping into an env data dir.
    in_env = _ds().config_value == _ENV_CONFIG
    if kind is SourceKind.CSV:
        start = (settings.participants_file or settings.labels_csv) if in_env else None
        chosen = await pick_file(start)
        if not chosen:
            return
        source = await configure_csv(chosen, _video_stems())
        if source is None:
            return
    else:
        start = (
            {
                SourceKind.VIDEO: settings.videos_dir,
                SourceKind.VDET: settings.vdet_dir,
                SourceKind.TRACK: settings.track_dir,
            }.get(kind)
            if in_env
            else None
        )
        chosen = await pick_directory(start)
        if not chosen:
            return
        source = DataSource(kind, Path(chosen))
    state.registry.add(source)
    if kind is SourceKind.VIDEO:
        await _offer_sibling_sources(Path(chosen))
    await _apply_changes()


# ── auto-fill related sources beside a chosen videos folder ──────────────────


#: Conventional sibling names Annie looks for next to a videos folder. Protagonist
#: prefers the manually-corrected CSV over the heuristic one; a labels CSV may be
#: named ``label.csv`` or ``labels.csv``.
_VDET_DIRNAME = "vdet"
_TRACK_DIRNAME = "track"
_PROTAGONIST_NAMES = ("protagonist_track_manual.csv", "protagonist_track_heuristic.csv")
_LABEL_NAMES = ("label.csv", "labels.csv")


@dataclass
class _Candidate:
    """One auto-detected sibling source offered for auto-fill."""

    label: str
    source: DataSource


def _label_source(csv_path: Path) -> DataSource | None:
    """Build a labels CSV source with the same defaults as env-seeding.

    The first column is the key (joins to the video id) and every other column is a
    value column, with its type sniffed from a sample of rows. ``None`` if the CSV
    has no usable header/value columns.
    """
    header = read_header(csv_path)
    if not header:
        return None
    key = header[0]
    values = tuple(col for col in header if col != key)
    if not values:
        return None
    rows = read_rows(csv_path)[:500]
    column_types: dict[str, str] = {
        col: detect_type(row.get(col, "") for row in rows) for col in values
    }
    return DataSource(
        SourceKind.CSV,
        csv_path,
        role=CsvRole.LABELS,
        key_column=key,
        value_columns=values,
        column_types=column_types,
    )


def _detect_sibling_sources(video_dir: Path) -> list[_Candidate]:
    """Find conventional vdet/track/protagonist/label siblings of ``video_dir``.

    Looks in the videos folder's parent directory and skips anything a source of
    that role already covers. Reads CSV headers/rows, so callers run it off the
    event loop (see :func:`_offer_sibling_sources`).
    """
    parent = video_dir.parent
    existing = {s.kind for s in state.registry.sources}
    has_protagonist = any(s.is_protagonist for s in state.registry.sources)
    label_paths = {
        s.path for s in state.registry.sources if s.kind is SourceKind.CSV and not s.is_protagonist
    }
    candidates: list[_Candidate] = []

    vdet = parent / _VDET_DIRNAME
    if SourceKind.VDET not in existing and vdet.is_dir():
        candidates.append(
            _Candidate(f"Vdet folder — {vdet.name}/", DataSource(SourceKind.VDET, vdet))
        )

    track = parent / _TRACK_DIRNAME
    if SourceKind.TRACK not in existing and track.is_dir():
        candidates.append(
            _Candidate(f"Track folder — {track.name}/", DataSource(SourceKind.TRACK, track))
        )

    if not has_protagonist:
        for name in _PROTAGONIST_NAMES:
            path = parent / name
            if path.is_file():
                source = DataSource(
                    SourceKind.CSV,
                    path,
                    role=CsvRole.PROTAGONIST,
                    key_column=DEFAULT_KEY_COLUMN,
                    value_columns=(DEFAULT_VALUE_COLUMN,),
                )
                candidates.append(_Candidate(f"Protagonist CSV — {name}", source))
                break  # a manual CSV supersedes the heuristic one

    for name in _LABEL_NAMES:
        path = parent / name
        if path.is_file() and path not in label_paths:
            source = _label_source(path)
            if source is not None:
                candidates.append(_Candidate(f"Label CSV — {name}", source))
            break

    return candidates


async def _offer_sibling_sources(video_dir: Path) -> None:
    """Offer to auto-fill vdet/track/protagonist/label sources found beside the videos.

    A quality-of-life step after adding a videos folder: if Annie recognises the
    conventional sibling files/folders, it asks once whether to add them, so the user
    doesn't have to pick each path by hand.
    """
    candidates = await run.io_bound(_detect_sibling_sources, video_dir)
    if not candidates:
        return

    with ui.dialog() as dialog, ui.card().classes("w-[32rem] max-w-full gap-3"):
        ui.label("Auto-fill related sources?").classes("text-lg font-medium")
        ui.label(f"Annie found these next to the videos folder in {video_dir.parent}:").classes(
            "text-sm"
        ).style(f"color:{theme.NEUTRAL}")
        boxes = [(ui.checkbox(c.label, value=True), c) for c in candidates]
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Skip", on_click=lambda: dialog.submit(False)).props("flat")
            ui.button(
                "Fill selected", icon="auto_awesome", on_click=lambda: dialog.submit(True)
            ).props("unelevated")

    if not await dialog:
        return
    added = 0
    for box, candidate in boxes:
        if box.value:
            state.registry.add(candidate.source)
            added += 1
    if added:
        ui.notify(f"Added {added} related source(s)", color=theme.PRIMARY)


async def _remove(source: DataSource) -> None:
    """Remove a source and refresh."""
    state.registry.remove(source)
    await _apply_changes()


# ── metric cards ─────────────────────────────────────────────────────────────


def _metric_card(title: str, key: str, color: str) -> None:
    """Build one overview metric card and register its value label under ``key``."""
    with ui.card().classes("flex-1 min-w-[8rem]"):
        ui.label(title).style(f"color:{color}").classes("text-sm")
        _METRICS[key] = ui.label("—").classes("text-2xl font-medium")


@ui.refreshable
def _metric_cards() -> None:
    """Build the two rows of overview metric cards and fill them from the scan."""
    _METRICS.clear()
    with ui.row().classes("w-full gap-3"):
        _metric_card("# videos", "num_videos", theme.NEUTRAL)
        _metric_card("# vdet files", "num_vdet_files", theme.VDET_COLOR)
        _metric_card("# track files", "num_track_files", theme.TRACK_COLOR)
    with ui.row().classes("w-full gap-3"):
        _metric_card("# videos with vdet + track", "videos_vdet_and_track", theme.PRIMARY)
        _metric_card("# videos with vdet", "videos_with_vdet", theme.VDET_COLOR)
        _metric_card("# videos with track", "videos_with_track", theme.TRACK_COLOR)

    counts = state.scan.counts if state.scan is not None else {}
    for key, label in _METRICS.items():
        label.set_text(str(counts.get(key, 0)))


# ── source list + add box ────────────────────────────────────────────────────


def _availability_chip(source: DataSource) -> None:
    """Draw the Available / Unavailable chip for a source."""
    if source.available:
        ui.badge("Available", color=theme.AVAILABLE)
    else:
        ui.badge("Unavailable", color=theme.UNAVAILABLE).tooltip("path not found")


def _source_card(source: DataSource) -> None:
    """Render one configured source row."""
    with ui.card().classes("w-full"), ui.row().classes("w-full items-center gap-3 no-wrap"):
        ui.icon(KIND_ICONS[source.kind], color=theme.PRIMARY)
        with ui.column().classes("gap-0 flex-grow min-w-0"):
            with ui.row().classes("items-center gap-2"):
                ui.label(source.label).classes("font-medium")
                _availability_chip(source)
            ui.label(str(source.path)).classes("text-xs break-all").style(f"color:{theme.NEUTRAL}")
            if source.kind is SourceKind.CSV and source.value_columns:
                ui.label(
                    f"key: {source.key_column} · columns: {', '.join(source.value_columns)}"
                ).classes("text-xs").style(f"color:{theme.NEUTRAL}")
        ui.button(icon="delete", on_click=lambda s=source: _remove(s)).props(
            "flat round dense"
        ).tooltip("Remove source")


@ui.refreshable
def _source_list() -> None:
    """Build the list of configured sources plus the add box."""
    ds = _ds()
    with ui.column().classes("w-full gap-2"):
        ui.label("Sources").classes("text-lg font-medium")
        if not state.registry.sources:
            ui.label("No sources yet — add a videos folder to begin.").style(
                f"color:{theme.NEUTRAL}"
            )
        for source in state.registry.sources:
            _source_card(source)

        with ui.row().classes("items-center gap-2"):
            with ui.button("Add data source", icon="add").props("outline"), ui.menu():
                for kind in state.registry.available_kinds_to_add():
                    ui.menu_item(KIND_LABELS[kind], on_click=lambda k=kind: _add_kind(k))
            rescan_btn = (
                ui.button("Rescan", icon="refresh", on_click=_rescan)
                .props("flat")
                .tooltip(
                    "Re-read the folders — picks up files added since the last scan "
                    "(e.g. while a conversion is running)"
                )
            )
            if ds.scanning:
                rescan_btn.props(add="loading")


@ui.refreshable
def _task_sections() -> None:
    """Show, per supervision task, which sources it needs and whether they're present.

    This mirrors the Annotator's task switch: a task is offered there exactly when
    it is *ready* here, so the Dataset tab makes it obvious what a given task expects.
    """
    with ui.column().classes("w-full gap-2"):
        ui.label("Tasks").classes("text-lg font-medium")
        ui.label("What each Annotator task needs — a task is offered once it is ready.").classes(
            "text-xs"
        ).style(f"color:{theme.NEUTRAL}")
        for readiness in task_readiness(state.registry):
            with ui.card().classes("w-full"), ui.row().classes("w-full items-center gap-3 no-wrap"):
                ready = readiness.ready
                ui.icon(
                    "check_circle" if ready else "radio_button_unchecked",
                    color=theme.AVAILABLE if ready else theme.NEUTRAL,
                )
                with ui.column().classes("gap-0 flex-grow min-w-0"):
                    ui.label(TASK_LABELS[readiness.task]).classes("font-medium")
                    with ui.row().classes("items-center gap-2 flex-wrap"):
                        for req in readiness.requirements:
                            ui.badge(
                                req.label,
                                color=theme.AVAILABLE if req.present else theme.UNAVAILABLE,
                            ).tooltip("present" if req.present else "missing")
                ui.badge(
                    "Ready" if ready else "Not ready",
                    color=theme.AVAILABLE if ready else theme.NEUTRAL,
                )


# ── persistence block ─────────────────────────────────────────────────────────


#: Matches the auto-generated ``annie_<timestamp>.db`` session-DB filename.
_ANNIE_DB_RE = re.compile(r"^annie_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.db$")


def _fresh_session_db_path() -> Path:
    """A brand-new timestamped session-DB path under the sessions directory."""
    return settings.sessions_dir / f"annie_{datetime.now():%Y-%m-%d_%H-%M-%S}.db"


def _session_dbs() -> list[Path]:
    """List ``*.db`` files in the sessions directory for the "use existing" picker.

    Renamed databases come first (they are the ones a user deliberately named),
    then the auto-generated ``annie_<timestamp>.db`` files; both groups newest-first
    by modification time.
    """
    try:
        with os.scandir(settings.sessions_dir) as it:
            found = [
                (entry.name, Path(entry.path), entry.stat().st_mtime)
                for entry in it
                if entry.is_file() and entry.name.endswith(".db") and not entry.name.startswith(".")
            ]
    except (PermissionError, OSError):
        return []
    renamed = sorted(
        (t for t in found if not _ANNIE_DB_RE.match(t[0])), key=lambda t: t[2], reverse=True
    )
    auto = sorted((t for t in found if _ANNIE_DB_RE.match(t[0])), key=lambda t: t[2], reverse=True)
    return [path for _name, path, _mtime in (*renamed, *auto)]


def _new_session_db() -> None:
    """Create and switch to a fresh timestamped session DB (a clean default)."""
    ds = _ds()
    fresh = _fresh_session_db_path()
    ds.db_mode = "session"
    ds.session_db_path = str(fresh)
    state.set_store(fresh)
    ui.notify(f"New session DB: {fresh.name}", color=theme.PRIMARY)
    _persistence_section.refresh()


def _rename_session_db(new_str: str) -> None:
    """Rename the current session DB file, moving its data to the new path."""
    ds = _ds()
    new_str = new_str.strip()
    if not new_str:
        ui.notify("Enter a name for the session DB.", color=theme.WARNING)
        return
    new = Path(new_str).expanduser()
    if new.suffix != ".db":
        new = new.with_suffix(".db")
    # The live store is the truth about which file is open; session_db_path can lag it.
    current = state.store.db_path
    if new == current:
        return
    if new.exists():
        ui.notify(f"A database already exists at {new}", color=theme.DANGER)
        return
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        if current.exists():
            shutil.move(str(current), str(new))
    except OSError as exc:
        ui.notify(f"Could not rename DB: {exc}", color=theme.DANGER)
        return
    ds.session_db_path = str(new)
    state.set_store(new)  # (re)creates the file if the move was a no-op
    ui.notify(f"Renamed session DB to {new.name}", color=theme.PRIMARY)
    _persistence_section.refresh()


def _select_existing_db(path: Path) -> None:
    """Load a DB chosen from the sessions list (auto-applies, no Apply step)."""
    ds = _ds()
    ds.db_mode = "existing"
    ds.db_path_custom = str(path)
    state.set_store(path)
    ui.notify(f"Review DB: {path.name}", color=theme.PRIMARY)
    _persistence_section.refresh()


def _apply_db_path(path_str: str) -> None:
    """Apply an edited DB path on blur/Enter — renaming a session DB, else opening it.

    The single path box means one edit has two sensible readings, resolved by what the
    current DB *is*:

    * a **session** DB (one Annie created as a throwaway) is *renamed* — the file moves
      to the new path so the review state written so far comes along, which is how a
      timestamped default becomes a deliberately named database;
    * anything else (a config-pinned or env DB) is left alone and the new path is simply
      **opened**, since silently moving a database a config points at would break it.
    """
    ds = _ds()
    path_str = path_str.strip()
    if not path_str:
        return
    new = Path(path_str).expanduser()
    if new.suffix != ".db":
        new = new.with_suffix(".db")
    if new == state.store.db_path:
        return
    if ds.db_mode == "session":
        _rename_session_db(str(new))
        return
    ds.db_path_custom = str(new)
    state.set_store(new)
    ui.notify(f"Review DB: {new.name}", color=theme.PRIMARY)
    _persistence_section.refresh()


async def _pick_db_file() -> None:
    """Open a file picker to select an existing DB, then apply it automatically."""
    chosen = await pick_file(settings.sessions_dir)
    if chosen:
        _select_existing_db(Path(chosen))


@ui.refreshable
def _persistence_section() -> None:
    """Persistence block: the active review-DB path, plus browse / new-DB actions.

    The DB *path* is the whole story, so it leads: one text box holding the active
    database, an "available"/"new file" tag telling the user whether that path already
    holds a database, and two quiet icon buttons beside it — browse for an existing
    ``.db``, or spin up a fresh timestamped session DB. Editing the path and pressing
    Enter (or clicking away) applies immediately: an existing file is opened, and a
    session DB Annie created is *renamed* (its data moves with it). There is no
    "session vs existing" mode switch — a config-pinned or env DB simply shows its own
    named path here, and a brand-new config shows a timestamped throwaway.
    """
    ds = _ds()
    active = state.store.db_path
    with ui.card().classes("w-full"), ui.column().classes("w-full gap-3"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("storage", color=theme.PRIMARY)
            ui.label("Persistence").classes("text-lg font-medium")

        _db_path_controls(ds, active)
        _session_db_list(active)


def _db_path_controls(ds: _DatasetState, active: Path) -> None:
    """The active-DB path box with an availability tag, browse, and new-DB buttons."""
    with ui.row().classes("w-full items-center gap-2"):
        path_input = (
            ui.input("Review database", value=str(active))
            .classes("flex-grow")
            .props("dense outlined")
            .tooltip(
                "The database this dataset's review state is saved to. Edit and press "
                "Enter to open another file — or to rename the current session DB."
            )
        )
        path_input.on("blur", lambda: _apply_db_path(path_input.value))
        path_input.on("keydown.enter", lambda: _apply_db_path(path_input.value))

        exists = active.exists()
        ui.badge(
            "available" if exists else "new file",
            color=theme.AVAILABLE if exists else theme.NEUTRAL,
        ).tooltip(
            "A database already exists at this path"
            if exists
            else "No file here yet — it is created on the first review"
        )

        ui.button(icon="folder_open", on_click=_pick_db_file).props("flat dense").tooltip(
            "Browse for an existing .db file"
        )
        ui.button(icon="add", on_click=_new_session_db).props("flat dense").tooltip(
            "Create a fresh timestamped session DB (rename it in the box above)"
        )

    if ds.db_mode == "session":
        ui.label("Rename this session DB by editing the path above.").classes("text-xs").style(
            f"color:{theme.NEUTRAL}"
        )


def _session_db_list(active: Path) -> None:
    """The newest-first list of session databases, click one to switch to it."""
    dbs = _session_dbs()
    if not dbs:
        return
    ui.label("Recent session databases").classes("text-xs").style(f"color:{theme.NEUTRAL}")
    with ui.column().classes("w-full gap-0 max-h-48 overflow-auto"):
        for path in dbs:
            _session_db_row(path, is_active=path == active)


def _session_db_row(path: Path, *, is_active: bool) -> None:
    """One clickable database row in the "use existing" list (active one highlighted)."""
    row = ui.row().classes("w-full items-center gap-2 cursor-pointer p-1 rounded hover:bg-gray-200")
    row.on("click", lambda: _select_existing_db(path))
    with row:
        ui.icon(
            "check_circle" if is_active else "database",
            color=theme.SUCCESS if is_active else theme.NEUTRAL,
        )
        label = ui.label(path.name).classes("text-sm break-all")
        if is_active:
            label.classes("font-medium")


def render() -> None:
    """Build the Dataset tab body; register per-client state."""
    client = context.client
    _dataset_state[client.id] = _DatasetState()
    client.on_disconnect(lambda: _dataset_state.pop(client.id, None))

    with ui.column().classes("w-full gap-4"):
        _config_section()
        _metric_cards()
        _source_list()
        _task_sections()
        _persistence_section()

    # When the app starts, the initial scan runs in the background (see annie.app).
    # Poll until it finishes, then refresh only the metric cards — the source list
    # is intentionally skipped here because rebuilding it calls source.count() for
    # every directory source, which blocks the event loop for ~1.5 s on large datasets.
    # Sources haven't changed, so only aggregate metrics need updating.
    if state.scan is None:

        def _poll_startup_scan() -> None:
            try:
                if state.scan is not None and not _ds().scanning:
                    _metric_cards.refresh()
                    _poll_timer.active = False
            except RuntimeError:
                # Client disconnected before the scan finished; stop quietly.
                _poll_timer.active = False

        _poll_timer = ui.timer(0.5, _poll_startup_scan)
