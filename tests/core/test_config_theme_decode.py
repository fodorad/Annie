"""Tests for config resolution, theme tokens, and media-agnostic decode helpers."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from annie.core import theme
from annie.core.config import Settings, _env_int, _env_path
from annie.media.decode import media_available, strip_indices


class TestConfig(unittest.TestCase):
    def test_env_path_expands_user_and_handles_empty(self) -> None:
        with mock.patch.dict(os.environ, {"X_PATH": "~/data"}):
            self.assertEqual(_env_path("X_PATH"), Path.home() / "data")
        with mock.patch.dict(os.environ, {"X_PATH": "  "}):
            self.assertIsNone(_env_path("X_PATH"))

    def test_env_int_falls_back_on_garbage(self) -> None:
        with mock.patch.dict(os.environ, {"X_INT": "not-a-number"}):
            self.assertEqual(_env_int("X_INT", 42), 42)
        with mock.patch.dict(os.environ, {"X_INT": "7"}):
            self.assertEqual(_env_int("X_INT", 42), 7)

    def test_settings_reads_env_overrides(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"ANNIE_VIDEO_DIR": "/tmp/vids", "ANNIE_PORT": "9999"},
        ):
            settings = Settings()
        self.assertEqual(settings.videos_dir, Path("/tmp/vids"))
        self.assertEqual(settings.port, 9999)

    def test_default_extensions(self) -> None:
        self.assertIn(".mp4", Settings().video_extensions)


class TestTheme(unittest.TestCase):
    def test_status_color_mapping(self) -> None:
        self.assertEqual(theme.status_color("linked"), theme.SUCCESS)
        self.assertEqual(theme.status_color("video_only"), theme.WARNING)
        self.assertEqual(theme.status_color("annotation_only"), theme.DANGER)

    def test_named_color_lookup(self) -> None:
        self.assertEqual(theme.color("primary"), theme.PRIMARY)
        with self.assertRaises(KeyError):
            theme.color("nonexistent")  # type: ignore[arg-type]

    def test_every_status_has_label_and_icon(self) -> None:
        for status in ("linked", "video_only", "annotation_only"):
            self.assertIn(status, theme.STATUS_LABELS)
            self.assertIn(status, theme.STATUS_ICONS)


class TestStripIndices(unittest.TestCase):
    def test_five_evenly_spaced_indices(self) -> None:
        self.assertEqual(strip_indices(101, 5), [0, 25, 50, 75, 100])

    def test_single_frame_video(self) -> None:
        self.assertEqual(strip_indices(1, 5), [0, 0, 0, 0, 0])

    def test_count_one(self) -> None:
        self.assertEqual(strip_indices(50, 1), [0])

    def test_invalid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            strip_indices(0)
        with self.assertRaises(ValueError):
            strip_indices(10, 0)


class TestMediaGuards(unittest.TestCase):
    def test_media_available_returns_bool(self) -> None:
        self.assertIsInstance(media_available(), bool)


if __name__ == "__main__":
    unittest.main()
