import json

import pandas as pd

from scripts.study_contrail_q90_bucket_edge import attach_rolling_q90, load_rows


def test_missing_contrail_day_is_not_converted_to_zero(tmp_path):
    contrails = [{
        "location_key": "miami", "local_date": "2026-09-01",
        "hourly_counts": {"18": 3, "19": 4, "20": 5}, "error": None,
    }]
    nbm = [
        {"location_key": "miami", "date": "2026-09-02", "nbm_max_f": 90, "nbm_max_sd_f": 2},
        {"location_key": "miami", "date": "2026-09-03", "nbm_max_f": 90, "nbm_max_sd_f": 2},
    ]
    bucket = lambda day: {
        "city": "miami", "date": day, "validation_ok": True, "expiration_value": 89,
        "contracts": [], "winner_contracts": ["WIN"],
    }
    paths = []
    for name, value in [("c.json", contrails), ("n.json", nbm), ("b.json", [bucket("2026-09-02"), bucket("2026-09-03")])]:
        path = tmp_path / name
        path.write_text(json.dumps(value))
        paths.append(path)
    df = load_rows(*paths)
    assert len(df) == 1
    assert df.iloc[0].feature_count == 12


def test_top90_uses_tie_aware_rolling_percentile():
    dates = pd.date_range("2026-08-01", periods=22, freq="D")
    df = pd.DataFrame({
        "city": ["miami"] * 22,
        "date": dates,
        "feature_count": [0.0] * 20 + [0.0, 10.0],
    })
    out = attach_rolling_q90(df, lookback_days=90, min_history_days=20)
    zero_row = out.iloc[20]
    high_row = out.iloc[21]
    assert zero_row.q90 == 0.0
    assert zero_row.rolling_percentile == 50.0
    assert not bool(zero_row.is_top90)
    assert high_row.rolling_percentile > 90.0
    assert bool(high_row.is_top90)
