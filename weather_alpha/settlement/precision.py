from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class PrecisionPolicy:
    """Preserve native weather precision until a venue rule explicitly transforms it."""

    source_name: str
    native_increment_f: Decimal | None = None
    native_increment_c: Decimal | None = None
    allow_implicit_rounding: bool = False


def as_decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if not text or text.upper() in {"M", "MM", "NULL", "NONE", "NAN"}:
        return None
    return Decimal(text)


def extreme_decimal(values: Iterable[object], *, kind: str) -> Decimal | None:
    parsed = [v for raw in values if (v := as_decimal(raw)) is not None]
    if not parsed:
        return None
    if kind == "high":
        return max(parsed)
    if kind == "low":
        return min(parsed)
    raise ValueError("kind must be 'high' or 'low'")


def difference(a: object | None, b: object | None) -> Decimal | None:
    da = as_decimal(a)
    db = as_decimal(b)
    return None if da is None or db is None else da - db
