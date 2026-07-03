"""Settings tab — UI preferences, render cache, review export/import, maintenance.

Surfaces the session-only UI preferences (Browse / Annotator row height), the
render-cache controls, the review-status export/import actions, and a Maintenance
block that lets you reclaim disk space by removing old logs, session databases, and
rendered clips.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import ui

from annie.core import logbook, theme
from annie.core.config import settings
from annie.core.state import state
from annie.pages import annotator, browse

if TYPE_CHECKING:
    from collections.abc import Callable

# ── helpers ───────────────────────────────────────────────────────────────────


def _human_size(n_bytes: int) -> str:
    """Format a byte count as a human-readable string."""
    if n_bytes < 1024:
        return f"{n_bytes} B"
    if n_bytes < 1024**2:
        return f"{n_bytes / 1024:.1f} KB"
    return f"{n_bytes / 1024**2:.1f} MB"


def _dir_size(directory: Path) -> int:
    """Sum of all file sizes (non-recursive) in ``directory``, 0 if missing."""
    if not directory.is_dir():
        return 0
    return sum(f.stat().st_size for f in directory.iterdir() if f.is_file())


def _old_logs() -> list[Path]:
    """Log files in logs_dir that are not the current session's log."""
    if not settings.logs_dir.is_dir():
        return []
    current = logbook.LOG.log_path
    return [
        f
        for f in settings.logs_dir.iterdir()
        if f.is_file() and f.suffix == ".log" and f != current
    ]


def _old_session_dbs() -> list[Path]:
    """Session DB files that are not the currently active database."""
    if not settings.sessions_dir.is_dir():
        return []
    active = state.store.db_path.resolve()
    return [
        f
        for f in settings.sessions_dir.iterdir()
        if f.is_file() and f.suffix == ".db" and f.resolve() != active
    ]


# ── maintenance actions ───────────────────────────────────────────────────────


def _clear_logs() -> str:
    """Delete old log files and return a summary string."""
    targets = _old_logs()
    freed = sum(f.stat().st_size for f in targets)
    for f in targets:
        f.unlink(missing_ok=True)
    return f"Deleted {len(targets)} log file(s) — {_human_size(freed)} freed"


def _clear_session_dbs() -> str:
    """Delete old session DB files and return a summary string."""
    targets = _old_session_dbs()
    freed = sum(f.stat().st_size for f in targets)
    for f in targets:
        f.unlink(missing_ok=True)
    return f"Deleted {len(targets)} session DB(s) — {_human_size(freed)} freed"


def _clear_render_cache() -> str:
    """Delete all render clips, reset UI render boxes, return summary."""
    _jobs, files = state.renderer.clear_all()
    browse.refresh()
    annotator.refresh()
    return f"Cleared {files} rendered clip(s) — render boxes reset to default"


# ── UI ────────────────────────────────────────────────────────────────────────


