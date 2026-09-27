from weather_alpha.backtest.validation import EventReturn, Verdict, validate_strategy


def rows_for_dates(n_dates: int):
    rows = []
    for i in range(n_dates):
        day = f"d{i:03d}"
        for j in range(2):
            rows.append(EventReturn(
                strategy="test", event_id=f"e{i}_{j}", date=day,
                station="KAAA" if j == 0 else "KBBB", latency_seconds=300,
                execution_case="DEPTH_5", pnl=1.0, capital=1.0,
            ))
    return rows


def test_positive_22_date_result_is_provisional_not_keep():
    result = validate_strategy(rows_for_dates(22))
    assert result.verdict == Verdict.PROVISIONAL
    assert "50 independent" in result.reason


def test_positive_50_date_result_can_keep():
    result = validate_strategy(rows_for_dates(50))
    assert result.verdict == Verdict.KEEP
