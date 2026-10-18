"""The Annotator in a real browser: curation, protagonist review, segment review, events."""

from __future__ import annotations

import csv
import re
from typing import TYPE_CHECKING

from playwright.sync_api import expect

from tests.e2e._browser import BrowserCase

if TYPE_CHECKING:
    from pathlib import Path


class AnnotatorCase(BrowserCase):
    task = ""

    def dataset_config(self) -> Path | None:
        return self.copied_config()

    def setUp(self) -> None:
        super().setUp()
        self.queue_all()
        self.tab("Annotator")
        task = self.panel().get_by_role("button", name=self.task, exact=True)
        task.click()
        expect(task).to_have_class(re.compile(r"bg-primary"))  # the task is on screen


class TestCuration(AnnotatorCase):
    task = "Curation"

    def test_verdict_and_note_are_saved(self) -> None:
        row = self.panel().locator(".q-card").filter(has_text="100499_3")
        expect(row.locator("img[src^='data:image']")).to_have_count(5)
        row.get_by_role("button").filter(has_text="thumb_down").click()
        expect(row.locator(".text-negative")).to_have_count(1)
        row.get_by_label("note").fill("blurry start")
        row.get_by_label("note").blur()
        self.tab("Browse")  # sent after the blur, so the note is saved by then
        expect(self.panel()).to_contain_text("100499_3")

        self.page.reload()  # a fresh page reads everything back from the review database
        self.tab("Annotator")
        self.panel().get_by_role("button", name="Curation", exact=True).click()
        row = self.panel().locator(".q-card").filter(has_text="100499_3")
        expect(row.get_by_label("note")).to_have_value("blurry start")
        expect(row.locator(".text-negative")).to_have_count(1)

    def test_dequeue_one_then_clear_all(self) -> None:
        cards = (
            self.panel().locator(".q-card").filter(has=self.page.locator("i", has_text="thumb_up"))
        )
        expect(cards).to_have_count(3)
        self.tab("Annotator")
        self.panel().get_by_role("button", name="Protagonist review", exact=True).click()
        rows = self.panel().locator(".q-card").filter(has=self.page.get_by_label("Protagonist"))
        expect(rows).to_have_count(3)
        rows.first.get_by_role("button").filter(has_text="close").click()
        expect(rows).to_have_count(2)
        self.panel().get_by_role("button", name="Clear all").click()
        self.dialog().get_by_role("button", name="Clear all").click()
        expect(self.page.get_by_role("tab", name="Annotator")).to_have_attribute(
            "aria-disabled", "true"
        )


class TestProtagonistReview(AnnotatorCase):
    task = "Protagonist review"

    def test_pick_a_track_and_export_the_corrections(self) -> None:
        row = self.panel().locator(".q-card").filter(has_text="100499_3")
        expect(row.locator("img[src^='data:image']")).to_have_count(5)
        row.get_by_label("Protagonist").click()
        self.page.get_by_role("option").filter(has_text="track2").first.click()
        expect(self.page.get_by_role("option")).to_have_count(0)

        self.panel().get_by_role("button", name="Export corrected CSV").click()
        expect(self.page.get_by_text("Exported 1 correction(s)")).to_be_visible()
        manual = self.server_data / "protagonist_track_manual.csv"
        with manual.open(encoding="utf-8") as handle:
            rows = {row["uuid"]: row["track_id"] for row in csv.DictReader(handle)}
        self.assertEqual(rows["100499_3"], "2")

    def dataset_config(self) -> Path | None:
        config = self.copied_config()
        self.server_data = config.parent
        return config


SEGMENTS = {
    "kind": "csv",
    "path": "segments.csv",
    "role": "segmentation",
    "key_column": "video_id",
    "value_columns": ["speaker"],
    "segment_column": "segment",
    "bands": [
        {"name": "truth", "start_column": "gt_start", "end_column": "gt_end"},
        {"name": "aligned", "start_column": "wx_start", "end_column": "wx_end"},
    ],
}


