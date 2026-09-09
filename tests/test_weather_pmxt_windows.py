from datetime import date, datetime, timezone

from weather_alpha.backtest.weather_pmxt_windows import derive_weather_pmxt_hours, floor_hour, unique_hours


def test_floor_hour_utc():
    dt = datetime(2026, 6, 11, 22, 47, 33, tzinfo=timezone.utc)
    assert floor_hour(dt) == datetime(2026, 6, 11, 22, 0, tzinfo=timezone.utc)


def test_derive_final_six_hours_deduplicates_contract_event():
    rows = [
        {"status": "EXACT", "station": "KMDW", "settlement_date": "2026-06-11", "contract_id": "A"},
        {"status": "EXACT", "station": "KMDW", "settlement_date": "2026-06-11", "contract_id": "B"},
    ]
    hours = derive_weather_pmxt_hours(rows, final_hours=6)
    assert len(hours) == 6
    assert len(unique_hours(hours)) == 6
    assert all(h.station == "KMDW" and h.settlement_date == date(2026, 6, 11) for h in hours)


def test_non_exact_rows_ignored():
    rows = [{"status": "PROBABLE", "station": "KMDW", "settlement_date": "2026-06-11", "contract_id": "A"}]
    assert derive_weather_pmxt_hours(rows) == []
