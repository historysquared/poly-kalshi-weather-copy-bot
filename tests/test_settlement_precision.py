from decimal import Decimal

from weather_alpha.settlement.precision import as_decimal, difference, extreme_decimal


def test_decimal_helpers_do_not_round_source_values():
    assert as_decimal("82.1") == Decimal("82.1")
    assert as_decimal("82.09") == Decimal("82.09")
    assert difference("82.1", "81.94") == Decimal("0.16")


def test_extreme_decimal_preserves_native_precision():
    values = ["81.9", "82.04", "82.1", "81.95"]
    assert extreme_decimal(values, kind="high") == Decimal("82.1")
    assert extreme_decimal(values, kind="low") == Decimal("81.9")
