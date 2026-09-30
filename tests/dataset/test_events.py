"""Tests for the Event-annotation domain: frame/second maths and JSON/CSV export."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from annie.dataset.events import (
    build_export_tree,
    clamp_event,
    export_events_csv,
    export_events_json,
    frames_to_seconds,
    seconds_to_frame,
)
from annie.dataset.storage import EventRecord


def _event(
    video_id: str,
    track: str,
    start: int,
    end: int,
    *,
    label: str = "",
    note: str = "",
    attributes: dict[str, str] | None = None,
    color: str | None = None,
) -> EventRecord:
    return EventRecord(
        event_id=f"{video_id}-{track}-{start}",
        video_id=video_id,
        row_key=f"{video_id}::",
        track=track,
        start_frame=start,
        end_frame=end,
        label=label,
        note=note,
        attributes=attributes or {},
        color=color,
        updated_at="2020-01-01T00:00:00",
    )


class TestFrameSeconds(unittest.TestCase):
    def test_round_trip_at_common_fps(self) -> None:
        for fps in (25.0, 30.0, 23.976):
            for frame in (0, 1, 100, 4321):
                sec = frames_to_seconds(frame, fps)
                self.assertEqual(seconds_to_frame(sec, fps), frame)

    def test_zero_fps_degrades_safely(self) -> None:
        self.assertEqual(frames_to_seconds(100, 0.0), 0.0)
        self.assertEqual(seconds_to_frame(4.0, 0.0), 0)

    def test_seconds_to_frame_rounds_to_nearest(self) -> None:
        self.assertEqual(seconds_to_frame(0.041, 25.0), 1)  # 1.025 frames → 1
        self.assertEqual(seconds_to_frame(0.055, 25.0), 1)  # 1.375 frames → 1
        self.assertEqual(seconds_to_frame(0.061, 25.0), 2)  # 1.525 frames → 2
        self.assertEqual(seconds_to_frame(-1.0, 25.0), 0)  # never negative


class TestClampEvent(unittest.TestCase):
    def test_orders_reversed_pair(self) -> None:
        self.assertEqual(clamp_event(40, 10, 100), (10, 40))

    def test_bounds_to_last_frame(self) -> None:
        self.assertEqual(clamp_event(90, 200, 100), (90, 99))

    def test_clamps_negative_to_zero(self) -> None:
        self.assertEqual(clamp_event(-5, 10, 100), (0, 10))

    def test_unknown_frame_count_only_clamps_lower(self) -> None:
        # Before metadata loads, an event may still be authored past any upper bound.
        self.assertEqual(clamp_event(-3, 5000, 0), (0, 5000))


class TestExportTree(unittest.TestCase):
    def test_groups_by_video_and_track_with_seconds(self) -> None:
        events = [
            _event("v", "speech", 0, 25, label="A"),
            _event("v", "gesture", 50, 75),
        ]
        tree = build_export_tree(events, {"v": 25.0})
        self.assertEqual(tree["v"]["fps"], 25.0)
        tracks = tree["v"]["tracks"]
        self.assertEqual(set(tracks), {"speech", "gesture"})
        speech = tracks["speech"][0]
        self.assertEqual((speech["start_sec"], speech["end_sec"]), (0.0, 1.0))
        self.assertEqual(speech["label"], "A")

    def test_missing_fps_defaults_to_zero(self) -> None:
        tree = build_export_tree([_event("v", "speech", 0, 25)], {})
        self.assertEqual(tree["v"]["fps"], 0.0)
        self.assertEqual(tree["v"]["tracks"]["speech"][0]["start_sec"], 0.0)

    def test_participants_group_by_category_name(self) -> None:
        # The category name *is* the track, so exports already separate participants.
        events = [
            _event("v", "Mother", 0, 25, label="smile"),
            _event("v", "Baby", 50, 75, label="coo"),
        ]
        tree = build_export_tree(events, {"v": 25.0})
        self.assertEqual(set(tree["v"]["tracks"]), {"Mother", "Baby"})


class TestExportJson(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_json_nests_attributes(self) -> None:
        events = [_event("v", "speech", 0, 25, attributes={"speaker": "A"})]
        path = export_events_json(events, {"v": 25.0}, self.tmp / "out.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        event = data["v"]["tracks"]["speech"][0]
        self.assertEqual(event["attributes"], {"speaker": "A"})

    def test_session_json_spans_multiple_videos(self) -> None:
        events = [_event("v", "speech", 0, 5), _event("w", "gesture", 0, 5)]
        path = export_events_json(events, {"v": 25.0, "w": 30.0}, self.tmp / "s.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"v", "w"})


class TestExportCsv(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def _rows(self, path: Path) -> list[dict[str, str]]:
        with path.open(encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_flattens_attributes_as_columns(self) -> None:
        events = [
            _event("v", "speech", 0, 25, attributes={"speaker": "A", "intensity": "high"}),
            _event("v", "speech", 30, 50, attributes={"speaker": "B"}),  # no intensity
        ]
        path = export_events_csv(events, {"v": 25.0}, self.tmp / "out.csv")
        rows = self._rows(path)
        self.assertEqual(rows[0]["attr_speaker"], "A")
        self.assertEqual(rows[0]["attr_intensity"], "high")
        # The second event lacks intensity → its column is present but empty (union header).
        self.assertEqual(rows[1]["attr_speaker"], "B")
        self.assertEqual(rows[1]["attr_intensity"], "")

    def test_fixed_columns_and_seconds(self) -> None:
        path = export_events_csv([_event("v", "speech", 0, 25)], {"v": 25.0}, self.tmp / "c.csv")
        row = self._rows(path)[0]
        self.assertEqual(row["video_id"], "v")
        self.assertEqual(row["track"], "speech")
        self.assertEqual(row["start_frame"], "0")
        self.assertEqual(row["end_sec"], "1.0")

    def test_no_attributes_yields_only_fixed_columns(self) -> None:
        path = export_events_csv([_event("v", "speech", 0, 5)], {"v": 25.0}, self.tmp / "n.csv")
        header = self._rows(path)[0].keys()
        self.assertNotIn("attr_", "".join(header))

    def test_color_column_present(self) -> None:
        events = [
            _event("v", "speech", 0, 5, color="#a8dadc"),
            _event("v", "speech", 10, 15),  # no colour → empty cell
        ]
        rows = self._rows(export_events_csv(events, {"v": 25.0}, self.tmp / "col.csv"))
        self.assertEqual(rows[0]["color"], "#a8dadc")
        self.assertEqual(rows[1]["color"], "")


if __name__ == "__main__":
    unittest.main()
