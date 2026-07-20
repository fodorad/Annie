"""Tests for the Annotator's Segment-review task: accept/drop persistence + jump.

These drive the task's decision, undecide, and jump helpers against a real registry and
store, asserting the decision is written to the review DB, that deciding stays on the clip
(no auto-advance), that "Undecided" puts a clip back in the pool, and that "jump to next
undecided" walks the backlog — all without needing a browser to decode frames.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from nicegui import core, ui

from annie.core.state import state
from annie.dataset.scanning import ScanResult
from annie.dataset.segments import next_undecided_index
from annie.dataset.sources import (
    CsvRole,
    DataSource,
    SegmentationBand,
    SourceKind,
    SourceRegistry,
)
from annie.dataset.storage import ReviewStore
from annie.pages import annotator
from tests.fixtures import write_table
from tests.pages._nicegui import quiet_slow_callback_warnings, ui_client


class TestSegmentReviewFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved_loop = core.loop
        core.loop = asyncio.get_running_loop()
        quiet_slow_callback_warnings()

    async def asyncTearDown(self) -> None:
        core.loop = self._saved_loop

    def setUp(self) -> None:
        self._saved = (state.store, state.scan, state.registry)
        self.root = Path(tempfile.mkdtemp())
        state.store = ReviewStore(self.root / "annie.db")
        state.scan = ScanResult(entries=[])
        band = write_table(
            self.root / "review_band.csv",
            ["video_id", "segment_id", "gt_start_sec", "gt_end_sec", "gt_text"],
            [
                {
                    "video_id": "v",
                    "segment_id": "0",
                    "gt_start_sec": "1.0",
                    "gt_end_sec": "2.0",
                    "gt_text": "a",
                },
                {
                    "video_id": "v",
                    "segment_id": "1",
                    "gt_start_sec": "3.0",
                    "gt_end_sec": "4.0",
                    "gt_text": "b",
                },
            ],
        )
        reg = SourceRegistry()
        reg.add(
            DataSource(
                SourceKind.CSV,
                band,
                role=CsvRole.SEGMENTATION,
                key_column="video_id",
                segment_column="segment_id",
                bands=(SegmentationBand("GT", "gt_start_sec", "gt_end_sec"),),
                value_columns=("gt_text",),
            )
        )
        state.registry = reg

    def tearDown(self) -> None:
        state.store, state.scan, state.registry = self._saved
        annotator._segment_states.clear()  # noqa: SLF001

    async def test_decide_persists_and_jump_then_export(self) -> None:
        with ui_client(), ui.column():
            annotator._segment_review_task()  # noqa: SLF001 - builds + loads clips
            st = annotator._segment_state()  # noqa: SLF001
            self.assertEqual(len(st.clips), 2)
            self.assertEqual(st.index, 0)

            first, second = st.clips[0], st.clips[1]
            # accept the first clip → stored, but the cursor stays put (no auto-advance)
            annotator._decide(first.key, first.video_id, "accept")  # noqa: SLF001
            self.assertEqual(state.store.decisions()[first.key], "accept")
            self.assertEqual(st.index, 0)

            # jump to the next undecided → cursor moves to the still-undecided second clip
            annotator._jump_to_undecided(st)  # noqa: SLF001
            self.assertEqual(st.index, 1)

            # drop the second clip → stored; a jump now has nowhere to go and is inert
            annotator._decide(second.key, second.video_id, "drop")  # noqa: SLF001
            self.assertEqual(state.store.decisions()[second.key], "drop")
            annotator._jump_to_undecided(st)  # noqa: SLF001
            self.assertEqual(st.index, 1)

            # export writes the two files beside the source
            annotator._segment_export()  # noqa: SLF001
        accepted = self.root / "review_band_accepted.csv"
        dropped = self.root / "review_band_dropped.csv"
        self.assertIn(first.key, accepted.read_text(encoding="utf-8"))
        self.assertIn(second.key, dropped.read_text(encoding="utf-8"))

    async def test_undecide_clears_the_verdict_and_reopens_the_jump(self) -> None:
        with ui_client(), ui.column():
            annotator._segment_review_task()  # noqa: SLF001
            st = annotator._segment_state()  # noqa: SLF001
            first, second = st.clips[0], st.clips[1]

            annotator._decide(first.key, first.video_id, "accept")  # noqa: SLF001
            annotator._decide(second.key, second.video_id, "drop")  # noqa: SLF001
            # Everything decided → nothing to jump to.
            self.assertIsNone(
                next_undecided_index(st.clips, state.store.decisions(), st.index),
            )

            # Undo the first clip's verdict — it returns to the undecided pool.
            annotator._undecide(first.key)  # noqa: SLF001
            self.assertNotIn(first.key, state.store.decisions())
            self.assertEqual(
                next_undecided_index(st.clips, state.store.decisions(), st.index),
                0,
            )

    async def test_reopening_resumes_decisions_from_store(self) -> None:
        # Pre-seed a decision as though a prior pass saved it.
        state.store.set_decision("v_0", "v", "accept")
        with ui_client(), ui.column():
            annotator._segment_review_task()  # noqa: SLF001
            decisions = state.store.decisions()
            self.assertEqual(decisions.get("v_0"), "accept")  # survives a fresh build


if __name__ == "__main__":
    unittest.main()
