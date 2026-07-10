"""Tests for the participant parser and protagonist resolution."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.parsers.participants import (
    NO_ACTIVE_TRACK,
    export_resolved,
    load_participants,
    manual_path_for,
    resolve_active_track,
    resolved_mapping,
    set_active_track,
)
from tests.fixtures import write_participants, write_table

HEURISTIC = "participant_face_track_heuristic.csv"
MANUAL = "participant_face_track_heuristic_manual.csv"


class TestLoadParticipants(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_missing_file_is_empty_mapping(self) -> None:
        self.assertEqual(load_participants(self.tmp / "nope.csv"), {})

    def test_parses_uuid_and_negative_one(self) -> None:
        path = write_participants(self.tmp / "p.csv", {"a_1": 0, "b_2": -1, "c_3": 5})
        self.assertEqual(load_participants(path), {"a_1": 0, "b_2": -1, "c_3": 5})


class TestManualPathFor(unittest.TestCase):
    def test_derives_manual_sibling(self) -> None:
        self.assertEqual(manual_path_for(f"/x/{HEURISTIC}"), Path(f"/x/{MANUAL}"))


class TestResolveActiveTrack(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.heuristic = self.tmp / HEURISTIC

    def test_falls_back_to_minus_one_with_no_files(self) -> None:
        self.assertEqual(resolve_active_track("vid", self.heuristic), NO_ACTIVE_TRACK)

    def test_uses_heuristic_when_no_manual(self) -> None:
        write_participants(self.heuristic, {"vid": 3})
        self.assertEqual(resolve_active_track("vid", self.heuristic), 3)

    def test_manual_wins_over_heuristic(self) -> None:
        write_participants(self.heuristic, {"vid": 3})
        write_participants(self.tmp / MANUAL, {"vid": 8})
        self.assertEqual(resolve_active_track("vid", self.heuristic), 8)

    def test_resolved_mapping_layers_manual_over_heuristic(self) -> None:
        write_participants(self.heuristic, {"a": 1, "b": 2})
        write_participants(self.tmp / MANUAL, {"b": 9})
        self.assertEqual(resolved_mapping(self.heuristic), {"a": 1, "b": 9})


class TestSetActiveTrack(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.heuristic = self.tmp / HEURISTIC

    def test_writes_manual_file_only(self) -> None:
        write_participants(self.heuristic, {"vid": 3})
        before = self.heuristic.read_text(encoding="utf-8")
        set_active_track("vid", 9, self.heuristic)
        self.assertTrue((self.tmp / MANUAL).is_file())
        self.assertEqual(self.heuristic.read_text(encoding="utf-8"), before)  # untouched

    def test_upsert_overwrites_previous_correction(self) -> None:
        set_active_track("vid", 1, self.heuristic)
        set_active_track("vid", 2, self.heuristic)
        mapping = load_participants(self.tmp / MANUAL)
        self.assertEqual(mapping["vid"], 2)
        self.assertEqual(len(mapping), 1)  # no duplicate row

    def test_resolution_reflects_correction(self) -> None:
        write_participants(self.heuristic, {"vid": 0})
        set_active_track("vid", 4, self.heuristic)
        self.assertEqual(resolve_active_track("vid", self.heuristic), 4)


class TestCustomColumns(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.source = write_table(
            self.tmp / "main.csv",
            ["video", "main_track"],
            [{"video": "vid", "main_track": "2"}],
        )

    def test_load_and_resolve_with_custom_columns(self) -> None:
        self.assertEqual(load_participants(self.source, "video", "main_track"), {"vid": 2})
        self.assertEqual(resolve_active_track("vid", self.source, "video", "main_track"), 2)

    def test_set_and_resolve_with_custom_columns(self) -> None:
        set_active_track("vid", 5, self.source, "video", "main_track")
        self.assertEqual(resolve_active_track("vid", self.source, "video", "main_track"), 5)

    def test_export_resolved_layers_manual(self) -> None:
        set_active_track("vid", 7, self.source, "video", "main_track")
        out = export_resolved(self.source, self.tmp / "out.csv", "video", "main_track")
        self.assertEqual(load_participants(out, "video", "main_track"), {"vid": 7})


if __name__ == "__main__":
    unittest.main()
