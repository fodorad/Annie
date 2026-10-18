"""The Browse tab in a real browser: rows with real decoded frames, filters, and selection."""

from __future__ import annotations

from playwright.sync_api import Locator, expect

from tests.e2e._browser import BrowserCase

RENDER_TIMEOUT_MS = 120_000
"""A full annotated render re-encodes the whole clip."""


class BrowseCase(BrowserCase):
    def setUp(self) -> None:
        super().setUp()
        self.tab("Browse")

    def rows(self) -> Locator:
        """The sample rows (cards carrying a selection corner)."""
        return (
            self.panel()
            .locator(".q-card")
            .filter(has=self.page.locator("div[style*='z-index: 6']"))
        )

    def row(self, video_id: str) -> Locator:
        return self.rows().filter(has_text=video_id)

    def open_panel(self, name: str) -> Locator:
        """Expand the Manipulate, Filter or View panel and return it."""
        header = self.panel().locator(".q-expansion-item__container > .q-item")
        header = header.filter(has=self.page.get_by_text(name, exact=True)).first
        header.click()
        return header.locator("xpath=../..")


class TestRows(BrowseCase):
    def test_a_row_shows_its_frames_tags_and_plays(self) -> None:
        row = self.row("100499_3")
        expect(row.locator("img[src^='data:image']")).to_have_count(5)  # the decoded strip
        expect(row).to_contain_text("#frames: 357")
        expect(row).to_contain_text("4 track")
        expect(row).to_contain_text("sentiment: -2.000000")
        expect(row.get_by_text("audio", exact=True)).to_be_visible()

        row.get_by_role("button", name="Original").click()
        expect(row.locator("video")).to_have_count(1)

    def test_render_burns_the_tracks_into_a_clip(self) -> None:
        row = self.row("-UacrmKiTn4_10")
        row.get_by_role("button", name="Render").click()
        expect(row.locator("video")).to_have_count(1, timeout=RENDER_TIMEOUT_MS)


class TestFilters(BrowseCase):
    def test_name_tags_and_clear(self) -> None:
        expect(self.rows()).to_have_count(3)
        filters = self.open_panel("Filter")
        filters.get_by_label("name starts with").fill("100")
        expect(self.rows()).to_have_count(1)  # applied once typing pauses
        expect(self.rows().first).to_contain_text("100499_3")

        filters.locator("button", has_text="Clear").click()
        expect(self.rows()).to_have_count(3)

    def test_a_label_transform_changes_the_tags(self) -> None:
        manipulate = self.open_panel("Manipulate")
        sentiment = manipulate.locator(".nicegui-row").filter(has_text="sentiment").first
        sentiment.locator(".q-select").click()
        self.page.get_by_role("option", name="sign").click()
        expect(self.row("100499_3")).not_to_contain_text("sentiment: -2.000000")

    def test_ids_from_a_csv_file(self) -> None:
        data = self.example_copy()
        ids = data / "wanted.csv"
        ids.write_text("video_id\n100499_3\n", encoding="utf-8")
        self.open_panel("Filter").get_by_role("button", name="Ids from CSV…").click()
        self.pick(ids)
        dialog = self.dialog()
        dialog.get_by_role("button").last.click()  # confirm the suggested id column
        expect(self.rows()).to_have_count(1)
        expect(self.panel()).to_contain_text("wanted.csv")


class TestSelection(BrowseCase):
    def test_selecting_a_video_queues_it_for_the_annotator(self) -> None:
        annotator = self.page.get_by_role("tab", name="Annotator")
        expect(annotator).to_have_attribute("aria-disabled", "true")
        self.row("100499_3").locator("div[style*='z-index: 6']").click()
        expect(annotator).not_to_have_attribute("aria-disabled", "true")

        # The quick-selection grid shows the same selection; clicking a box toggles it.
        self.open_panel("View").get_by_text("Quick selection").click()
        boxes = self.panel().locator(".cursor-pointer").filter(has_text="#3")
        expect(boxes.first.locator("i", has_text="check_circle")).to_be_visible()
        boxes.first.click()
        expect(annotator).to_have_attribute("aria-disabled", "true")

    def test_add_all_then_annotate(self) -> None:
        self.open_panel("View").get_by_role("button", name="Add all to Annotator").click()
        self.tab("Annotator")
        expect(self.panel()).to_contain_text("100499_3")
