from dataclasses import dataclass
from datetime import date, datetime, timezone

from weather_alpha.settlement.reconstruction import (
    StationClock,
    local_standard_settlement_window,
    reconstruct_daily_extreme,
    whole_f_candidates_for_reported_c,
)


@dataclass(frozen=True)
class Obs:
    valid_time: datetime
    temperature_f: float


def test_chicago_summer_lst_window_is_1am_to_1am_civil():
    clock = StationClock("KMDW", "America/Chicago", -6)
    window = local_standard_settlement_window(clock, date(2026, 7, 15))
    assert window.start_utc.isoformat() == "2026-07-15T06:00:00+00:00"
    assert window.end_utc.isoformat() == "2026-07-16T06:00:00+00:00"
    assert window.start_civil.hour == 1
    assert window.end_civil.hour == 1


def test_chicago_winter_lst_window_matches_midnight_civil():
    clock = StationClock("KMDW", "America/Chicago", -6)
    window = local_standard_settlement_window(clock, date(2026, 1, 15))
    assert window.start_utc.isoformat() == "2026-01-15T06:00:00+00:00"
    assert window.start_civil.hour == 0
    assert window.end_civil.hour == 0


def test_post_midnight_civil_observation_can_belong_to_prior_lst_day():
    clock = StationClock("KMDW", "America/Chicago", -6)
    # 05:30 UTC July 16 = 00:30 CDT July 16, but still inside July 15 LST day.
    obs = [Obs(datetime(2026, 7, 16, 5, 30, tzinfo=timezone.utc), 99.0)]
    result = reconstruct_daily_extreme(obs, clock=clock, settlement_date=date(2026, 7, 15), kind="high")
    assert result.observed_extreme_f == 99.0
    assert result.observations_used == 1


def test_public_whole_c_report_can_map_to_multiple_whole_f_values():
    candidates = whole_f_candidates_for_reported_c(47)
    assert 116 in candidates
    assert 117 in candidates
    assert 118 not in candidates


def test_reconstruction_residual_against_official_value():
    clock = StationClock("KNYC", "America/New_York", -5)
    obs = [
        Obs(datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc), 84.0),
        Obs(datetime(2026, 6, 10, 19, 0, tzinfo=timezone.utc), 86.0),
    ]
    result = reconstruct_daily_extreme(
        obs,
        clock=clock,
        settlement_date=date(2026, 6, 10),
        official_extreme_f=87.0,
        kind="high",
    )
    assert result.observed_extreme_f == 86.0
    assert result.residual_f == 1.0
    assert result.exact_match is False
    assert result.within_one_f is True
