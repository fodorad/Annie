"""The Log tab in a real browser: a real decode failure is recorded, filtered and inspected."""

from __future__ import annotations

from typing import TYPE_CHECKING

from playwright.sync_api import expect

from tests.e2e._browser import BrowserCase

if TYPE_CHECKING:
    from pathlib import Path


class TestLog(BrowserCase):
    def dataset_config(self) -> Path | None:
        config = self.copied_config()
        (config.parent / "video" / "100499_3.mp4").write_bytes(b"not a video")
        return config

    def test_a_failure_is_shown_filtered_and_explained(self) -> None:
        self.tab("Browse")  # decoding the broken video fails, and says so
        broken = self.panel().locator(".q-card").filter(has_text="100499_3").first
        expect(broken).to_contain_text("#frames: ?")
        self.tab("Log")
        rows = self.panel().locator(".nicegui-row.no-wrap").filter(has_text="100499_3")
        expect(rows.first).to_be_visible()

        self.panel().get_by_label("filter text").fill("started")
        expect(rows).to_have_count(0)
        expect(self.panel().get_by_text("Annie started")).to_be_visible()
        self.panel().get_by_label("filter text").fill("")

        self.panel().locator(".q-select").first.click()
        self.page.get_by_role("option", name="errors").click()
        expect(self.panel().get_by_text("Annie started")).to_have_count(0)
        rows.first.get_by_role("button").filter(has_text="open_in_full").click()
        expect(self.dialog()).to_contain_text("100499_3")
        self.dialog().get_by_role("button", name="Close").click()

        self.panel().get_by_role("button", name="Clear log").click()
        expect(rows).to_have_count(0)
