"""Tests for the Annotator's role-driven task selection.

The Annotator offers only the tasks whose sources are present (see
:func:`annie.dataset.sources.task_readiness`) and remembers which one each client is
working. These tests drive that selection logic directly against a registry, without a
browser, since it is plain state over the source registry.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annie.core.state import state
from annie.dataset.sources import (
    CsvRole,
    DataSource,
    SegmentationBand,
    SourceKind,
    SourceRegistry,
    TaskKind,
)
from annie.pages import annotator
from tests.fixtures import write_table, write_video


class TestReadyTasks(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_registry = state.registry
        self.root = Path(tempfile.mkdtemp())
        state.registry = SourceRegistry()

    def tearDown(self) -> None:
        state.registry = self._saved_registry

    def _add_video(self) -> None:
        write_video(self.root / "v", "A")
        state.registry.add(DataSource(SourceKind.VIDEO, self.root / "v"))

    def _add_segmentation(self) -> None:
        band = write_table(
            self.root / "review_band.csv",
            ["video_id", "segment_id", "gt_start_sec", "gt_end_sec"],
            [{"video_id": "A", "segment_id": "0", "gt_start_sec": "1.0", "gt_end_sec": "2.0"}],
        )
        state.registry.add(
            DataSource(
                SourceKind.CSV,
                band,
                role=CsvRole.SEGMENTATION,
                key_column="video_id",
                segment_column="segment_id",
                bands=(SegmentationBand("GT", "gt_start_sec", "gt_end_sec"),),
            )
        )

    def test_only_curation_with_bare_video(self) -> None:
        self._add_video()
        ready = annotator._ready_tasks()  # noqa: SLF001
        self.assertIn(TaskKind.CURATION, ready)
        self.assertNotIn(TaskKind.PROTAGONIST, ready)
        self.assertNotIn(TaskKind.SEGMENT_REVIEW, ready)

    def test_segment_review_offered_with_segmentation_source(self) -> None:
        self._add_video()
        self._add_segmentation()
        self.assertIn(TaskKind.SEGMENT_REVIEW, annotator._ready_tasks())  # noqa: SLF001

    def test_no_ready_tasks_with_empty_registry(self) -> None:
        self.assertEqual(annotator._ready_tasks(), [])  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
