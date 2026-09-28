from scripts.run_live_alpha_shadow import ladder_signals, monotonic_signals


def base_row(shape, ticker, yes_ask, no_ask, lower=None, upper=None, high=80.0):
    return {
        "snapshot_time": "2026-09-28T12:00:00+00:00",
        "event_id": "EVT", "ticker": ticker, "station": "KXYZ", "settlement_date": "2026-09-28",
        "shape": shape, "lower": lower, "upper": upper, "high_so_far_f": high,
        "yes_ask": yes_ask, "no_ask": no_ask, "estimated_taker_fee_per_contract": 0.0,
        "source_family": "WEATHER_COMPANY",
    }


def test_yes_ladder_underround_emits_locked_basket():
    rows = [
        base_row("below", "A", 0.20, 0.81, upper=70),
        base_row("bucket", "B", 0.25, 0.76, lower=70, upper=75),
        base_row("above", "C", 0.45, 0.56, lower=75),
    ]
    signals = ladder_signals(rows, rows[0]["snapshot_time"], 0.02)
    yes = [x for x in signals if x["strategy"] == "LADDER_YES_UNDERROUND_V1"]
    assert len(yes) == 1
    assert round(yes[0]["net_edge_after_fee"], 6) == 0.10


def test_incomplete_ladder_does_not_emit():
    rows = [base_row("below", "A", 0.2, 0.8, upper=70), base_row("above", "C", 0.7, 0.3, lower=75)]
    assert ladder_signals(rows, rows[0]["snapshot_time"], 0.01) == []


def test_monotonic_bucket_elimination_requires_buffer_and_price_gap():
    row = base_row("bucket", "B", 0.2, 0.85, lower=70, upper=75, high=77.2)
    signals = monotonic_signals([row], row["snapshot_time"], 2.0, 0.95, 0.03)
    assert len(signals) == 1
    assert signals[0]["side"] == "NO"
    assert signals[0]["strategy"] == "MONOTONIC_HIGH_LOCK_V1"
    row["high_so_far_f"] = 76.9
    assert monotonic_signals([row], row["snapshot_time"], 2.0, 0.95, 0.03) == []
