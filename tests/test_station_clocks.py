from datetime import date

from weather_alpha.settlement.reconstruction import local_standard_settlement_window
from weather_alpha.settlement.stations import station_clock


def test_chicago_summer_lst_window_is_one_am_to_one_am_civil():
    clock = station_clock("KMDW")
    window = local_standard_settlement_window(clock, date(2026, 6, 11))
    assert window.start_utc.isoformat() == "2026-06-11T06:00:00+00:00"
    assert window.end_utc.isoformat() == "2026-06-12T06:00:00+00:00"
    assert window.start_civil.hour == 1
    assert window.end_civil.hour == 1


def test_unknown_station_fails_closed():
    try:
        station_clock("KZZZ")
    except KeyError as exc:
        assert "no verified settlement clock" in str(exc)
    else:
        raise AssertionError("unknown station should fail closed")
