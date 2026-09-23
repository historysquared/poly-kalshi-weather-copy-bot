from datetime import date

from weather_alpha.backtest.bulk_weather_pmxt import event_archive_hours, plan_archive_hours


def test_event_archive_hours_are_final_six_lst_hours():
    hours = event_archive_hours(station="KNYC", settlement_date=date(2026, 6, 11), final_hours=6)
    assert len(hours) == 6
    assert [h.hour_utc.hour for h in hours] == [23, 0, 1, 2, 3, 4]
    assert hours[0].hour_utc.date().isoformat() == "2026-06-11"
    assert hours[-1].hour_utc.date().isoformat() == "2026-06-12"


def test_plan_dedupes_bucket_contracts_for_same_event():
    rows = [
        {"status":"EXACT", "station":"KNYC", "settlement_date":"2026-06-11", "contract_id":"A"},
        {"status":"EXACT", "station":"KNYC", "settlement_date":"2026-06-11", "contract_id":"B"},
        {"status":"PROBABLE", "station":"KNYC", "settlement_date":"2026-06-12", "contract_id":"C"},
    ]
    plan = plan_archive_hours(rows, final_hours=6)
    assert len(plan) == 6
    assert len({x.hour_utc for x in plan}) == 6


def test_plan_dedupes_shared_utc_hours_across_stations():
    rows = [
        {"status":"EXACT", "station":"KNYC", "settlement_date":"2026-06-11", "contract_id":"A"},
        {"status":"EXACT", "station":"KMIA", "settlement_date":"2026-06-11", "contract_id":"B"},
    ]
    plan = plan_archive_hours(rows, final_hours=6)
    assert len(plan) == 6
