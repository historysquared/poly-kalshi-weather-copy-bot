from datetime import datetime, timezone

from weather_alpha.providers.surface import IemAsosOneMinuteArchive


def test_iem_station_normalization():
    assert IemAsosOneMinuteArchive._iem_station("KMDW") == "MDW"
    assert IemAsosOneMinuteArchive._iem_station("MDW") == "MDW"


def test_iso_z():
    dt = datetime(2026, 6, 11, 6, 0, tzinfo=timezone.utc)
    assert IemAsosOneMinuteArchive._iso_z(dt) == "2026-06-11T06:00:00Z"


def test_parse_time_accepts_iem_valid_utc_format():
    parsed = IemAsosOneMinuteArchive._parse_time("2026-06-11 06:01")
    assert parsed == datetime(2026, 6, 11, 6, 1, tzinfo=timezone.utc)
