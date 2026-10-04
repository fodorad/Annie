"""Tests for nested video layouts (real temp trees with neutral, invented names)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.dataset.layout import DiscoveryReport, VideoLayout, contains_nested_videos


def _touch(root: Path, *relative: str) -> None:
    for name in relative:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")


class TestMatching(unittest.TestCase):
    def test_captures_fields_across_segments_and_filename(self) -> None:
        layout = VideoLayout("{grp}/{sub}/clip_{part}.mp4")
        self.assertEqual(layout.match("g1/s2/clip_x.mp4"), {"grp": "g1", "sub": "s2", "part": "x"})

    def test_rejects_wrong_depth_prefix_or_extension(self) -> None:
        layout = VideoLayout("{grp}/{sub}/clip_{part}.mp4")
        for path in (
            "g1/clip_x.mp4",
            "g1/s2/other_x.mp4",
            "g1/s2/clip_x.mov",
            "a/g1/s2/clip_x.mp4",
        ):
            self.assertIsNone(layout.match(path), path)

    def test_literal_text_is_escaped(self) -> None:
        layout = VideoLayout("{a}/v.(1).mp4")
        self.assertIsNotNone(layout.match("x/v.(1).mp4"))
        self.assertIsNone(layout.match("x/vX(1)Xmp4"))

    def test_double_star_matches_any_depth(self) -> None:
        layout = VideoLayout("**/{name}.mp4")
        self.assertEqual(layout.match("a.mp4"), {"name": "a"})
        self.assertEqual(layout.match("d1/d2/a.mp4"), {"name": "a"})

    def test_id_default_joins_fields_in_pattern_order(self) -> None:
        layout = VideoLayout("{grp}/clip_{part}.mp4")
        self.assertEqual(layout.video_id({"grp": "g", "part": "p"}), "g_p")

    def test_id_template(self) -> None:
        layout = VideoLayout("{grp}/clip_{part}.mp4", "{part}-{grp}")
        self.assertEqual(layout.video_id({"grp": "g", "part": "p"}), "p-g")


class TestValidation(unittest.TestCase):
    def test_invalid_layouts_raise(self) -> None:
        bad = [
            VideoLayout(""),
            VideoLayout("/abs/{a}.mp4"),
            VideoLayout("../{a}.mp4"),
            VideoLayout("plain.mp4"),
            VideoLayout("{a}/{a}.mp4"),
            VideoLayout("{a}.mp4", "{nope}"),
            VideoLayout("{a}.mp4", "no_fields"),
        ]
        for layout in bad:
            with self.assertRaises(ValueError, msg=layout):
                layout.validate()

    def test_valid_layout_passes(self) -> None:
        VideoLayout("{a}/{b}.mp4", "{b}_{a}").validate()


class TestDiscover(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_finds_sorts_and_skips_junk(self) -> None:
        _touch(
            self.root,
            "g1/s2/clip_b.mp4",
            "g1/s1/clip_a.mp4",
            "g1/s1/._clip_a.mp4",
            "g1/s1/notes.txt",
            ".hidden/s9/clip_a.mp4",
            "g1/s3/other/clip_a.mp4",
        )
        report = VideoLayout("{grp}/{sub}/clip_{part}.mp4", "{sub}_{part}").discover(self.root)
        self.assertEqual([v.video_id for v in report.videos], ["s1_a", "s2_b"])
        self.assertEqual(report.videos[0].fields, {"grp": "g1", "sub": "s1", "part": "a"})
        self.assertEqual(report.videos[0].path, self.root / "g1/s1/clip_a.mp4")
        self.assertEqual(report.collisions, [])

    def test_collisions_are_reported_and_first_kept(self) -> None:
        _touch(self.root, "g1/s1/clip_a.mp4", "g2/s1/clip_a.mp4")
        report = VideoLayout("{grp}/{sub}/clip_{part}.mp4", "{sub}_{part}").discover(self.root)
        self.assertEqual(report.collisions, ["s1_a"])
        self.assertEqual(len(report.videos), 1)
        self.assertEqual(report.videos[0].fields["grp"], "g1")

    def test_missing_root_is_empty_and_invalid_layout_raises(self) -> None:
        self.assertEqual(VideoLayout("{a}.mp4").discover(self.root / "nope").videos, [])
        with self.assertRaises(ValueError):
            VideoLayout("").discover(self.root)


class TestSummary(unittest.TestCase):
    def test_summary_counts_fields_and_examples(self) -> None:
        root = Path(tempfile.mkdtemp())
        _touch(root, "g1/clip_a.mp4", "g1/clip_b.mp4", "g2/clip_a.mp4")
        text = VideoLayout("{grp}/clip_{part}.mp4").discover(root).summary()
        self.assertIn("3 videos", text)
        self.assertIn("grp ×2", text)
        self.assertIn("part ×2", text)
        self.assertIn("0 collisions", text)
        self.assertIn("g1_a", text)

    def test_empty_summary(self) -> None:
        self.assertEqual(DiscoveryReport().summary(), "No videos matched this layout.")


class TestContainsNestedVideos(unittest.TestCase):
    def test_detects_only_videos_below_the_root(self) -> None:
        root = Path(tempfile.mkdtemp())
        _touch(root, "top.mp4")
        self.assertFalse(contains_nested_videos(root, (".mp4",)))
        _touch(root, "g/s/clip.MP4")
        self.assertTrue(contains_nested_videos(root, (".mp4",)))

    def test_ignores_junk_and_missing_root(self) -> None:
        root = Path(tempfile.mkdtemp())
        _touch(root, "g/._clip.mp4", ".hidden/clip.mp4")
        self.assertFalse(contains_nested_videos(root, (".mp4",)))
        self.assertFalse(contains_nested_videos(root / "nope", (".mp4",)))


if __name__ == "__main__":
    unittest.main()
