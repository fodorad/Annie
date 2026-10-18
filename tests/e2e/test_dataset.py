"""The Dataset tab in a real browser: compose a dataset, save and reload it, switch databases."""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Locator, expect

from tests.e2e._browser import BrowserCase


class TestComposeADataset(BrowserCase):
    config = None

    def add_source(self, kind: str) -> None:
        self.panel().get_by_role("button", name="Add data source").click()
        self.page.locator(".q-menu").get_by_text(kind).click()

    def metric(self, name: str) -> Locator:
        """The metric card titled ``name``."""
        return self.panel().locator(".q-card").filter(has_text=name).first

    def test_compose_save_remove_and_reload(self) -> None:
        data = self.example_copy()
        configs = data.parent / "configs"
        configs.mkdir()
        self.tab("Dataset")
        expect(self.panel().get_by_text("No sources yet")).to_be_visible()

        # A videos folder, then the related sources Annie finds beside it.
        self.add_source("Videos folder")
        self.pick(data / "video")
        expect(self.dialog()).to_contain_text("Auto-fill related sources?")
        self.dialog().get_by_role("button", name="Fill selected").click()
        expect(self.metric("# videos")).to_contain_text("3")
        expect(self.metric("# track files")).to_contain_text("6")
        protagonist = self.panel().locator(".q-card").filter(has_text="Protagonist review")
        expect(protagonist.last).to_contain_text("Ready")

        # Saved as a named config with its own review database.
        self.panel().get_by_role("button", name="Save current…").click()
        self.dialog().get_by_label("Config name").fill("Mini copy")
        self.dialog().get_by_role("button", name="Choose folder…").click()
        self.pick(configs)
        saved = configs / "mini_copy.json"
        expect(self.page.get_by_text("Saved 'Mini copy'")).to_be_visible()
        self.assertEqual(json.loads(saved.read_text())["name"], "Mini copy")
        expect(self.panel().get_by_label("Config")).to_have_value("Mini copy")

        # Removing a source updates the counts and the tasks at once.
        track = self.panel().locator(".q-card").filter(has_text="Track folder").last
        track.get_by_role("button").last.click()
        expect(self.metric("# track files")).to_have_count(0)

        # A fresh config is empty; loading the saved one brings every source back.
        self.choose("Config", "New config…")
        expect(self.panel().get_by_text("No sources yet")).to_be_visible()
        self.panel().get_by_role("button", name="Load from file…").click()
        self.pick(saved)
        expect(self.metric("# track files")).to_contain_text("6")

    def test_add_a_label_table(self) -> None:
        data = self.example_copy()
        self.tab("Dataset")
        self.add_source("Videos folder")
        self.pick(data / "video")
        self.dialog().get_by_role("button", name="Skip").click()
        expect(self.metric("# videos")).to_contain_text("3")

        self.add_source("Table")
        self.pick(data / "label.csv")
        table = self.dialog()
        expect(table).to_contain_text("3 of 3 videos matched")
        table.get_by_role("checkbox", name="sentiment").click()
        table.get_by_role("button", name="Add source").click()
        label = self.panel().locator(".q-card").filter(has_text="Label file").last
        expect(label).to_contain_text("tags: sentiment")


class TestReviewDatabases(BrowserCase):
    def test_new_rename_and_switch_back(self) -> None:
        self.tab("Dataset")
        path_box = self.panel().get_by_label("Review database")
        new_db = self.panel().get_by_role("button").filter(has_text="add").last
        original = path_box.input_value()

        new_db.click()
        expect(self.page.get_by_text("New session DB:")).to_be_visible()
        fresh = path_box.input_value()
        self.assertNotEqual(fresh, original)

        # Editing the path of a session database renames it; its data moves along.
        renamed = Path(fresh).with_name("named.db")
        path_box.fill(str(renamed))
        path_box.press("Enter")
        expect(self.page.get_by_text("Renamed session DB to named.db")).to_be_visible()
        expect(path_box).to_have_value(str(renamed))
        self.assertFalse(Path(fresh).exists())

        # Another new one, then back to the named one from the recent list.
        new_db.click()
        expect(path_box).not_to_have_value(str(renamed))
        self.panel().get_by_text("named.db", exact=True).click()
        expect(path_box).to_have_value(str(renamed))

    def test_switching_to_another_example(self) -> None:
        self.tab("Dataset")
        self.choose("Config", "[Example] First Impression V2 Mini")
        metric = self.panel().locator(".q-card").filter(has_text="# videos").first
        expect(metric).to_contain_text("10")
