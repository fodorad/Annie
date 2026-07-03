"""Browse 'Manipulate' transforms — derive a display/filter value per label column.

A label column carries raw CSV text plus a **data type** (``str`` / ``int`` /
``float``, chosen when the CSV is added). The Browse *Manipulate* block then applies
one **transform** per column, replacing its raw value for tags and filtering:

* text → ``trim`` (strip surrounding whitespace);
* numeric → ``round`` (to the nearest integer, e.g. sentiment ``1.0`` → ``1``),
  ``threshold`` (``≥X`` / ``<X`` buckets), or ``sign`` (``negative`` / ``zero`` /
  ``positive``).

The functions are pure and unit-tested; the UI only wires them up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

ColumnType = Literal["str", "int", "float"]
"""A column's data type."""
TransformKind = Literal["none", "trim", "round", "threshold", "sign"]
"""A transform a column can carry."""

#: Transforms offered for text columns (no numeric operations apply).
_TEXT_TRANSFORMS: tuple[TransformKind, ...] = ("none", "trim")
#: Transforms offered for numeric columns.
_NUMERIC_TRANSFORMS: tuple[TransformKind, ...] = ("none", "round", "threshold", "sign")

TRANSFORM_LABELS: dict[TransformKind, str] = {
    "none": "none",
    "trim": "trim spaces",
    "round": "round to X digits",
    "threshold": "threshold ≥ X",
    "sign": "sign (−/0/+)",
}
"""Short human labels for the transform dropdown."""


@dataclass(slots=True)
class Transform:
    """One column's transform.

    Attributes:
        kind: The transform to apply.
        threshold: The cut for the ``threshold`` transform.
        digits: Decimal places for the ``round`` transform (default 2).
    """

    kind: TransformKind = "none"
    threshold: float = 1.0
    digits: int = 2


def transforms_for(col_type: ColumnType) -> tuple[TransformKind, ...]:
    """Return the transforms valid for a column type."""
    return _TEXT_TRANSFORMS if col_type == "str" else _NUMERIC_TRANSFORMS


def _is_int(text: str) -> bool:
    try:
        int(text)
    except ValueError:
        return False
    return True


def _is_float(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def detect_type(values: Iterable[str]) -> ColumnType:
    """Guess a column's type from a sample of its (string) values.

    Args:
        values: Sample cell values.

    Returns:
        ``"int"`` if every non-empty value is an integer, ``"float"`` if every value
        is numeric, else ``"str"``. An all-empty/absent column defaults to ``"str"``.
    """
    seen = [v.strip() for v in values if v and v.strip()]
    if not seen:
        return "str"
    if all(_is_int(v) for v in seen):
        return "int"
    if all(_is_float(v) for v in seen):
        return "float"
    return "str"


def _fmt_threshold(value: float) -> str:
    """Format a threshold without trailing zeros (``1.0`` → ``1``)."""
    return f"{value:g}"


def apply_transform(value: str, col_type: ColumnType, transform: Transform) -> str:
    """Apply a column's transform to one raw value, returning its display value.

    A value that cannot be parsed as a number (in a numeric column) is returned
    unchanged, so a stray non-numeric cell never breaks the row.

    Args:
        value: The raw cell text.
        col_type: The column's data type.
        transform: The transform to apply.

    Returns:
        The transformed string.
    """
    if col_type == "str":
        return value.strip() if transform.kind == "trim" else value

    if transform.kind == "none":
        return value
    try:
        number = float(value)
    except ValueError:
        return value
    if transform.kind == "round":
        d = max(0, int(transform.digits))
        return str(round(number, d)) if d > 0 else str(int(round(number)))
    if transform.kind == "threshold":
        cut = _fmt_threshold(transform.threshold)
        return f"≥{cut}" if number >= transform.threshold else f"<{cut}"
    if transform.kind == "sign":
        if number > 0:
            return "positive"
        if number < 0:
            return "negative"
        return "zero"
    return value
