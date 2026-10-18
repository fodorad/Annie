"""Two browser tabs on one Annie: data-wide changes reach both, per-tab work stays put."""

from __future__ import annotations

from playwright.sync_api import expect

from tests.e2e._browser import BrowserCase


class TestTwoTabs(BrowserCase):
    def test_a_queued_video_enables_the_other_tabs_annotator(self) -> None:
        other = self.open_page()
        annotator = other.get_by_role("tab", name="Annotator")
        expect(annotator).to_have_attribute("aria-disabled", "true")
        self.queue_all()
        expect(annotator).not_to_have_attribute("aria-disabled", "true")

    def test_switching_the_dataset_rebuilds_the_other_tabs_browse(self) -> None:
        other = self.open_page()
        self.tab("Browse", other)
        expect(self.panel(other)).to_contain_text("100499_3")

        self.tab("Dataset")
        self.choose("Config", "[Example] First Impression V2 Mini")
        expect(self.panel(other)).not_to_contain_text("100499_3")
        expect(self.panel()).to_contain_text("# videos")

    def test_filtering_in_one_tab_leaves_the_other_alone(self) -> None:
        other = self.open_page()
        self.tab("Browse", other)
        self.tab("Browse")
        header = self.panel().locator(".q-expansion-item__container > .q-item")
        header.filter(has=self.page.get_by_text("Filter", exact=True)).first.click()
        self.panel().get_by_label("name starts with").fill("100")
        expect(self.panel()).not_to_contain_text("--qXJuDtHPw_5")
        expect(self.panel(other)).to_contain_text("--qXJuDtHPw_5")
