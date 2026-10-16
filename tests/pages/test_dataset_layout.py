"""Tests for the Dataset tab's Layout inputs (pure parsing; the widgets are browser-gated)."""

from __future__ import annotations

import unittest
from pathlib import Path

from annie.core.models import VideoEntry
from annie.dataset.layout import VideoLayout
from annie.dataset.scanning import ScanResult
from annie.dataset.sources import DataSource, SourceKind
from annie.pages.dataset import LayoutCommit, layout_commit, layout_from_input, layout_summary


class TestLayoutFromInput(unittest.TestCase):
    def test_trims_the_pattern(self) -> None:
        self.assertEqual(layout_from_input("  {a}/clip_{b}.mp4 ").pattern, "{a}/clip_{b}.mp4")

    def test_invalid_input_raises_a_readable_error(self) -> None:
        for pattern in ("", "plain.mp4", "{a}/{a}.mp4"):
            with self.assertRaises(ValueError):
                layout_from_input(pattern)


class TestLayoutCommit(unittest.TestCase):
    def test_valid_new_layout_is_applied(self) -> None:
        outcome = layout_commit("{a}/clip_{b}.mp4", None)
        self.assertEqual(outcome.layout, VideoLayout("{a}/clip_{b}.mp4"))
        self.assertIsNone(outcome.error)

    def test_unchanged_layout_does_nothing(self) -> None:
        current = VideoLayout("{a}.mp4")
        self.assertEqual(layout_commit(" {a}.mp4 ", current), LayoutCommit())

    def test_invalid_layout_reports_and_applies_nothing(self) -> None:
        outcome = layout_commit("plain.mp4", None)
        self.assertIsNone(outcome.layout)
        self.assertIn("placeholder", outcome.error or "")

    def test_empty_pattern_is_not_an_error_yet(self) -> None:
        self.assertEqual(layout_commit("  ", None), LayoutCommit())


class TestLayoutSummary(unittest.TestCase):
    def test_summarises_the_last_scan(self) -> None:
        source = DataSource(SourceKind.VIDEO, Path("."), layout=VideoLayout("{g}/{p}.mp4"))
        scan = ScanResult(
            entries=[
                VideoEntry("g1_x", Path("g1/x.mp4"), labels={"g": "g1", "p": "x"}),
                VideoEntry("g2_x", Path("g2/x.mp4"), labels={"g": "g2", "p": "x"}),
            ],
            layout_collisions=[],
        )
        text = layout_summary(source, scan) or ""
        self.assertIn("2 videos", text)
        self.assertIn("g ×2", text)
        self.assertIn("p ×1", text)

    def test_none_without_layout_or_scan(self) -> None:
        flat = DataSource(SourceKind.VIDEO, Path("."))
        self.assertIsNone(layout_summary(flat, ScanResult()))
        nested = DataSource(SourceKind.VIDEO, Path("."), layout=VideoLayout("{g}.mp4"))
        self.assertIsNone(layout_summary(nested, None))


if __name__ == "__main__":
    unittest.main()
