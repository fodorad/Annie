"""Tests for the Log tab's Report button: sent once, limited, and shown as Sent after."""

from __future__ import annotations

import unittest
from datetime import datetime

from nicegui import ui

from annie.core import bugreport
from annie.core.logbook import LogEvent
from annie.pages import logs
from tests.fixtures import env_vars
from tests.pages._nicegui import ui_client


def _error(seq: int = 3) -> LogEvent:
    return LogEvent(seq, datetime(2026, 10, 12, 17, 3), "error", "Could not decode frames…", "tb")


class _Post:
    """A fake report sender: records each text and answers with a fixed outcome."""

    def __init__(self, ok: bool = True, reason: str = "") -> None:
        self.outcome = (ok, reason)
        self.texts: list[str] = []

    async def __call__(self, text: str) -> tuple[bool, str]:
        self.texts.append(text)
        return self.outcome


class _ReportCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        bugreport.reset()
        self.addCleanup(bugreport.reset)
        env = env_vars(ANNIE_REPORT_KEY="test-key")
        env.__enter__()
        self.addCleanup(env.__exit__, None, None, None)


class TestSend(_ReportCase):
    async def test_a_sent_report_is_remembered(self) -> None:
        post = _Post()
        sent, message = await logs._send(_error(), "Opened Browse", post)
        self.assertTrue(sent)
        self.assertEqual(message, "Report sent — thank you!")
        self.assertTrue(bugreport.already_sent(3))
        (text,) = post.texts
        self.assertIn("What I was doing:\nOpened Browse", text)
        self.assertIn("Error: Could not decode frames…", text)

    async def test_the_same_error_is_never_sent_twice(self) -> None:
        post = _Post()
        await logs._send(_error(), "", post)
        sent, message = await logs._send(_error(), "", post)
        self.assertTrue(sent)
        self.assertEqual(message, "This error was already reported.")
        self.assertEqual(len(post.texts), 1)

    async def test_a_failed_send_can_be_retried(self) -> None:
        post = _Post(ok=False, reason="TypeError: Failed to fetch")
        sent, message = await logs._send(_error(), "", post)
        self.assertFalse(sent)
        self.assertEqual(message, "The report could not be sent: TypeError: Failed to fetch")
        self.assertFalse(bugreport.already_sent(3))

    async def test_the_hourly_limit_stops_sending(self) -> None:
        for seq in range(bugreport.MAX_REPORTS_PER_HOUR):
            bugreport.mark_sent(100 + seq)
        post = _Post()
        sent, message = await logs._send(_error(), "", post)
        self.assertFalse(sent)
        self.assertIn("Too many reports", message)
        self.assertEqual(post.texts, [])


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
        saved_key = bugreport.ACCESS_KEY
        self.addCleanup(setattr, bugreport, "ACCESS_KEY", saved_key)
        bugreport.ACCESS_KEY = ""  # a build without a shipped key
        with env_vars(ANNIE_REPORT_KEY=""), ui_client() as client:
            logs._report_control(_error())
            self.assertEqual(_buttons(client), [])


if __name__ == "__main__":
    unittest.main()
