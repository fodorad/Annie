"""Tests for the Browse filtering predicates."""

from __future__ import annotations

import unittest
from pathlib import Path

from annie.core.models import VideoEntry
from annie.dataset.filtering import FilterSpec, ReviewState, apply_filters, matches


def _entry(
    vid: str,
    *,
    video: bool = True,
    vdet: bool = False,
    tracks: int = 0,
    labels: dict[str, str] | None = None,
) -> VideoEntry:
    return VideoEntry(
        video_id=vid,
        video_path=Path(f"/v/{vid}.mp4") if video else None,
        vdet_path=Path(f"/d/{vid}.vdet") if vdet else None,
        track_ids=list(range(tracks)),
        track_paths=[Path(f"/t/{vid}__track{i}.csv") for i in range(tracks)],
        labels=labels or {},
    )


class TestFilterSpec(unittest.TestCase):
    def test_is_active(self) -> None:
        self.assertFalse(FilterSpec().is_active)
        self.assertTrue(FilterSpec(vdet="has").is_active)
        self.assertTrue(FilterSpec(labels={"Sentiment": {"neg"}}).is_active)
        self.assertFalse(FilterSpec(labels={"Sentiment": set()}).is_active)
        self.assertTrue(FilterSpec(name_prefix="-NFr").is_active)
        self.assertFalse(FilterSpec(name_prefix="   ").is_active)
        self.assertTrue(FilterSpec(video="has").is_active)
        self.assertTrue(FilterSpec(audio="missing").is_active)
        self.assertTrue(FilterSpec(frames="lt", frames_threshold=25).is_active)


class TestMatches(unittest.TestCase):
    def setUp(self) -> None:
        self.liked = ReviewState()
        self.entry = _entry("A", vdet=True, tracks=2, labels={"Sentiment": "negative"})

    def test_name_prefix_facet(self) -> None:
        a = _entry("-NFrJFQijFE_0")
        b = _entry("-NFrJFQijFE_1")
        other = _entry("ZZZ_9")
        spec = FilterSpec(name_prefix="-NFrJFQ")
        self.assertTrue(matches(a, self.liked, spec))
        self.assertTrue(matches(b, self.liked, spec))
        self.assertFalse(matches(other, self.liked, spec))
        # case-insensitive
        self.assertTrue(matches(a, self.liked, FilterSpec(name_prefix="-nfrjfq")))

    def test_video_facet(self) -> None:
        self.assertTrue(matches(self.entry, self.liked, FilterSpec(video="has")))
        self.assertFalse(matches(self.entry, self.liked, FilterSpec(video="missing")))
        audio_only = _entry("A0", video=False)
        self.assertTrue(matches(audio_only, self.liked, FilterSpec(video="missing")))

    def test_audio_facet_uses_probed_flag(self) -> None:
        has = FilterSpec(audio="has")
        missing = FilterSpec(audio="missing")
        self.assertTrue(matches(self.entry, self.liked, has, has_audio=True))
        self.assertFalse(matches(self.entry, self.liked, has, has_audio=False))
        self.assertTrue(matches(self.entry, self.liked, missing, has_audio=False))
        # unknown (not yet probed) only passes "any"
        self.assertFalse(matches(self.entry, self.liked, has, has_audio=None))
        self.assertTrue(matches(self.entry, self.liked, FilterSpec(), has_audio=None))

    def test_frames_facet(self) -> None:
        lt = FilterSpec(frames="lt", frames_threshold=25)
        gt = FilterSpec(frames="gt", frames_threshold=250)
        self.assertTrue(matches(self.entry, self.liked, lt, num_frames=10))
        self.assertFalse(matches(self.entry, self.liked, lt, num_frames=30))
        self.assertTrue(matches(self.entry, self.liked, gt, num_frames=300))
        self.assertFalse(matches(self.entry, self.liked, gt, num_frames=100))
        # unknown frame count only passes "any"
        self.assertFalse(matches(self.entry, self.liked, lt, num_frames=None))

    def test_vdet_facet(self) -> None:
        self.assertTrue(matches(self.entry, self.liked, FilterSpec(vdet="has")))
        self.assertFalse(matches(self.entry, self.liked, FilterSpec(vdet="missing")))

    def test_track_facets(self) -> None:
        self.assertTrue(matches(self.entry, self.liked, FilterSpec(tracks="multi")))
        self.assertFalse(matches(self.entry, self.liked, FilterSpec(tracks="one")))
        self.assertFalse(matches(self.entry, self.liked, FilterSpec(tracks="none")))
        self.assertTrue(matches(_entry("B", tracks=1), self.liked, FilterSpec(tracks="one")))
        self.assertTrue(matches(_entry("C", tracks=0), self.liked, FilterSpec(tracks="none")))

    def test_review_facet_defaults_liked(self) -> None:
        self.assertTrue(matches(self.entry, ReviewState(), FilterSpec(review="liked")))
        self.assertFalse(matches(self.entry, ReviewState(), FilterSpec(review="disliked")))
        disliked = ReviewState(verdict="bad")
        self.assertTrue(matches(self.entry, disliked, FilterSpec(review="disliked")))

    def test_note_and_annotator_facets(self) -> None:
        self.assertFalse(matches(self.entry, ReviewState(note="  "), FilterSpec(has_note=True)))
        self.assertTrue(
            matches(self.entry, ReviewState(note="bad crop"), FilterSpec(has_note=True))
        )
        self.assertFalse(matches(self.entry, ReviewState(), FilterSpec(in_annotator=True)))
        self.assertTrue(
            matches(self.entry, ReviewState(in_annotator=True), FilterSpec(in_annotator=True))
        )

    def test_label_facet_or_within_and_across(self) -> None:
        spec = FilterSpec(labels={"Sentiment": {"negative", "neutral"}})
        self.assertTrue(matches(self.entry, self.liked, spec))
        pos = _entry("P", labels={"Sentiment": "positive"})
        self.assertFalse(matches(pos, self.liked, spec))
        # AND across columns: add an Angry constraint the entry fails.
        spec2 = FilterSpec(labels={"Sentiment": {"negative"}, "Angry": {"0.50"}})
        self.assertFalse(matches(self.entry, self.liked, spec2))


