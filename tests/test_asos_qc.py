from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from weather_alpha.quality.asos_qc import QCConfig, QCStatus, assess_asos_temperature_quality


@dataclass(frozen=True)
class Obs:
    valid_time: datetime
    temperature_f: float


def _t(minute: int) -> datetime:
    return datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def test_good_series_passes():
    observations = [Obs(_t(0), 88.0), Obs(_t(5), 88.5), Obs(_t(10), 89.0)]
    result = assess_asos_temperature_quality(observations, as_of=_t(12))
    assert result.status == QCStatus.PASS
    assert result.live_trading_allowed is True


def test_stale_station_fails_closed():
    observations = [Obs(_t(0), 88.0), Obs(_t(5), 88.5)]
    result = assess_asos_temperature_quality(observations, as_of=_t(40))
    assert result.status == QCStatus.FAIL
    assert "STALE_STATION_FAIL" in result.reason_codes
    assert result.live_trading_allowed is False


def test_implausible_jump_fails():
    observations = [Obs(_t(0), 70.0), Obs(_t(1), 80.0)]
    result = assess_asos_temperature_quality(observations, as_of=_t(2))
    assert result.status == QCStatus.FAIL
    assert "IMPLAUSIBLE_TEMPERATURE_JUMP" in result.reason_codes


def test_large_gap_fails():
    observations = [Obs(_t(0), 80.0), Obs(_t(40), 82.0)]
    result = assess_asos_temperature_quality(observations, as_of=_t(41))
    assert result.status == QCStatus.FAIL
    assert "OBSERVATION_GAP_FAIL" in result.reason_codes


def test_flatline_can_be_tuned_and_fails_closed():
    cfg = QCConfig(flatline_minutes_warn=5, flatline_minutes_fail=10, stale_minutes_fail=60)
    observations = [Obs(_t(0), 75.0), Obs(_t(5), 75.0), Obs(_t(10), 75.0)]
    result = assess_asos_temperature_quality(observations, as_of=_t(11), config=cfg)
    assert result.status == QCStatus.FAIL
    assert "TEMPERATURE_FLATLINE_FAIL" in result.reason_codes


def test_duplicate_timestamp_warns_not_silently_passes():
    observations = [Obs(_t(0), 75.0), Obs(_t(0), 75.0), Obs(_t(5), 75.5)]
    result = assess_asos_temperature_quality(observations, as_of=_t(6))
    assert result.status == QCStatus.WARN
    assert "DUPLICATE_TIMESTAMP" in result.reason_codes
