"""Browser scaffolding for end-to-end tests: a real Annie server driven by Chromium.

Each test gets its own server (with its own home, so no test sees another's state) and a
fresh page; each class shares one headless browser. Set ``ANNIE_E2E_HEADED=1`` to watch.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from playwright.sync_api import Browser, Locator, Page, Playwright, expect, sync_playwright

from tests.e2e._server import EXAMPLE_CONFIG, REPO, AnnieServer

if TYPE_CHECKING:
    from collections.abc import Callable

TIMEOUT_MS = 15_000
"""How long an expectation waits for the UI (a cold first decode can take seconds)."""

expect.set_options(timeout=TIMEOUT_MS)


class BrowserCase(unittest.TestCase):
    """Starts Annie and opens it in a fresh browser page for every test.

    Attributes:
        config: The dataset the server opens on start (``None``: a first run).
        env: Extra environment variables for the server.
        server: The running server.
        page: This test's page, already loaded.
    """

    config: ClassVar = EXAMPLE_CONFIG
    env: ClassVar[dict[str, str]] = {}
    server: AnnieServer
    _playwright: ClassVar[Playwright]
    _browser: ClassVar[Browser]
    page: Page

    @classmethod
    def setUpClass(cls) -> None:
        cls._playwright = sync_playwright().start()
        cls.addClassCleanup(cls._playwright.stop)
        headed = os.environ.get("ANNIE_E2E_HEADED") == "1"
        cls._browser = cls._playwright.chromium.launch(headless=not headed)
        cls.addClassCleanup(cls._browser.close)

    def setUp(self) -> None:
        self._errors: list[str] = []
        self.server = AnnieServer(config=self.dataset_config(), env=dict(self.env))
        self.prepare_home(self.server.home)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.page = self.open_page()

    def prepare_home(self, home: Path) -> None:
        """Put files into the server's Annie home before it starts; override as needed."""

    def dataset_config(self) -> Path | None:
        """The config the server opens on start; override to build one per test."""
        return self.config

    def open_page(self) -> Page:
        """Open the app in a new browser tab (its own NiceGUI client)."""
        context = self._browser.new_context(
            viewport={"width": 1600, "height": 1000}, accept_downloads=True
        )
        self.addCleanup(context.close)
        # Exports then save through a plain download, as in Firefox and Safari; Chromium's
        # native Save dialog cannot be answered from a test.
        context.add_init_script("delete window.showSaveFilePicker")
        page = context.new_page()
        page.set_default_timeout(TIMEOUT_MS)
        page.on("pageerror", lambda exc: self._errors.append(str(exc)))
        page.goto(self.server.url)
        expect(page.get_by_role("tab", name="Home")).to_be_visible()
        return page

    def tab(self, name: str, page: Page | None = None) -> None:
        """Open the tab labelled ``name``."""
        (page or self.page).get_by_role("tab", name=name).click()

    def panel(self, page: Page | None = None) -> Locator:
        """The visible tab panel."""
        return (page or self.page).locator(".q-tab-panel:visible").first

    def dialog(self) -> Locator:
        """The topmost open dialog."""
        return self.page.locator(".q-dialog").last

    def choose(self, label: str, option: str) -> None:
        """Pick ``option`` in the drop-down labelled ``label``."""
        self.page.get_by_label(label).click()
        self.page.get_by_role("option", name=option, exact=True).click()

    def pick(self, target: Path) -> None:
        """Walk Annie's folder/file picker to ``target`` and choose it.

        The picker opens at home (or beside the last pick); this clicks ``..`` and folder
        names until it shows ``target``'s folder, as a user would.
        """
        target = target.resolve()
        picker = self.dialog()
        where = picker.locator(".break-all").first
        folder = target.parent if target.is_file() else target
        for _ in range(64):
            expect(where).not_to_have_text("Loading…")
            current = Path(where.inner_text())
            if current == folder:
                break
            step = folder.relative_to(current).parts[0] if folder.is_relative_to(current) else ".."
            picker.locator(".cursor-pointer").filter(
                has=self.page.get_by_text(step, exact=True)
            ).first.click()
            expect(where).not_to_have_text(str(current))
        else:
            self.fail(f"the picker never reached {folder}")
        if target.is_file():
            picker.locator(".cursor-pointer").filter(
                has=self.page.get_by_text(target.name, exact=True)
            ).first.click()
        else:
            picker.get_by_role("button", name="Select this folder").click()

    def example_copy(self) -> Path:
        """A private copy of the example dataset (removed after the test)."""
        root = Path(tempfile.mkdtemp(prefix="annie-e2e-data-")).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        shutil.copytree(REPO / "data" / "example" / "mosei_mini", root / "mosei_mini")
        return root / "mosei_mini"

    def download(self, click: Callable[[], None]) -> str:
        """Run ``click`` and return the text of the file it downloads."""
        with self.page.expect_download() as info:
            click()
        return Path(info.value.path()).read_text(encoding="utf-8")

    def copied_config(self, *extra: dict) -> Path:
        """A config over a private copy of the example, so the test may write beside it.

        Args:
            *extra: More source entries for the config, with paths relative to the copy.
        """
        data = self.example_copy()
        config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        for source in config["sources"]:
            source["path"] = source["path"].replace("../example/mosei_mini/", "")
        config["sources"].extend(extra)
        config["db"] = "annie_copy.db"
        path = data / "copy.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def queue_all(self) -> None:
        """Queue every video for the Annotator, from Browse."""
        self.tab("Browse")
        header = self.panel().locator(".q-expansion-item__container > .q-item")
        header.filter(has=self.page.get_by_text("View", exact=True)).first.click()
        self.panel().get_by_role("button", name="Add all to Annotator").click()
        expect(self.page.get_by_role("tab", name="Annotator")).not_to_have_attribute(
            "aria-disabled", "true"
        )

    def tearDown(self) -> None:
        self.assertEqual(self._errors, [], "the page raised JavaScript errors")
