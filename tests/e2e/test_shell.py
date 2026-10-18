"""The app shell in a real browser: start-up, tab navigation, banners, a second launch."""

from __future__ import annotations

import os
import re
import subprocess
import sys

from playwright.sync_api import expect

from tests.e2e._browser import BrowserCase
from tests.e2e._server import REPO


class TestNavigation(BrowserCase):
    def test_home_cards_open_their_tabs(self) -> None:
        page = self.page
        for card, landmark in (
            ("Dataset", "Sources"),
            ("Browse", "Manipulate"),
            ("Settings", "Maintenance"),
            ("Log", "Level"),
            ("Convert", "Audio"),
        ):
            self.tab("Home")
            page.locator(".q-card", has_text=card).filter(has_text="arrow_forward").first.click()
            expect(page.get_by_role("tab", name=card)).to_have_attribute("aria-selected", "true")
            expect(page.locator(".q-tab-panel:visible").get_by_text(landmark).first).to_be_visible()

    def test_the_header_shows_the_version(self) -> None:
        expect(self.page.locator("header")).to_contain_text(re.compile(r"v\d+\.\d+"))

    def test_the_annotator_waits_for_a_queued_video(self) -> None:
        expect(self.page.get_by_role("tab", name="Annotator")).to_have_attribute(
            "aria-disabled", "true"
        )


class TestFirstRun(BrowserCase):
    config = None

    def test_browse_asks_for_a_videos_folder(self) -> None:
        self.tab("Browse")
        expect(self.page.get_by_text("Add a videos folder on the Dataset tab")).to_be_visible()


class TestOutdatedLauncher(BrowserCase):
    env = {"ANNIE_LAUNCHER_VERSION": "1"}

    def test_a_banner_offers_the_new_installer(self) -> None:
        expect(self.page.get_by_text("A new Annie installer is available")).to_be_visible()
        expect(self.page.get_by_role("link", name="Download installer")).to_be_visible()


class TestSecondLaunch(BrowserCase):
    def test_a_second_launch_reuses_the_running_annie(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("ANNIE_")}
        env.update(
            ANNIE_HOME=str(self.server.home),
            ANNIE_PORT=str(self.server.port),
            ANNIE_HOST="127.0.0.1",
        )
        second = subprocess.run(
            [sys.executable, "-m", "annie.app"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("Annie is already running", second.stdout + second.stderr)
