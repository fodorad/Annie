"""Tests for the pending slot: spinner while loading, then content or an error icon."""

from __future__ import annotations

import unittest

from nicegui import ui

from annie.pages import pending
from tests.pages._nicegui import ui_client


def _spinners(box: ui.element) -> int:
    """How many spinners sit anywhere inside ``box``."""
    return sum(1 for el in box.descendants() if el.tag == "q-spinner")


class TestPendingSlot(unittest.TestCase):
    def test_pending_empties_the_box_and_shows_one_spinner(self) -> None:
        with ui_client():
            box = pending.media_box(100, 60)
            with box:
                ui.label("old")
            pending.pending(box)
            pending.pending(box)  # a repeat never stacks a second spinner
            self.assertEqual(_spinners(box), 1)
            self.assertEqual(_labels(box), [])

    def test_keep_overlays_the_old_content(self) -> None:
        with ui_client():
            box = pending.media_box(100, 60)
            with box:
                old = ui.label("old frame")
            pending.pending(box, keep=True)
            self.assertIn(old, box.default_slot.children)
            self.assertEqual(_spinners(box), 1)
            self.assertTrue(pending.is_pending(box))

    def test_ready_replaces_spinner_and_old_content(self) -> None:
        with ui_client():
            box = pending.media_box(100, 60)
            with box:
                ui.label("old")
            pending.pending(box, keep=True)
            pending.ready(box, lambda: ui.label("new"))
            self.assertEqual(_spinners(box), 0)
            self.assertFalse(pending.is_pending(box))
            self.assertEqual([el.text for el in _labels(box)], ["new"])

    def test_failed_shows_the_error_icon_with_its_reason(self) -> None:
        with ui_client():
            box = pending.media_box(100, 60)
            pending.pending(box)
            pending.failed(box, "cannot decode")
            self.assertEqual(_spinners(box), 0)
            icons = [el for el in box.descendants() if el.tag == "q-icon"]
            self.assertEqual(len(icons), 1)
            tips = [el for el in box.descendants() if el.tag == "q-tooltip"]
            self.assertEqual(tips[0].text, "cannot decode")

    def test_every_call_is_a_no_op_on_a_deleted_box(self) -> None:
        with ui_client():
            box = pending.media_box(100, 60)
            box.delete()
            pending.pending(box)
            pending.pending(box, keep=True)
            pending.ready(box, lambda: ui.label("x"))
            pending.failed(box, "x")
            self.assertFalse(pending.is_pending(box))


class TestPendingBadge(unittest.TestCase):
    def test_badge_spins_until_settled(self) -> None:
        with ui_client():
            slot = pending.pending_badge("#frames")
            self.assertEqual(_spinners(slot), 1)
            pending.settle_badge(slot, "#frames: 12")
            self.assertEqual(_spinners(slot), 0)
            badges = [el for el in slot.descendants() if el.tag == "q-badge"]
            self.assertEqual([b.text for b in badges], ["#frames: 12"])

    def test_settling_a_deleted_badge_is_a_no_op(self) -> None:
        with ui_client():
            slot = pending.pending_badge("audio")
            slot.delete()
            pending.settle_badge(slot, "audio: ?")


def _labels(box: ui.element) -> list[ui.label]:
    return [el for el in box.descendants() if isinstance(el, ui.label)]


if __name__ == "__main__":
    unittest.main()
