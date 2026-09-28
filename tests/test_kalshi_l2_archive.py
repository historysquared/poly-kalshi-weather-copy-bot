from pathlib import Path

from scripts.record_kalshi_weather_l2_archive import (
    health_template,
    load_all_weather_catalog_tickers,
    load_ticker_cache,
    market_series,
    save_ticker_cache,
)
from weather_alpha.markets.kalshi_weather_series import (
    ALL_DAILY_TEMPERATURE_SERIES, ALL_HIGH_SERIES, LOW_SERIES_BY_CITY, SERIES_BY_CITY,
)


def test_weather_series_registry_is_exact_and_unique():
    assert len(SERIES_BY_CITY) == 24
    assert len(ALL_HIGH_SERIES) == 24
    assert len(set(ALL_HIGH_SERIES)) == 24
    assert "KXHIGHINFLATION" not in ALL_HIGH_SERIES
    assert SERIES_BY_CITY["trenton"] == "KXHIGHTTTN"
    assert SERIES_BY_CITY["louisville"] == "KXHIGHTSDF"
    assert SERIES_BY_CITY["newark"] == "KXHIGHTEWR"
    assert SERIES_BY_CITY["san_diego"] == "KXHIGHTSAN"


def test_daily_temperature_collection_includes_full_verified_low_family():
    assert len(LOW_SERIES_BY_CITY) == 24
    assert len(set(LOW_SERIES_BY_CITY.values())) == 24
    assert LOW_SERIES_BY_CITY["atlanta"] == "KXLOWTATL"
    assert LOW_SERIES_BY_CITY["san_diego"] == "KXLOWTSAN"
    assert LOW_SERIES_BY_CITY["trenton"] == "KXLOWTTTN"
    assert len(ALL_DAILY_TEMPERATURE_SERIES) == 48


def test_market_series_prefers_explicit_series_then_event_prefix():
    assert market_series({"series_ticker": "kxhighny", "event_ticker": "WRONG-26SEP27"}) == "KXHIGHNY"
    assert market_series({"event_ticker": "KXHIGHCHI-26SEP27"}) == "KXHIGHCHI"


def test_ticker_cache_roundtrip(tmp_path: Path):
    path = tmp_path / "tickers.json"
    save_ticker_cache(path, {"B", "A", "A"})
    assert load_ticker_cache(path) == ["A", "B"]


def test_health_template_is_read_only():
    health = health_template(list(ALL_DAILY_TEMPERATURE_SERIES))
    assert health["read_only"] is True
    assert health["live_order_submission"] is False
    assert health["series_count"] == 48


def test_load_all_weather_catalog_tickers(tmp_path: Path):
    path = tmp_path / "catalog.json"
    path.write_text('{"active_series":[{"markets":[{"ticker":"B"},{"ticker":"A"}]},{"markets":[{"ticker":"A"}]}]}')
    assert load_all_weather_catalog_tickers(path) == ["A", "B"]
