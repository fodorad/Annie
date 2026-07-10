"""Tests for the off-screen unload gate behind Browse/Annotator row recycling."""

from __future__ import annotations

import unittest

from annie.pages.viewport import OffscreenGate


class TestOffscreenGate(unittest.TestCase):
    def test_starts_loaded(self) -> None:
        self.assertTrue(OffscreenGate().loaded)

    def test_hide_then_expire_drops_the_images(self) -> None:
        gate = OffscreenGate()
        generation = gate.hide()
        assert generation is not None
        self.assertTrue(gate.expire(generation))
        self.assertFalse(gate.loaded)

    def test_a_row_that_returns_before_the_delay_is_not_dropped(self) -> None:
        """Normal scrolling must never trigger a redecode."""
        gate = OffscreenGate()
        generation = gate.hide()
        assert generation is not None
        self.assertFalse(gate.show(), "still loaded, so nothing to rebuild")
        self.assertFalse(gate.expire(generation), "the stale unload must not fire")
        self.assertTrue(gate.loaded)

    def test_show_after_a_drop_requests_a_rebuild(self) -> None:
        gate = OffscreenGate()
        generation = gate.hide()
        assert generation is not None
        gate.expire(generation)
        self.assertTrue(gate.show(), "images were dropped, so they must be rebuilt")
        self.assertTrue(gate.loaded)

    def test_show_while_loaded_does_not_rebuild(self) -> None:
        gate = OffscreenGate()
        self.assertFalse(gate.show())

    def test_hide_twice_only_the_newest_unload_wins(self) -> None:
        gate = OffscreenGate()
        first = gate.hide()
        second = gate.hide()
        assert first is not None and second is not None
        self.assertNotEqual(first, second)
        self.assertFalse(gate.expire(first), "superseded by a later visibility change")
        self.assertTrue(gate.expire(second))

    def test_hide_when_already_unloaded_arms_nothing(self) -> None:
        gate = OffscreenGate()
        generation = gate.hide()
        assert generation is not None
        gate.expire(generation)
        self.assertIsNone(gate.hide(), "nothing left to drop")

    def test_expire_is_idempotent(self) -> None:
        gate = OffscreenGate()
        generation = gate.hide()
        assert generation is not None
        self.assertTrue(gate.expire(generation))
        self.assertFalse(gate.expire(generation), "must not drop twice")

    def test_scroll_thrash_leaves_the_row_loaded(self) -> None:
        """Flicking a row in and out repeatedly settles on 'loaded, nothing pending'."""
        gate = OffscreenGate()
        stale = []
        for _ in range(5):
            generation = gate.hide()
            if generation is not None:
                stale.append(generation)
            gate.show()
        for generation in stale:
            self.assertFalse(gate.expire(generation))
        self.assertTrue(gate.loaded)

    def test_full_cycle_drop_then_restore_then_drop_again(self) -> None:
        gate = OffscreenGate()
        first = gate.hide()
        assert first is not None
        self.assertTrue(gate.expire(first))
        self.assertTrue(gate.show())
        second = gate.hide()
        assert second is not None
        self.assertTrue(gate.expire(second))
        self.assertFalse(gate.loaded)


if __name__ == "__main__":
    unittest.main()
