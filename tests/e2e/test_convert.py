"""The Convert tab in a real browser: re-encode real audio and video, watch the progress."""

from __future__ import annotations

from playwright.sync_api import expect

from tests.e2e._browser import BrowserCase

CONVERT_TIMEOUT_MS = 180_000
"""Three short clips, re-encoded and validated one by one."""


class TestConvert(BrowserCase):
    def test_audio_then_video_with_the_new_audio(self) -> None:
        data = self.example_copy()
        audio_out, video_out = data.parent / "audio_out", data.parent / "video_out"
        self.tab("Convert")
        panel = self.panel()
        progress = panel.locator(".q-card").filter(has_text="Video").last

        # The folder fields take a typed path, or one chosen with the picker.
        panel.get_by_label("Audio input folder", exact=True).fill(str(data / "audio"))
        panel.get_by_role("button").filter(has_text="folder_open").nth(1).click()
        self.pick(data.parent)
        expect(panel.get_by_label("Audio output folder", exact=True)).to_have_value(
            str(data.parent)
        )
        panel.get_by_label("Audio output folder", exact=True).fill(str(audio_out))
        panel.get_by_role("button", name="Convert audio").click()
        expect(self.page.get_by_text("converting 3 file(s)")).to_be_visible()
        expect(panel).to_contain_text("converted 3", timeout=CONVERT_TIMEOUT_MS)
        self.assertEqual(len(list(audio_out.glob("*.wav"))), 3)

        panel.get_by_label("Video input folder", exact=True).fill(str(data / "video"))
        panel.get_by_label("Video output folder", exact=True).fill(str(video_out))
        panel.get_by_role("button", name="Convert video").click()
        expect(panel).to_contain_text("3/3", timeout=CONVERT_TIMEOUT_MS)
        expect(progress).to_contain_text("converted 3")
        self.assertEqual(len(list(video_out.glob("*.mp4"))), 3)

        panel.get_by_role("button", name="Export reencoded ids").click()
        self.pick(data.parent)
        expect(self.page.get_by_text("Exported 3 id(s)")).to_be_visible()
        self.assertTrue((data.parent / "reencoded_ids.csv").exists())

    def test_an_empty_input_folder_is_explained(self) -> None:
        data = self.example_copy()
        empty = data.parent / "empty"
        empty.mkdir()
        self.tab("Convert")
        self.panel().get_by_label("Audio input folder", exact=True).fill(str(empty))
        self.panel().get_by_label("Audio output folder", exact=True).fill(str(data.parent / "out"))
        self.panel().get_by_role("button", name="Convert audio").click()
        expect(self.page.locator(".q-notification")).to_be_visible()
        expect(self.panel()).to_contain_text("No conversion running.")