class TestApplyFilters(unittest.TestCase):
    def test_combined_filters(self) -> None:
        entries = [
            _entry("A", vdet=True, tracks=2, labels={"Sentiment": "negative"}),
            _entry("B", vdet=False, tracks=0, labels={"Sentiment": "positive"}),
            _entry("C", vdet=True, tracks=1, labels={"Sentiment": "negative"}),
        ]
        reviews = {"A": ReviewState(verdict="bad"), "C": ReviewState()}

        def review_of(key: str) -> ReviewState:
            return reviews.get(key, ReviewState())

        spec = FilterSpec(vdet="has", labels={"Sentiment": {"negative"}})
        kept = [e.video_id for e in apply_filters(entries, spec, review_of)]
        self.assertEqual(kept, ["A", "C"])

        spec_disliked = FilterSpec(review="disliked")
        kept2 = [e.video_id for e in apply_filters(entries, spec_disliked, review_of)]
        self.assertEqual(kept2, ["A"])

    def test_apply_filters_label_transform(self) -> None:
        entries = [
            _entry("A", labels={"sentiment": "1.0"}),
            _entry("B", labels={"sentiment": "-2.0"}),
        ]
        spec = FilterSpec(labels={"sentiment": {"positive"}})

        def label_of(entry: VideoEntry, column: str) -> str | None:
            raw = entry.labels.get(column)
            return None if raw is None else ("positive" if float(raw) > 0 else "negative")

        kept = [
            e.video_id
            for e in apply_filters(entries, spec, lambda _k: ReviewState(), label_of=label_of)
        ]
        self.assertEqual(kept, ["A"])

    def test_apply_filters_audio_lookup(self) -> None:
        entries = [_entry("A"), _entry("B"), _entry("C")]
        audio = {"A": True, "B": False}  # C unknown

        kept = [
            e.video_id
            for e in apply_filters(
                entries, FilterSpec(audio="has"), lambda _k: ReviewState(), audio.get
            )
        ]
        self.assertEqual(kept, ["A"])

    def test_apply_filters_id_list(self) -> None:
        entries = [_entry("A"), _entry("B"), _entry("C")]
        spec = FilterSpec(id_list={"C", "A", "missing"})

        kept = [e.video_id for e in apply_filters(entries, spec, lambda _k: ReviewState())]
        self.assertEqual(kept, ["A", "C"])  # manifest order, unknown ids ignored

    def test_id_list_combines_with_other_facets(self) -> None:
        entries = [_entry("A", vdet=True), _entry("B"), _entry("C")]
        spec = FilterSpec(id_list={"A", "B"}, vdet="has")

        kept = [e.video_id for e in apply_filters(entries, spec, lambda _k: ReviewState())]
        self.assertEqual(kept, ["A"])

    def test_id_list_is_a_facet(self) -> None:
        self.assertFalse(FilterSpec().is_active)
        self.assertTrue(FilterSpec(id_list={"A"}).is_active)


if __name__ == "__main__":
    unittest.main()
