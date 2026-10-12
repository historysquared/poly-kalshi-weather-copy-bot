from argparse import Namespace
from decimal import Decimal

from scripts.run_weather_company_diagnostic_track import evaluate


def test_diagnostic_does_not_emit_when_required_slope_is_missing():
    row = {
        "yes_ask": "0.20",
        "no_ask": "0.80",
        "in_bucket_now": True,
        "minutes_since_high": "75",
        "drop_from_high_f": "1.5",
        "slope_15m_f_per_min": None,
    }
    args = Namespace(
        control_min_minutes_since_high=Decimal("60"),
        control_min_drop_from_high_f=Decimal("1.0"),
        control_max_positive_slope=Decimal("0.02"),
        price_floor=Decimal("0.10"),
        minimum_edge=Decimal("0.04"),
    )
    result = evaluate(row, args)
    assert result is not None
    assert result["tournament_decision"] == "DATA_INCOMPLETE"
    assert "LOCK_FAIL_SLOPE_MISSING" in result["control_lock_failures"]
