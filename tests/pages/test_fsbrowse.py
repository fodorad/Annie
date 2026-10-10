"""Tests for the folder-picker filesystem helpers."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath

from annie.pages.fsbrowse import (
    drive_roots,
    list_files,
    list_subdirectories,
    other_drives,
    parent_of,
    resolve_start_dir,
    scan_entries,
)


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


class TestScanEntries(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "dir_b").mkdir()
        (self.tmp / "Dir_a").mkdir()
        (self.tmp / "note.txt").write_text("x", encoding="utf-8")
        (self.tmp / "data.csv").write_text("x", encoding="utf-8")

    def test_folder_mode_skips_files_entirely(self) -> None:
        # The picker's whole point when choosing a folder: never read/render files.
        subdirs, files = scan_entries(self.tmp, want_files=False)
        self.assertEqual([p.name for p in subdirs], ["Dir_a", "dir_b"])
        self.assertEqual(files, [])

    def test_file_mode_returns_both_sorted(self) -> None:
        subdirs, files = scan_entries(self.tmp, want_files=True)
        self.assertEqual([p.name for p in subdirs], ["Dir_a", "dir_b"])
        self.assertEqual([p.name for p in files], ["data.csv", "note.txt"])

    def test_suffix_filter_applies_only_to_files(self) -> None:
        subdirs, files = scan_entries(self.tmp, want_files=True, suffixes=(".csv",))
        self.assertEqual([p.name for p in subdirs], ["Dir_a", "dir_b"])
        self.assertEqual([p.name for p in files], ["data.csv"])


class TestDrives(unittest.TestCase):
    """At a drive root on Windows the picker offers the other drives (e.g. a USB disk)."""

    def test_other_drives_excludes_the_current_one(self) -> None:
        c, e, f = (PureWindowsPath(d) for d in ("C:\\", "E:\\", "F:\\"))
        self.assertEqual(other_drives(PureWindowsPath("E:/videos"), [c, e, f]), [c, f])

    def test_other_drives_matches_by_anchor(self) -> None:
        root = Path(Path.cwd().anchor)
        self.assertEqual(other_drives(root, [root]), [])

    @unittest.skipIf(hasattr(os, "listdrives"), "Windows lists real drives")
    def test_no_drive_list_off_windows(self) -> None:
        self.assertEqual(drive_roots(), [])

    @unittest.skipUnless(hasattr(os, "listdrives"), "os.listdrives is Windows-only")
    def test_lists_the_system_drive(self) -> None:
        self.assertIn(Path(Path.home().anchor), drive_roots())


@unittest.skipUnless(sys.platform == "win32", "file attributes are Windows-only")
class TestWindowsHiddenEntries(unittest.TestCase):
    def test_hidden_attribute_is_skipped(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "visible").mkdir()
        (tmp / "secret").mkdir()
        subprocess.run(["attrib", "+h", str(tmp / "secret")], check=True)  # noqa: S603, S607
        self.assertEqual([p.name for p in list_subdirectories(tmp)], ["visible"])
        names = [p.name for p in list_subdirectories(tmp, show_hidden=True)]
        self.assertEqual(names, ["secret", "visible"])


if __name__ == "__main__":
    unittest.main()
