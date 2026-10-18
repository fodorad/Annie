"""The Settings tab in a real browser: preferences, review export, and reclaiming space."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from playwright.sync_api import expect

from tests.e2e._browser import BrowserCase

if TYPE_CHECKING:
    from pathlib import Path


class TestSettings(BrowserCase):
    def prepare_home(self, home: Path) -> None:
        (home / "logs").mkdir()
        (home / "logs" / "older.log").write_text("old\n", encoding="utf-8")
        (home / "sessions").mkdir()
        (home / "sessions" / "annie_old.db").write_bytes(b"\0" * 2048)
        self.home = home

    def test_row_height_applies_to_browse(self) -> None:
        self.tab("Settings")
        height = self.panel().get_by_label("Browse row height (px)")
        height.fill("200")
        height.blur()
        self.tab("Browse")
        image = self.panel().locator("img[src^='data:image']").first
        expect(image).to_be_visible()
        box = image.bounding_box()
        assert box is not None
        self.assertAlmostEqual(box["height"], 200, delta=1)

    def test_export_review_status(self) -> None:
        self.queue_all()
        self.tab("Settings")
        text = self.download(lambda: self.panel().get_by_role("button", name="Export JSON").click())
        self.assertIn("100499_3", json.dumps(json.loads(text)))

    def test_clean_up_reclaims_old_logs_and_databases(self) -> None:
        self.tab("Settings")
        maintenance = self.panel().locator(".q-card").filter(has_text="Maintenance")
        expect(maintenance).to_contain_text("Old log files (1)")
        expect(maintenance).to_contain_text("Old session databases (1)")
        maintenance.get_by_role("button", name="Clean up all").click()
        expect(maintenance).to_contain_text("Old log files (0)")
        self.assertFalse((self.home / "logs" / "older.log").exists())
        self.assertFalse((self.home / "sessions" / "annie_old.db").exists())
