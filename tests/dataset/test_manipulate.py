"""Tests for the Browse label transforms (Manipulate block)."""

from __future__ import annotations

import unittest

from annie.dataset.manipulate import Transform, apply_transform, detect_type, transforms_for


class TestDetectType(unittest.TestCase):
    def test_int_float_str(self) -> None:
        self.assertEqual(detect_type(["0", "1", "1"]), "int")
        self.assertEqual(detect_type(["1.000000", "0.0", "-3"]), "float")
        self.assertEqual(detect_type(["valid", "train"]), "str")

    def test_mixed_falls_back_to_str(self) -> None:
        self.assertEqual(detect_type(["1", "two"]), "str")

    def test_empty_defaults_str(self) -> None:
        self.assertEqual(detect_type(["", "  ", ""]), "str")


class TestTransformsFor(unittest.TestCase):
    def test_options_by_type(self) -> None:
        self.assertEqual(transforms_for("str"), ("none", "trim"))
        self.assertEqual(transforms_for("float"), ("none", "round", "threshold", "sign"))
        self.assertEqual(transforms_for("int"), ("none", "round", "threshold", "sign"))


class TestApplyTransform(unittest.TestCase):
    def test_str_trim(self) -> None:
        self.assertEqual(apply_transform("  hi  ", "str", Transform("trim")), "hi")
        self.assertEqual(apply_transform("  hi  ", "str", Transform("none")), "  hi  ")

    def test_round(self) -> None:
        # Default digits=2
        self.assertEqual(apply_transform("1.000000", "float", Transform("round")), "1.0")
        self.assertEqual(apply_transform("-2.4", "float", Transform("round")), "-2.4")
        self.assertEqual(apply_transform("0.666667", "float", Transform("round")), "0.67")
        # digits=0 → integer-style
        t0 = Transform("round", digits=0)
        self.assertEqual(apply_transform("1.000000", "float", t0), "1")
        self.assertEqual(apply_transform("0.666667", "float", t0), "1")
        # digits=4
        t4 = Transform("round", digits=4)
        self.assertEqual(apply_transform("0.666667", "float", t4), "0.6667")

    def test_threshold(self) -> None:
        t = Transform("threshold", threshold=1.0)
        self.assertEqual(apply_transform("1.0", "float", t), "≥1")
        self.assertEqual(apply_transform("0.66", "float", t), "<1")

    def test_sign(self) -> None:
        s = Transform("sign")
        self.assertEqual(apply_transform("2.0", "float", s), "positive")
        self.assertEqual(apply_transform("-0.6", "float", s), "negative")
        self.assertEqual(apply_transform("0", "float", s), "zero")

    def test_non_numeric_passthrough(self) -> None:
        self.assertEqual(apply_transform("n/a", "float", Transform("round")), "n/a")


if __name__ == "__main__":
    unittest.main()
