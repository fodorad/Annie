"""Tests for what a bug report contains, where it goes, and how often it may be sent."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock

from annie.core import bugreport
from annie.core.logbook import LogEvent


def _event(seq: int = 7) -> LogEvent:
    return LogEvent(
        seq, datetime(2026, 10, 12, 17, 3, 41), "error", "Could not decode frames…", "Traceback…"
    )


class TestAccessKey(unittest.TestCase):
    def test_off_without_a_key(self) -> None:
        with (
            mock.patch.object(bugreport, "ACCESS_KEY", ""),
            mock.patch.dict("os.environ", {"ANNIE_REPORT_KEY": ""}),
        ):
            self.assertFalse(bugreport.reporting_available())

    def test_the_built_in_key(self) -> None:
        with (
            mock.patch.object(bugreport, "ACCESS_KEY", "built-in"),
            mock.patch.dict("os.environ", {"ANNIE_REPORT_KEY": ""}),
        ):
            self.assertEqual(bugreport.access_key(), "built-in")
            self.assertTrue(bugreport.reporting_available())

    def test_the_environment_overrides_it(self) -> None:
        with (
            mock.patch.object(bugreport, "ACCESS_KEY", "built-in"),
            mock.patch.dict("os.environ", {"ANNIE_REPORT_KEY": " from-env "}),
        ):
            self.assertEqual(bugreport.access_key(), "from-env")


class TestReportText(unittest.TestCase):
    def test_contains_the_error_its_details_and_versions(self) -> None:
        text = bugreport.build_report(_event(), "", version="1.14.2")
        self.assertIn("Error: Could not decode frames…", text)
        self.assertIn("Time: 2026-10-12 17:03:41", text)
        self.assertIn("Annie: 1.14.2", text)
        self.assertIn("Python: ", text)
        self.assertIn("OS: ", text)
        self.assertTrue(text.endswith("Details:\nTraceback…"))
        self.assertNotIn("What I was doing", text)

    def test_the_users_note_comes_first(self) -> None:
        text = bugreport.build_report(_event(), "  Opened Browse  ", version="1.14.2")
        self.assertTrue(text.startswith("What I was doing:\nOpened Browse\n"))

    def test_an_event_without_details(self) -> None:
        event = LogEvent(1, datetime(2026, 1, 1), "error", "Boom")
        self.assertTrue(bugreport.build_report(event, "", version="1").endswith("(none)"))

    def test_payload_for_the_service(self) -> None:
        with mock.patch.dict("os.environ", {"ANNIE_REPORT_KEY": "k"}):
            body = bugreport.payload("text")
        self.assertEqual(
            body,
            {
                "access_key": "k",
                "subject": "Annie bug report",
                "from_name": "Annie",
                "message": "text",
            },
        )


class TestOncePerErrorAndHourlyLimit(unittest.TestCase):
    def setUp(self) -> None:
        bugreport.reset()
        self.addCleanup(bugreport.reset)

    def test_each_error_is_sent_once(self) -> None:
        self.assertFalse(bugreport.already_sent(7))
        bugreport.mark_sent(7, now=0.0)
        self.assertTrue(bugreport.already_sent(7))
        self.assertFalse(bugreport.already_sent(8))

    def test_at_most_five_per_hour(self) -> None:
        for seq in range(bugreport.MAX_REPORTS_PER_HOUR):
            self.assertTrue(bugreport.can_send(now=100.0 + seq))
            bugreport.mark_sent(seq, now=100.0 + seq)
        self.assertFalse(bugreport.can_send(now=200.0))
        # An hour after the first report, one slot is free again.
        self.assertTrue(bugreport.can_send(now=100.0 + 3600))


if __name__ == "__main__":
    unittest.main()
