from pathlib import Path

from scripts.record_kalshi_weather_l2_archive import (
    health_template,
    load_ticker_cache,
    market_series,
    save_ticker_cache,
)
from weather_alpha.markets.kalshi_weather_series import ALL_HIGH_SERIES, SERIES_BY_CITY


def test_weather_series_registry_is_exact_and_unique():
    assert len(SERIES_BY_CITY) == 20
    assert len(ALL_HIGH_SERIES) == 20
    assert len(set(ALL_HIGH_SERIES)) == 20
    assert "KXHIGHINFLATION" not in ALL_HIGH_SERIES


def test_market_series_prefers_explicit_series_then_event_prefix():
    assert market_series({"series_ticker": "kxhighny", "event_ticker": "WRONG-26SEP27"}) == "KXHIGHNY"
    assert market_series({"event_ticker": "KXHIGHCHI-26SEP27"}) == "KXHIGHCHI"


def test_ticker_cache_roundtrip(tmp_path: Path):
    path = tmp_path / "tickers.json"
    save_ticker_cache(path, {"B", "A", "A"})
    assert load_ticker_cache(path) == ["A", "B"]


def test_health_template_is_read_only():
    health = health_template(list(ALL_HIGH_SERIES))
    assert health["read_only"] is True
    assert health["live_order_submission"] is False
    assert health["series_count"] == 20