class TestSegmentReview(AnnotatorCase):
    task = "Segment review"

    def dataset_config(self) -> Path | None:
        config = self.copied_config(SEGMENTS)
        self.segments = config.parent / "segments.csv"
        self.segments.write_text(
            "video_id,segment,speaker,gt_start,gt_end,wx_start,wx_end\n"
            "100499_3,_1,host,0.5,2.0,0.6,2.1\n"
            "100499_3,_2,guest,3.0,5.0,3.1,4.8\n",
            encoding="utf-8",
        )
        return config

    def test_decide_every_clip_with_the_keyboard_and_export(self) -> None:
        panel = self.panel()
        expect(panel).to_contain_text("1 / 2")
        expect(panel).to_contain_text("speaker: host")
        expect(panel.locator("img[src^='data:image']")).to_have_count(10)  # two bands of five

        self.page.keyboard.press("a")
        expect(panel.get_by_text("accept", exact=True)).to_be_visible()
        self.page.keyboard.press("ArrowRight")
        expect(panel).to_contain_text("2 / 2")
        self.page.keyboard.press("d")
        expect(panel.get_by_text("drop", exact=True)).to_be_visible()

        panel.get_by_role("button", name="Clip").first.click()  # cut the exact span
        expect(panel.locator("video")).to_have_count(1, timeout=60_000)

        panel.get_by_role("button", name="Export").click()
        expect(self.page.get_by_text("Exported 1 accepted")).to_be_visible()
        accepted = self.segments.with_name("segments_accepted.csv").read_text(encoding="utf-8")
        dropped = self.segments.with_name("segments_dropped.csv").read_text(encoding="utf-8")
        self.assertIn("host", accepted)
        self.assertIn("guest", dropped)


class TestEventAnnotation(AnnotatorCase):
    task = "Event annotation"

    def manage(self, which: int, *names: str) -> None:
        """Add ``names`` in the participants (0) or event categories (1) manager."""
        self.panel().get_by_role("button").filter(has_text="more_horiz").nth(which).click()
        dialog = self.dialog()
        for name in names:
            dialog.locator("input").last.fill(name)
            dialog.get_by_role("button", name="Add").click()
            expect(dialog.locator(f"input[value='{name}']")).to_have_count(1)
        dialog.get_by_role("button", name="Done").click()

    def test_mark_edit_export_and_delete_an_event(self) -> None:
        page, panel = self.page, self.panel()
        expect(panel.locator("video")).to_have_count(1)
        expect(panel).to_contain_text("video 1 / 3")
        self.manage(0, "Mother")
        self.manage(1, "Smile")
        expect(panel.get_by_role("button", name="(2) Mother")).to_be_visible()
        expect(panel.get_by_role("button", name="(⇧1) Smile")).to_be_visible()

        # Choose the lane and the category with the keys, then mark a span.
        page.keyboard.press("2")
        page.keyboard.press("Shift+Digit1")
        page.keyboard.press("i")
        expect(panel.get_by_role("button", name="Set end (O)")).to_be_visible()
        for _ in range(12):
            page.keyboard.press(".")
        page.keyboard.press("o")
        editor = panel.locator(".q-card").filter(has_text="Event").filter(has_text="participant")
        field = editor.locator(".q-field")
        expect(field.filter(has_text="participant").first).to_contain_text("Mother")
        expect(field.filter(has_text="event category").first).to_contain_text("Smile")

        editor.get_by_label("note").fill("big smile")
        editor.get_by_label("note").blur()
        editor.get_by_role("button", name="Add attribute").click()

        panel.get_by_role("button").filter(has_text="zoom_in").click()
        panel.get_by_role("button").filter(has_text="zoom_out").click()

        panel.get_by_role("button", name="Export").click()
        csv_text = self.download(
            lambda: page.locator(".q-menu .q-item").filter(has_text="This video — CSV").click()
        )
        self.assertIn("Smile", csv_text)
        self.assertIn("Mother", csv_text)
        self.assertIn("big smile", csv_text)

        page.locator("body").click(position={"x": 5, "y": 500})  # leave the text fields
        page.keyboard.press("Delete")
        expect(panel.get_by_text("Select or create an event to edit it.")).to_be_visible()

    def test_transport_and_next_video(self) -> None:
        panel = self.panel()
        expect(panel.locator("video")).to_have_count(1)
        for icon in ("play_arrow", "skip_next", "skip_previous", "forward_5", "replay_5"):
            panel.get_by_role("button").filter(has_text=icon).first.click()
        video = panel.locator("video")
        before = video.get_attribute("style") or ""
        panel.get_by_role("button").filter(has_text="add").first.click()  # a bigger video
        expect(video).not_to_have_attribute("style", before)
        panel.get_by_role("button").filter(has_text="chevron_right").first.click()
        expect(panel).to_contain_text("video 2 / 3")
