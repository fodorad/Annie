"""Tests for the ffprobe audio-detection command builder."""

from __future__ import annotations

import unittest

from annie.media.probe import audio_probe_command, video_probe_command


class TestProbeCommands(unittest.TestCase):
    def test_audio_selects_audio_streams(self) -> None:
        cmd = audio_probe_command("/data/v.mp4")
        self.assertEqual(cmd[0], "ffprobe")
        self.assertEqual(cmd[cmd.index("-select_streams") + 1], "a")
        self.assertEqual(cmd[-1], "/data/v.mp4")

    def test_video_selects_video_streams(self) -> None:
        cmd = video_probe_command("/data/v.mp4")
        self.assertEqual(cmd[cmd.index("-select_streams") + 1], "v")
        self.assertEqual(cmd[-1], "/data/v.mp4")


if __name__ == "__main__":
    unittest.main()
