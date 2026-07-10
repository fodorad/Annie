"""Tests for the protagonist correction service surface."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.core.config import settings
from annie.core.models import BBox, FrameAnnotation
from annie.dataset import corrections
from tests.fixtures import write_participants

HEURISTIC = "participant_face_track_heuristic.csv"
MANUAL = "participant_face_track_heuristic_manual.csv"


class TestCorrectionService(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.heuristic = self.tmp / HEURISTIC

    def test_resolve_uses_explicit_file(self) -> None:
        write_participants(self.heuristic, {"vid": 2})
        self.assertEqual(corrections.resolve_active_track("vid", self.heuristic), 2)

    def test_set_then_resolve(self) -> None:
        corrections.set_active_track("vid", 7, self.heuristic)
        self.assertEqual(corrections.resolve_active_track("vid", self.heuristic), 7)
        self.assertTrue((self.tmp / MANUAL).is_file())

    def test_falls_back_to_configured_file(self) -> None:
        original = settings.participants_file
        settings.participants_file = self.heuristic
        try:
            write_participants(self.heuristic, {"vid": 5})
            self.assertEqual(corrections.resolve_active_track("vid"), 5)
        finally:
            settings.participants_file = original

    def test_unconfigured_file_raises(self) -> None:
        original = settings.participants_file
        settings.participants_file = None
        try:
            with self.assertRaises(ValueError):
                corrections.resolve_active_track("vid")
        finally:
            settings.participants_file = original

    def test_hit_test_frame_delegates(self) -> None:
        annotation = FrameAnnotation(0, [BBox(0, 0, 50, 50, 0.9, track_id=3)])
        self.assertEqual(corrections.hit_test_frame((10, 10), annotation), 3)
        self.assertIsNone(corrections.hit_test_frame((99, 99), annotation))

    def test_export_corrected_writes_resolved_mapping(self) -> None:
        write_participants(self.heuristic, {"vid": 1})
        corrections.set_active_track("vid", 6, self.heuristic)
        out = corrections.export_corrected(self.tmp / "resolved.csv", self.heuristic)
        self.assertTrue(out.is_file())
        self.assertEqual(corrections.resolve_active_track("vid", out), 6)


if __name__ == "__main__":
    unittest.main()