@ui.refreshable
def _maintenance_section() -> None:
    """Maintenance card: space summary + targeted cleanup buttons."""
    old_logs = _old_logs()
    old_dbs = _old_session_dbs()
    log_size = sum(f.stat().st_size for f in old_logs)
    db_size = sum(f.stat().st_size for f in old_dbs)
    render_size = _dir_size(settings.temp_dir)

    with ui.card().classes("w-full"):
        with ui.column().classes("w-full gap-3"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("build", color=theme.PRIMARY)
                ui.label("Maintenance").classes("font-medium")

            # ── space summary ─────────────────────────────────────────────
            with ui.column().classes("w-full gap-1"):
                ui.label("Reclaimable space").classes("text-sm").style(f"color:{theme.NEUTRAL}")
                rows = [
                    (
                        "receipt_long",
                        f"Old log files ({len(old_logs)})",
                        _human_size(log_size),
                        settings.logs_dir,
                    ),
                    (
                        "storage",
                        f"Old session databases ({len(old_dbs)})",
                        _human_size(db_size),
                        settings.sessions_dir,
                    ),
                    (
                        "movie",
                        "Rendered clips (temp dir)",
                        _human_size(render_size),
                        settings.temp_dir,
                    ),
                ]
                for icon, label, size, path in rows:
                    with ui.row().classes("items-center gap-2 w-full"):
                        ui.icon(icon, size="sm").style(f"color:{theme.NEUTRAL}")
                        ui.label(label).classes("text-sm flex-grow")
                        ui.label(size).classes("text-sm font-mono").style(f"color:{theme.NEUTRAL}")
                    ui.label(str(path)).classes("text-xs ml-6 break-all").style(
                        f"color:{theme.NEUTRAL}"
                    )

            ttl = (
                ui.number(
                    "Auto-delete clips older than (seconds)",
                    value=settings.temp_ttl_seconds,
                    min=0,
                )
                .classes("w-72")
                .props("dense")
            )
            ttl.on("blur", lambda _: setattr(settings, "temp_ttl_seconds", int(ttl.value or 0)))

            ui.separator()

            # ── individual actions ────────────────────────────────────────
            def _run(action_fn: Callable[[], str], label: str = "") -> None:  # noqa: ARG001
                msg = action_fn()
                logbook.report(msg, level="info")
                ui.notify(msg, color=theme.PRIMARY)
                _maintenance_section.refresh()

            with ui.row().classes("items-center gap-2 flex-wrap"):
                ui.button(
                    "Delete old logs",
                    icon="receipt_long",
                    on_click=lambda: _run(_clear_logs, "logs"),
                ).props("flat").tooltip(
                    f"Remove {len(old_logs)} log file(s) from {settings.logs_dir}"
                ).set_enabled(bool(old_logs))

                ui.button(
                    "Delete old session DBs",
                    icon="storage",
                    on_click=lambda: _run(_clear_session_dbs, "dbs"),
                ).props("flat").tooltip(
                    f"Remove {len(old_dbs)} session database(s) from {settings.sessions_dir}"
                ).set_enabled(bool(old_dbs))

                ui.button(
                    "Clear render cache",
                    icon="movie",
                    on_click=lambda: _run(_clear_render_cache, "renders"),
                ).props("flat").tooltip(
                    "Delete all rendered clips and reset Browse/Annotator render boxes"
                ).set_enabled(render_size > 0)

            ui.separator()

            total = log_size + db_size + render_size

            def _clean_all() -> None:
                parts = []
                if old_logs:
                    parts.append(_clear_logs())
                if old_dbs:
                    parts.append(_clear_session_dbs())
                if render_size > 0:
                    parts.append(_clear_render_cache())
                msg = " · ".join(parts) if parts else "Nothing to clean up."
                logbook.report(msg, level="info")
                ui.notify(msg, color=theme.PRIMARY)
                _maintenance_section.refresh()

            ui.button(
                f"Clean up all  ({_human_size(total)})",
                icon="cleaning_services",
                on_click=_clean_all,
            ).props("unelevated").set_enabled(total > 0)


def render() -> None:
    """Build the Settings tab body."""
    with ui.column().classes("w-full gap-3"):
        ui.label("Settings").classes("text-xl font-medium")

        with ui.card().classes("w-full"):
            ui.label("Layout").classes("font-medium")
            ui.label("Row height sizes every thumbnail, strip frame, and render box.").classes(
                "text-xs"
            ).style(f"color:{theme.NEUTRAL}")

            browse_h = ui.number(
                "Browse row height (px)",
                value=state.ui.browse_row_height,
                min=72,
                max=400,
                step=15,
            ).classes("w-64")

            def set_browse_height() -> None:
                state.ui.browse_row_height = int(browse_h.value or state.ui.browse_row_height)
                browse.refresh()

            browse_h.on("blur", lambda _: set_browse_height())

            annot_h = ui.number(
                "Annotator row max height (px)",
                value=state.ui.annotator_row_height,
                min=72,
                max=600,
                step=15,
            ).classes("w-64")

            def set_annot_height() -> None:
                state.ui.annotator_row_height = int(annot_h.value or state.ui.annotator_row_height)
                annotator.refresh()

            annot_h.on("blur", lambda _: set_annot_height())

        with ui.card().classes("w-full"):
            ui.label("Review status export / import").classes("font-medium")
            ui.label(f"Database: {state.store.db_path}").classes("text-xs").style(
                f"color:{theme.NEUTRAL}"
            )

            def export_json() -> None:
                out = state.store.export_json(Path(settings.temp_dir) / "annie_review.json")
                ui.notify(f"Exported to {out}", color=theme.PRIMARY)

            def export_csv() -> None:
                out = state.store.export_csv(Path(settings.temp_dir) / "annie_review.csv")
                ui.notify(f"Exported to {out}", color=theme.PRIMARY)

            with ui.row().classes("gap-2"):
                ui.button("Export JSON", icon="download", on_click=export_json).props("flat")
                ui.button("Export CSV", icon="download", on_click=export_csv).props("flat")

        _maintenance_section()

        with ui.card().classes("w-full"):
            ui.label("About").classes("font-medium")
            ui.label("Annie — by Ádám Fodor").classes("text-sm")
            with ui.row().classes("items-center gap-2"):
                ui.icon("bug_report", color=theme.PRIMARY)
                ui.link(
                    "Report an issue", "https://github.com/fodorad/Annie/issues", new_tab=True
                ).classes("text-sm")
            with ui.row().classes("items-center gap-2"):
                ui.icon("mail", color=theme.PRIMARY)
                ui.link("fodorad201@gmail.com", "mailto:fodorad201@gmail.com").classes("text-sm")
