"""Tests for the Settings tab's review-export route (the browser saves what it serves)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.core.state import state
from annie.dataset.storage import ReviewStore
from annie.pages import settings


class TestReviewExportRoute(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = state.store
        state.store = ReviewStore(Path(tempfile.mkdtemp()) / "annie.db")

    def tearDown(self) -> None:
        state.store = self._saved

    def test_serves_json_and_csv(self) -> None:
        json_resp = settings._review_export_route("json")  # noqa: SLF001
        csv_resp = settings._review_export_route("csv")  # noqa: SLF001
        self.assertEqual((json_resp.status_code, json_resp.media_type), (200, "application/json"))
        self.assertEqual((csv_resp.status_code, csv_resp.media_type), (200, "text/csv"))
        self.assertTrue(bytes(csv_resp.body).startswith(b"row_key,video_id"))

    def test_unknown_format_is_204(self) -> None:
        self.assertEqual(settings._review_export_route("xml").status_code, 204)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
