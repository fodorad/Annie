"""Tests for the OS file-manager reveal command builder and Docker detection."""

from __future__ import annotations

import unittest
from pathlib import Path

from annie.pages.reveal import is_docker, reveal_command


class TestRevealCommand(unittest.TestCase):
    def test_macos_selects_file_in_finder(self) -> None:
        self.assertEqual(reveal_command("/data/v.mp4", "darwin"), ["open", "-R", "/data/v.mp4"])

    def test_windows_selects_file_in_explorer(self) -> None:
        cmd = reveal_command(r"C:\data\v.mp4", "win32")
        self.assertEqual(cmd[0], "explorer")
        self.assertTrue(cmd[1].startswith("/select,"))

    def test_other_opens_containing_directory(self) -> None:
        cmd = reveal_command("/data/sub/v.mp4", "linux")
        self.assertEqual(cmd, ["xdg-open", str(Path("/data/sub"))])


class TestIsDocker(unittest.TestCase):
    def test_returns_false_for_nonexistent_sentinel(self) -> None:
        self.assertFalse(is_docker(Path("/this/path/does/not/exist/__dockerenv")))

    def test_returns_true_for_existing_sentinel(self) -> None:
        self.assertTrue(is_docker(Path(__file__)))


if __name__ == "__main__":
    unittest.main()
