"""Tests for the pure paging cursor behind Browse/Annotator row reveal."""

from __future__ import annotations

import unittest

from annie.pages.paging import Pager, index_of_row_id


class TestPagerAdvance(unittest.TestCase):
    def test_successive_pages_are_contiguous(self) -> None:
        pager = Pager(total=25)
        self.assertEqual(pager.advance(10), slice(0, 10))
        self.assertEqual(pager.advance(10), slice(10, 20))
        self.assertEqual(pager.advance(10), slice(20, 25))

    def test_final_page_clamps_to_total(self) -> None:
        pager = Pager(total=7)
        pager.advance(5)
        self.assertEqual(pager.advance(5), slice(5, 7))
        self.assertEqual(pager.shown, 7)

    def test_remaining_and_exhausted_track_progress(self) -> None:
        pager = Pager(total=12)
        self.assertEqual(pager.remaining, 12)
        self.assertFalse(pager.exhausted)
        pager.advance(5)
        self.assertEqual(pager.remaining, 7)
        self.assertFalse(pager.exhausted)
        pager.advance(50)
        self.assertEqual(pager.remaining, 0)
        self.assertTrue(pager.exhausted)

    def test_advance_past_the_end_is_a_no_op(self) -> None:
        pager = Pager(total=3)
        pager.advance(3)
        self.assertEqual(pager.advance(3), slice(3, 3))
        self.assertTrue(pager.exhausted)

    def test_non_positive_page_size_still_makes_progress(self) -> None:
        """A bad page_size must never yield an empty, non-terminating page."""
        for page_size in (0, -5):
            with self.subTest(page_size=page_size):
                pager = Pager(total=3)
                self.assertEqual(pager.advance(page_size), slice(0, 1))
                self.assertEqual(pager.remaining, 2)

    def test_empty_list_is_exhausted_immediately(self) -> None:
        pager = Pager(total=0)
        self.assertTrue(pager.exhausted)
        self.assertEqual(pager.advance(10), slice(0, 0))
        self.assertEqual(pager.remaining, 0)


class TestPagerSeek(unittest.TestCase):
    def test_seek_starts_the_next_page_at_the_target(self) -> None:
        pager = Pager(total=5000)
        pager.seek(3399)  # the user typed "3400"
        self.assertEqual(pager.start, 3399)
        self.assertEqual(pager.advance(10), slice(3399, 3409))
        self.assertEqual(pager.remaining, 5000 - 3409)

    def test_seek_discards_previous_progress(self) -> None:
        pager = Pager(total=100)
        pager.advance(30)
        pager.seek(50)
        self.assertEqual(pager.shown, 50)
        self.assertEqual(pager.advance(10), slice(50, 60))

    def test_seek_clamps_below_zero(self) -> None:
        pager = Pager(total=10)
        pager.seek(-7)
        self.assertEqual(pager.start, 0)
        self.assertEqual(pager.advance(2), slice(0, 2))

    def test_seek_past_the_end_lands_on_the_last_row(self) -> None:
        pager = Pager(total=10)
        pager.seek(999)
        self.assertEqual(pager.start, 9)
        self.assertEqual(pager.advance(5), slice(9, 10))
        self.assertTrue(pager.exhausted)

    def test_seek_on_an_empty_list_stays_at_zero(self) -> None:
        pager = Pager(total=0)
        pager.seek(4)
        self.assertEqual(pager.start, 0)
        self.assertTrue(pager.exhausted)


class TestIndexOfRowId(unittest.TestCase):
    """Row ids are dataset positions, so a filtered list has gaps in them."""

    def test_exact_hit_in_an_unfiltered_list(self) -> None:
        row_ids = [1, 2, 3, 4, 5]
        self.assertEqual(index_of_row_id(row_ids, 4), 3)

    def test_exact_hit_in_a_filtered_list(self) -> None:
        row_ids = [3, 17, 204, 3400]
        self.assertEqual(index_of_row_id(row_ids, 204), 2)
        self.assertEqual(index_of_row_id(row_ids, 3400), 3)

    def test_a_filtered_out_row_id_resolves_to_the_next_shown_row(self) -> None:
        row_ids = [3, 17, 204, 3400]
        self.assertEqual(index_of_row_id(row_ids, 18), 2, "18 is hidden -> land on 204")
        self.assertEqual(index_of_row_id(row_ids, 1), 0)

    def test_a_row_id_past_the_last_shown_row_clamps_to_the_end(self) -> None:
        row_ids = [3, 17, 204]
        self.assertEqual(index_of_row_id(row_ids, 9999), 2)

    def test_empty_list(self) -> None:
        self.assertEqual(index_of_row_id([], 42), 0)

    def test_drives_the_pager_to_the_right_page(self) -> None:
        """Typing 3400 into a filtered Browse tab pages from where 3400 would be."""
        row_ids = [1, 900, 3399, 3400, 3401, 5000]
        pager = Pager(total=len(row_ids))
        pager.seek(index_of_row_id(row_ids, 3400))
        page = pager.advance(2)
        self.assertEqual([row_ids[i] for i in range(page.start, page.stop)], [3400, 3401])


if __name__ == "__main__":
    unittest.main()
