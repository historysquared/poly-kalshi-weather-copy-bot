from scripts.telegram_weather_paper_watcher import format_weather_event


def test_weather_signal_format_has_manual_review_fields():
    payload = {
        "signal_time": "2026-09-11T22:34:18.176191+00:00",
        "track": "C_EXPLORATORY",
        "station": "KNYC",
        "ticker": "KXHIGHNY-26SEP11-B78.5",
        "side": "NO",
        "tournament_entry_ask": "0.31",
        "tournament_probability_side": "0.58",
        "tournament_gross_edge": "0.27",
        "tournament_net_edge": "0.25",
        "latest_temp_f": "77.0",
        "high_so_far_f": "78.0",
        "minutes_since_high": "41.3",
        "drop_from_high_f": "1.0",
        "slope_15m_f_per_min": "-0.03",
        "track_lock_reasons": ["MINUTES_SINCE_HIGH"],
    }
    text = format_weather_event("TOURNAMENT_SIGNAL", payload, "America/Los_Angeles")
    assert "KNYC" in text
    assert "NO @ 0.310" in text
    assert "C_EXPLORATORY" in text
    assert "Temp 77.00F | High 78.00F" in text
    assert "Since high 41.3m" in text
    assert "PAPER ONLY" in text
