"""Tests for the Log tab's Report button: sent once, limited, and shown as Sent after."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock

from nicegui import ui

from annie.core import bugreport
from annie.core.logbook import LogEvent
from annie.pages import logs
from tests.pages._nicegui import ui_client


def _error(seq: int = 3) -> LogEvent:
    return LogEvent(seq, datetime(2026, 10, 12, 17, 3), "error", "Could not decode frames…", "tb")


class _ReportCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        bugreport.reset()
        self.addCleanup(bugreport.reset)
        patch = mock.patch.dict("os.environ", {"ANNIE_REPORT_KEY": "test-key"})
        patch.start()
        self.addCleanup(patch.stop)


class TestSend(_ReportCase):
    async def test_a_sent_report_is_remembered(self) -> None:
        post = mock.AsyncMock(return_value=(True, ""))
        with mock.patch.object(logs, "_post_report", post):
            sent, message = await logs._send(_error(), "Opened Browse")
        self.assertTrue(sent)
        self.assertEqual(message, "Report sent — thank you!")
        self.assertTrue(bugreport.already_sent(3))
        text = post.await_args.args[0]
        self.assertIn("What I was doing:\nOpened Browse", text)
        self.assertIn("Error: Could not decode frames…", text)

    async def test_the_same_error_is_never_sent_twice(self) -> None:
        post = mock.AsyncMock(return_value=(True, ""))
        with mock.patch.object(logs, "_post_report", post):
            await logs._send(_error(), "")
            sent, message = await logs._send(_error(), "")
        self.assertTrue(sent)
        self.assertEqual(message, "This error was already reported.")
        self.assertEqual(post.await_count, 1)

    async def test_a_failed_send_can_be_retried(self) -> None:
        post = mock.AsyncMock(return_value=(False, "TypeError: Failed to fetch"))
        with mock.patch.object(logs, "_post_report", post):
            sent, message = await logs._send(_error(), "")
        self.assertFalse(sent)
        self.assertEqual(message, "The report could not be sent: TypeError: Failed to fetch")
        self.assertFalse(bugreport.already_sent(3))

    async def test_the_hourly_limit_stops_sending(self) -> None:
        for seq in range(bugreport.MAX_REPORTS_PER_HOUR):
            bugreport.mark_sent(100 + seq)
        post = mock.AsyncMock(return_value=(True, ""))
        with mock.patch.object(logs, "_post_report", post):
            sent, message = await logs._send(_error(), "")
        self.assertFalse(sent)
        self.assertIn("Too many reports", message)
        post.assert_not_awaited()


def _buttons(client: object) -> list[ui.button]:
    return [e for e in client.elements.values() if isinstance(e, ui.button)]  # type: ignore[attr-defined]


class TestReportControl(_ReportCase):
    def test_an_error_gets_a_report_button(self) -> None:
        with ui_client() as client:
            logs._report_control(_error())
            labels = [b.text for b in _buttons(client)]
        self.assertEqual(labels, ["Report"])

    def test_a_reported_error_shows_a_disabled_sent(self) -> None:
        bugreport.mark_sent(3)
        with ui_client() as client:
            logs._report_control(_error())
            (button,) = _buttons(client)
        self.assertEqual(button.text, "Sent")
        self.assertIn("disable", button.props)

    def test_no_button_for_warnings_or_without_a_key(self) -> None:
        warning = LogEvent(4, datetime(2026, 10, 12), "warning", "slow")
        with ui_client() as client:
            logs._report_control(warning)
            self.assertEqual(_buttons(client), [])
        with (
            mock.patch.dict("os.environ", {"ANNIE_REPORT_KEY": ""}),
            mock.patch.object(bugreport, "ACCESS_KEY", ""),
            ui_client() as client,
        ):
            logs._report_control(_error())
            self.assertEqual(_buttons(client), [])


if __name__ == "__main__":
    unittest.main()
