"""Dataset tab — define the dataset by adding data sources; see live metrics.

Annie is dataset-agnostic: instead of fixed folder fields, you build the dataset
from a list of **data sources** (the ``+`` box). A videos folder is the mandatory
spine; vdet/track folders, a main-character CSV, and any number of label CSVs
attach to it. There is no Scan button — adding or removing a source re-scans in
place, and every metric and Browse row updates immediately. Each source shows an
Available / Unavailable chip and a live item count.

Each browser tab maintains its own scan-in-progress flag and database-mode
selection via a per-client :class:`_DatasetState` keyed by
:attr:`nicegui.Client.id`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nicegui import context, run, ui

from annie.core import logbook, theme
from annie.core.config import settings
from annie.core.state import _seed_registry, state
from annie.dataset import datasets
from annie.dataset.sources import KIND_ICONS, KIND_LABELS, DataSource, SourceKind, SourceRegistry
from annie.pages import annotator, browse
from annie.pages.csv_dialog import configure_csv
from annie.pages.folder_picker import pick_directory, pick_file

# ── per-client state ──────────────────────────────────────────────────────────


@dataclass
class _DatasetState:
    """Scan + persistence state isolated to one browser tab.

    Attributes:
        scanning: Whether a rescan is in progress for this client.
        db_mode: ``"session"`` (fresh per-startup DB) or ``"existing"`` (pinned file).
        db_path_custom: The user-typed or config-embedded DB path when mode is ``"existing"``.
    """

    scanning: bool = False
    db_mode: str = "existing" if settings.db_path_is_explicit else "session"
    db_path_custom: str = str(settings.db_path) if settings.db_path_is_explicit else ""


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
    browse.refresh()
    annotator.update_availability()


async def _rescan() -> None:
    """Re-walk the configured folders (e.g. to pick up newly-converted files)."""
    await _apply_changes()
    counts = state.scan.counts if state.scan is not None else {}
    ui.notify(f"Rescanned — {counts.get('num_videos', 0)} videos", color=theme.PRIMARY)


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
    """Reset the dataset to a blank slate: no sources, fresh session DB."""
    ds = _ds()
    state.registry = SourceRegistry()
    state.audio_cache.clear()
    state.frames_cache.clear()
    ds.db_mode = "session"
    ds.db_path_custom = ""
    state.set_store(settings.db_path)
    await _apply_changes()
    _persistence_section.refresh()
    ui.notify("New config", color=theme.PRIMARY)


async def _env_config() -> None:
    """Reload sources from the ANNIE_* environment variables."""
    ds = _ds()
    state.registry = _seed_registry()
    state.audio_cache.clear()
    state.frames_cache.clear()
    ds.db_mode = "existing" if settings.db_path_is_explicit else "session"
    ds.db_path_custom = str(settings.db_path) if settings.db_path_is_explicit else ""
    state.set_store(settings.db_path)
    await _apply_changes()
    _persistence_section.refresh()
    ui.notify("Loaded from environment variables", color=theme.PRIMARY)


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
    out = Path(folder) / "annie_dataset.json"
    db_to_save = Path(ds.db_path_custom) if ds.db_mode == "existing" and ds.db_path_custom else None
    datasets.save_config(out, state.registry, config_name, relative_to=folder, db_path=db_to_save)
    ui.notify(f"Saved '{config_name}' to {out}", color=theme.PRIMARY)


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
    if kind is SourceKind.CSV:
        chosen = await pick_file(settings.participants_file or settings.labels_csv)
        if not chosen:
            return
        source = await configure_csv(chosen, _video_stems())
        if source is None:
            return
    else:
        start = {
            SourceKind.VIDEO: settings.videos_dir,
            SourceKind.VDET: settings.vdet_dir,
            SourceKind.TRACK: settings.track_dir,
        }.get(kind)
        chosen = await pick_directory(start)
        if not chosen:
            return
        source = DataSource(kind, Path(chosen))
    state.registry.add(source)
    await _apply_changes()


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


# ── persistence block ─────────────────────────────────────────────────────────


async def _on_db_mode_change(mode: str) -> None:
    """Switch between session-DB and existing-DB modes."""
    ds = _ds()
    ds.db_mode = mode
    if mode == "session":
        state.set_store(settings.db_path)
    _persistence_section.refresh()


async def _apply_db_path(path_str: str) -> None:
    """Open an existing DB at ``path_str`` and switch the store to it."""
    ds = _ds()
    path_str = path_str.strip()
    if not path_str:
        ui.notify("Enter a database file path.", color=theme.WARNING)
        return
    ds.db_path_custom = path_str
    state.set_store(Path(path_str))
    ui.notify(f"Review DB: {path_str}", color=theme.PRIMARY)
    _persistence_section.refresh()


async def _pick_db_file() -> None:
    """Open a file picker to select an existing DB, then apply it."""
    ds = _ds()
    chosen = await pick_file(settings.sessions_dir)
    if chosen:
        ds.db_mode = "existing"
        ds.db_path_custom = str(chosen)
        state.set_store(Path(chosen))
        _persistence_section.refresh()


@ui.refreshable
def _persistence_section() -> None:
    """Persistence block: choose between a fresh session DB or a pinned existing file."""
    ds = _ds()
    with ui.card().classes("w-full"):
        with ui.column().classes("w-full gap-3"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("storage", color=theme.PRIMARY)
                ui.label("Persistence").classes("text-lg font-medium")

            ui.label(f"Active DB: {state.store.db_path}").classes("text-xs break-all").style(
                f"color:{theme.NEUTRAL}"
            )

            ui.toggle(
                {"session": "New session DB", "existing": "Use existing DB"},
                value=ds.db_mode,
                on_change=lambda e: _on_db_mode_change(e.value),
            )

            if ds.db_mode == "existing":
                with ui.row().classes("w-full items-center gap-2"):
                    path_input = (
                        ui.input("Database file path", value=ds.db_path_custom)
                        .classes("flex-grow")
                        .props("dense outlined")
                    )
                    ui.button(icon="folder_open", on_click=_pick_db_file).props(
                        "flat dense"
                    ).tooltip("Browse for a .db file")
                    ui.button(
                        "Apply", icon="check", on_click=lambda: _apply_db_path(path_input.value)
                    ).props("unelevated dense")


def render() -> None:
    """Build the Dataset tab body; register per-client state."""
    client = context.client
    _dataset_state[client.id] = _DatasetState()
    client.on_disconnect(lambda: _dataset_state.pop(client.id, None))

    with ui.column().classes("w-full gap-4"):
        ui.label("Dataset").classes("text-xl font-medium")
        _config_section()
        _metric_cards()
        _source_list()
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
