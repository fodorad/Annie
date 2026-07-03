"""Tests for the convert service: command builders, planning, runner, math."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from annie.media.convert import (
    AudioItem,
    AudioOptions,
    ConversionRunner,
    ConvertProgress,
    VideoItem,
    VideoOptions,
    audio_command,
    audio_video_consistent,
    black_video_command,
    expected_frame_count,
    expected_sample_count,
    export_failures,
    export_ids,
    plan_audio,
    plan_video,
    video_command,
    video_filters,
)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00")
    return path


class TestCommandBuilders(unittest.TestCase):
    def test_audio_command(self) -> None:
        cmd = audio_command("in.mp3", "out.wav", AudioOptions(sample_rate=16000, channels=1))
        self.assertEqual(cmd[:4], ["ffmpeg", "-y", "-i", "in.mp3"])
        self.assertIn("-vn", cmd)
        self.assertEqual(cmd[cmd.index("-ar") + 1], "16000")
        self.assertEqual(cmd[cmd.index("-ac") + 1], "1")
        self.assertEqual(cmd[-1], "out.wav")

    def test_video_filters_keep(self) -> None:
        self.assertEqual(video_filters(VideoOptions()), "")

    def test_video_filters_scale_gray_fps(self) -> None:
        f = video_filters(VideoOptions(width=320, height=240, grayscale=True, fps=24))
        self.assertEqual(f, "scale=320:240,hue=s=0,fps=24")

    def test_video_filters_single_dim_keeps_aspect(self) -> None:
        self.assertEqual(video_filters(VideoOptions(width=320)), "scale=320:-2")
        self.assertEqual(video_filters(VideoOptions(height=240)), "scale=-2:240")

    def test_video_command_without_audio(self) -> None:
        cmd = video_command("in.avi", "out.mp4", VideoOptions(add_audio=False))
        self.assertIn("-an", cmd)
        self.assertIn("libx264", cmd)
        self.assertIn("cfr", cmd)
        self.assertNotIn("apad", cmd)

    def test_video_command_with_audio_matches_length(self) -> None:
        cmd = video_command("in.mp4", "out.mp4", VideoOptions(add_audio=True), audio_path="a.wav")
        self.assertEqual(cmd.count("-i"), 2)
        self.assertIn("apad", cmd)
        self.assertIn("-shortest", cmd)
        self.assertIn("aac", cmd)

    def test_video_command_add_audio_but_no_path_is_silent(self) -> None:
        cmd = video_command("in.mp4", "out.mp4", VideoOptions(add_audio=True), audio_path=None)
        self.assertIn("-an", cmd)

    def test_black_video_command_uses_defaults(self) -> None:
        cmd = black_video_command("out.mp4", "a.wav", VideoOptions())
        joined = " ".join(cmd)
        self.assertIn("color=c=black:s=256x256:r=25", joined)
        self.assertIn("-shortest", cmd)


class TestPlanning(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.videos = self.root / "video"
        self.audio = self.root / "audio"
        self.out = self.root / "out"

    def test_plan_audio(self) -> None:
        _touch(self.audio / "a.wav")
        _touch(self.audio / "b.mp3")
        items = plan_audio(self.audio, self.out, AudioOptions(out_format="wav"))
        self.assertEqual({i.src.name for i in items}, {"a.wav", "b.mp3"})
        self.assertTrue(all(i.dst.suffix == ".wav" and i.dst.parent == self.out for i in items))

    def test_plan_video_pairs_audio_by_stem(self) -> None:
        _touch(self.videos / "x.mp4")
        _touch(self.audio / "x.wav")
        items = plan_video(self.videos, self.out, VideoOptions(add_audio=True), self.audio)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].audio, self.audio / "x.wav")

    def test_plan_video_no_audio_when_disabled(self) -> None:
        _touch(self.videos / "x.mp4")
        _touch(self.audio / "x.wav")
        items = plan_video(self.videos, self.out, VideoOptions(add_audio=False), self.audio)
        self.assertIsNone(items[0].audio)

    def test_plan_video_audio_only_black(self) -> None:
        _touch(self.videos / "x.mp4")
        _touch(self.audio / "x.wav")
        _touch(self.audio / "y.wav")  # no matching video
        opts = VideoOptions(add_audio=True, audio_only_black=True)
        items = plan_video(self.videos, self.out, opts, self.audio)
        black = [i for i in items if i.is_black]
        self.assertEqual(len(black), 1)
        self.assertEqual(black[0].dst, self.out / "y.mp4")
        self.assertEqual(black[0].audio, self.audio / "y.wav")

    def test_plan_video_audio_only_skipped_without_black(self) -> None:
        _touch(self.videos / "x.mp4")
        _touch(self.audio / "y.wav")  # no matching video, no black synthesis
        opts = VideoOptions(add_audio=True, audio_only_black=False)
        items = plan_video(self.videos, self.out, opts, self.audio)
        self.assertEqual([i.src.name for i in items if i.src], ["x.mp4"])
        self.assertTrue(all(not i.is_black for i in items))


class TestLengthMath(unittest.TestCase):
    def test_expected_counts(self) -> None:
        self.assertEqual(expected_frame_count(3.0, 30), 90)
        self.assertEqual(expected_sample_count(3.0, 16000), 48000)

    def test_consistent_within_tolerance(self) -> None:
        self.assertTrue(audio_video_consistent(90, 30, 48000, 16000))
        self.assertTrue(audio_video_consistent(90, 30, 48010, 16000))  # ~0.6ms off
        self.assertFalse(audio_video_consistent(90, 30, 16000, 16000))  # 1s vs 3s
        self.assertFalse(audio_video_consistent(90, 0, 48000, 16000))  # guard


class TestConvertProgress(unittest.TestCase):
    def test_fraction_and_succeeded(self) -> None:
        p = ConvertProgress(total=4, done=3, failed=[("x", "boom")], status="running")
        self.assertAlmostEqual(p.fraction, 0.75)
        self.assertEqual(p.succeeded_count, 2)

    def test_eta_none_until_estimable(self) -> None:
        self.assertIsNone(ConvertProgress(total=4, done=0, status="running").eta_seconds)
        self.assertIsNone(ConvertProgress(total=4, done=4, status="done").eta_seconds)

    def test_zero_total_fraction(self) -> None:
        self.assertEqual(ConvertProgress().fraction, 0.0)


class TestConversionRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = ConversionRunner()

    def tearDown(self) -> None:
        self.runner.shutdown()

    def _items(self, *names: str) -> list[AudioItem]:
        return [AudioItem(Path(f"/x/{n}"), Path(f"/o/{n}")) for n in names]

    def test_batch_accounting_with_a_failure(self) -> None:
        seen: list[str] = []

        def work(item: AudioItem) -> None:
            seen.append(item.label)
            if item.label == "bad.wav":
                raise RuntimeError("boom")

        self.runner.start("T", self._items("ok.wav", "bad.wav"), work).result(timeout=5)
        p = self.runner.progress()
        self.assertEqual(p.status, "done")
        self.assertEqual(p.total, 2)
        self.assertEqual(p.done, 2)
        self.assertEqual(p.succeeded_count, 1)
        self.assertEqual(p.succeeded, ["ok.wav"])
        self.assertEqual([n for n, _ in p.failed], ["bad.wav"])
        self.assertEqual(set(seen), {"ok.wav", "bad.wav"})

    def test_rejects_second_batch_while_running(self) -> None:
        gate = threading.Event()
        fut = self.runner.start("T", self._items("a.wav"), lambda _i: gate.wait(2))
        self.assertTrue(self.runner.running())
        with self.assertRaises(RuntimeError):
            self.runner.start("T2", self._items("b.wav"), lambda _i: None)
        gate.set()
        fut.result(timeout=3)
        self.assertFalse(self.runner.running())


class TestExports(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_export_ids_writes_sorted_unique_stems(self) -> None:
        out = export_ids(self.tmp / "ids.csv", ["b.mp4", "a.mp4", "a.mp4"])
        lines = out.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "id")
        self.assertEqual(lines[1:], ["a", "b"])

    def test_export_failures_writes_id_and_error(self) -> None:
        out = export_failures(self.tmp / "f.csv", [("x.mp4", "no video stream")])
        text = out.read_text(encoding="utf-8")
        self.assertIn("id,error", text)
        self.assertIn("x,no video stream", text)


class TestOptions(unittest.TestCase):
    def test_min_frames_default_is_two(self) -> None:
        self.assertEqual(VideoOptions().min_frames, 2)


class TestItemLabels(unittest.TestCase):
    def test_video_item_label_and_black(self) -> None:
        real = VideoItem(Path("/v/x.mp4"), Path("/o/x.mp4"))
        self.assertEqual(real.label, "x.mp4")
        self.assertFalse(real.is_black)
        black = VideoItem(None, Path("/o/y.mp4"), Path("/a/y.wav"))
        self.assertEqual(black.label, "y.mp4")
        self.assertTrue(black.is_black)


if __name__ == "__main__":
    unittest.main()
