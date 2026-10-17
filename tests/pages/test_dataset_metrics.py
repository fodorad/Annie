"""Tests for which overview metric cards the Dataset tab shows."""

from __future__ import annotations

import unittest
from pathlib import Path

from annie.dataset.sources import DataSource, SourceKind, SourceRegistry
from annie.pages import dataset
from annie.pages.dataset import MetricCard, metric_rows
from tests.pages._nicegui import ui_client


def _keys(registry: SourceRegistry) -> list[list[str]]:
    return [[card.key for card in row] for row in metric_rows(registry)]


def _registry(*kinds: SourceKind) -> SourceRegistry:
    registry = SourceRegistry()
    for kind in kinds:
        registry.add(DataSource(kind, Path(kind.value)))
    return registry


class TestMetricRows(unittest.TestCase):
    def test_videos_only_shows_just_the_video_count(self) -> None:
        self.assertEqual(_keys(_registry(SourceKind.VIDEO)), [["num_videos"]])

    def test_no_sources_still_shows_the_video_count(self) -> None:
        self.assertEqual(_keys(SourceRegistry()), [["num_videos"]])

    def test_vdet_adds_its_count_and_coverage(self) -> None:
        self.assertEqual(
            _keys(_registry(SourceKind.VIDEO, SourceKind.VDET)),
            [["num_videos", "num_vdet_files"], ["videos_with_vdet"]],
        )

    def test_track_alone_has_no_combined_card(self) -> None:
        self.assertEqual(
            _keys(_registry(SourceKind.VIDEO, SourceKind.TRACK)),
            [["num_videos", "num_track_files"], ["videos_with_track"]],
        )

    def test_vdet_and_track_show_every_card(self) -> None:
        self.assertEqual(
            _keys(_registry(SourceKind.VIDEO, SourceKind.VDET, SourceKind.TRACK)),
            [
                ["num_videos", "num_vdet_files", "num_track_files"],
                ["videos_vdet_and_track", "videos_with_vdet", "videos_with_track"],
            ],
        )


class TestMetricCard(unittest.TestCase):
    def _tags(self, value: int | None) -> list[str]:
        with ui_client() as client:
            dataset._metric_card(MetricCard("# videos", "num_videos", "#000"), value)  # noqa: SLF001
            return [el.tag for el in client.layout.descendants()]

    def test_scanning_card_spins(self) -> None:
        self.assertIn("q-spinner", self._tags(None))

    def test_scanned_card_shows_its_value(self) -> None:
        with ui_client() as client:
            dataset._metric_card(MetricCard("# videos", "num_videos", "#000"), 12)  # noqa: SLF001
            texts = [getattr(el, "text", "") for el in client.layout.descendants()]
        self.assertIn("12", texts)
        self.assertNotIn("q-spinner", self._tags(12))


if __name__ == "__main__":
    unittest.main()
