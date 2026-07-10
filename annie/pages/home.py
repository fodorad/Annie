"""Home tab — the landing page: brand mark, tagline, and a guide to the tabs.

A static, modern overview that opens by default. Each capability is a card that
jumps to its tab when clicked, so a first-time user can see at a glance what Annie
does and where to start.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from annie.core import theme

if TYPE_CHECKING:
    from collections.abc import Callable

#: One-line product tagline shown under the title on the Home tab.
_TAGLINE = "Local-first browser UI to explore, inspect, and validate a video annotation dataset"

#: ``(tab, icon, accent, title, description)`` for each capability card.
_FEATURES: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "convert",
        "autorenew",
        theme.PRIMARY,
        "Convert",
        "Re-encode your videos and audio into a consistent, torchcodec-validated "
        "format with constant frame rate H.264 and uniform audio. Progress is "
        "tracked live.",
    ),
    (
        "dataset",
        "folder",
        theme.VDET_COLOR,
        "Dataset",
        "Compose your dataset from multiple sources: a videos folder, detection "
        "and track directories, and any number of label or protagonist CSVs. "
        "Coverage metrics update instantly.",
    ),
    (
        "browse",
        "grid_view",
        theme.VIDEO_TAG_COLOR,
        "Browse",
        "Explore your dataset sample by sample. Filter by media presence, review "
        "status, or label values. View the original clip, annotated frame strip, "
        "and a full rendered preview side by side.",
    ),
    (
        "annotator",
        "edit",
        theme.ACCENT,
        "Annotator",
        "Review and correct the protagonist track for each queued video. "
        "Changes are applied and saved instantly, and a corrected CSV can be "
        "exported at any time.",
    ),
    (
        "logs",
        "receipt_long",
        theme.WARNING,
        "Log",
        "Monitor recorded events and errors in real time. Filter by level or "
        "keyword, copy entries to the clipboard, and revisit the full session "
        "log from the dated file on disk.",
    ),
    (
        "settings",
        "settings",
        theme.NEUTRAL,
        "Settings",
        "Adjust row heights for Browse and Annotator, manage the render cache, "
        "and export or import your complete review status as a portable file.",
    ),
)


def _feature_card(
    icon: str, accent: str, title: str, description: str, on_open: Callable[[], None]
) -> None:
    """Render one clickable capability card."""
    card = (
        ui.card()
        .classes(
            "w-[21rem] cursor-pointer gap-2 transition-all duration-200 "
            "hover:-translate-y-0.5 hover:shadow-xl"
        )
        .style("border-radius:14px;align-self:stretch")
    )
    card.on("click", lambda: on_open())
    with card:
        with ui.row().classes("items-center gap-3 no-wrap w-full"):
            with ui.element("div").style(
                f"width:44px;height:44px;border-radius:11px;display:flex;align-items:center;"
                f"justify-content:center;background:{accent}1a"
            ):
                ui.icon(icon, size="1.6rem").style(f"color:{accent}")
            ui.label(title).classes("text-lg font-medium flex-grow")
            ui.icon("arrow_forward", size="1.1rem").style(f"color:{theme.NEUTRAL}")
        ui.label(description).classes("text-sm leading-snug").style(f"color:{theme.NEUTRAL}")


def render(navigate: Callable[[str], None]) -> None:
    """Build the Home tab body.

    Args:
        navigate: Switches the active tab to the given tab name (card clicks).
    """
    with ui.column().classes("w-full items-center gap-6").style("padding:1.5rem 0 2.5rem"):
        # ── hero ────────────────────────────────────────────────────────────────
        with ui.column().classes("items-center gap-2"):
            ui.html(theme.LOGO_MARK_SVG).classes("w-20 h-20")
            ui.label("Annie").classes("text-4xl font-bold").style(f"color:{theme.PRIMARY}")
            ui.label(_TAGLINE).classes("text-base text-center").style(
                f"color:{theme.NEUTRAL};max-width:38rem"
            )

        # ── quick start hint ────────────────────────────────────────────────────
        with ui.row().classes("items-center gap-2 text-sm").style(f"color:{theme.NEUTRAL}"):
            ui.icon("bolt", size="1.1rem").style(f"color:{theme.ACCENT}")
            ui.label(
                "New here? Convert your videos for consistency, define a Dataset, "
                "then Browse and review."
            )

        # ── capability cards ────────────────────────────────────────────────────
        ui.label("What you can do").classes("text-lg font-medium")
        with ui.element("div").style(
            "max-width:72rem;display:flex;flex-wrap:wrap;justify-content:center;"
            "align-items:stretch;gap:1rem"
        ):
            for tab, icon, accent, title, description in _FEATURES:
                _feature_card(icon, accent, title, description, lambda t=tab: navigate(t))
