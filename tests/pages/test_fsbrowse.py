"""Tests for the folder-picker filesystem helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.pages.fsbrowse import list_files, list_subdirectories, parent_of, resolve_start_dir


class TestResolveStartDir(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_existing_directory_returned_as_is(self) -> None:
        self.assertEqual(resolve_start_dir(self.tmp), self.tmp)

    def test_falls_back_to_nearest_existing_ancestor(self) -> None:
        missing = self.tmp / "a" / "b" / "c"
        self.assertEqual(resolve_start_dir(missing), self.tmp)

    def test_none_falls_back_to_home(self) -> None:
        self.assertEqual(resolve_start_dir(None), Path.home())

    def test_empty_string_falls_back_to_home(self) -> None:
        self.assertEqual(resolve_start_dir(""), Path.home())


class TestParentOf(unittest.TestCase):
    def test_returns_parent(self) -> None:
        self.assertEqual(parent_of("/a/b/c"), Path("/a/b"))

    def test_root_has_no_parent(self) -> None:
        self.assertIsNone(parent_of("/"))


class TestListSubdirectories(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "beta").mkdir()
        (self.tmp / "Alpha").mkdir()
        (self.tmp / ".hidden").mkdir()
        (self.tmp / "._junk").mkdir()
        (self.tmp / "a_file.txt").write_text("x", encoding="utf-8")

    def test_lists_only_directories_sorted_case_insensitively(self) -> None:
        names = [p.name for p in list_subdirectories(self.tmp)]
        self.assertEqual(names, ["Alpha", "beta"])

    def test_hidden_excluded_by_default_included_on_request(self) -> None:
        visible = {p.name for p in list_subdirectories(self.tmp)}
        self.assertNotIn(".hidden", visible)
        self.assertNotIn("._junk", visible)
        with_hidden = {p.name for p in list_subdirectories(self.tmp, show_hidden=True)}
        self.assertIn(".hidden", with_hidden)

    def test_missing_directory_returns_empty(self) -> None:
        self.assertEqual(list_subdirectories(self.tmp / "nope"), [])


class TestListFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "b.csv").write_text("x", encoding="utf-8")
        (self.tmp / "A.txt").write_text("x", encoding="utf-8")
        (self.tmp / ".hidden").write_text("x", encoding="utf-8")
        (self.tmp / "sub").mkdir()

    def test_lists_only_files_sorted(self) -> None:
        self.assertEqual([p.name for p in list_files(self.tmp)], ["A.txt", "b.csv"])

    def test_suffix_filter(self) -> None:
        self.assertEqual([p.name for p in list_files(self.tmp, suffixes=(".csv",))], ["b.csv"])

    def test_hidden_excluded(self) -> None:
        self.assertNotIn(".hidden", {p.name for p in list_files(self.tmp)})

    def test_missing_directory_returns_empty(self) -> None:
        self.assertEqual(list_files(self.tmp / "nope"), [])


if __name__ == "__main__":
    unittest.main()
