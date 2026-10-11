from pathlib import Path

from datetime import datetime, timezone

from scripts.record_kalshi_weather_l2_archive import (
    bbo_payload,
    book_quote,
    health_template,
    load_all_weather_catalog_series_count,
    load_all_weather_catalog_tickers,
    load_ticker_cache,
    market_series,
    ticker_quote,
    update_live_book_state,
    save_ticker_cache,
)
from weather_alpha.live.kalshi_ws import KalshiBook
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


def test_load_all_weather_catalog_series_count(tmp_path: Path):
    path = tmp_path / "catalog.json"
    path.write_text('{"active_series":[{"ticker":"A","markets":[]},{"ticker":"B","markets":[]},null]}')
    assert load_all_weather_catalog_series_count(path) == 2


def test_ticker_quote_derives_binary_no_side():
    q = ticker_quote(
        {
            "market_ticker": "KXTEST",
            "time": "2026-10-11T17:00:00Z",
            "ts_ms": 1,
            "yes_bid_dollars": "0.3900",
            "yes_ask_dollars": "0.4000",
            "yes_bid_size_fp": "1.00",
            "yes_ask_size_fp": "81.00",
        },
        datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc),
    )
    assert q is not None
    assert q["yes_bid"] == "0.3900"
    assert q["yes_ask"] == "0.4000"
    assert q["no_bid"] == "0.6000"
    assert q["no_ask"] == "0.6100"
    assert q["no_bid_size"] == "81.00"
    assert q["no_ask_size"] == "1.00"


def test_book_quote_uses_orderbook_state_for_bbo_and_depth():
    book = KalshiBook("KXTEST")
    book.apply_snapshot(
        {
            "yes_dollars_fp": [["0.39", "2"], ["0.38", "3"]],
            "no_dollars_fp": [["0.60", "5"], ["0.59", "7"]],
            "ts": "2026-10-11T17:00:00Z",
        },
        42,
    )
    q = book_quote(book, datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc))
    assert q["yes_bid"] == "0.39"
    assert q["yes_ask"] == "0.40"
    assert q["no_bid"] == "0.60"
    assert q["no_ask"] == "0.61"
    assert q["yes_bid_size"] == "2"
    assert q["yes_ask_size"] == "5"
    assert q["sequence"] == 42
    assert q["yes_bid_depth_top5"] == "5"
    assert q["yes_ask_depth_top5"] == "12"


def test_bbo_payload_filters_to_daily_temperature_series():
    now = datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc)
    payload = bbo_payload(
        {
            "KXHIGHNY-26OCT12-B70.5": {"yes_ask": "0.40"},
            "KXLOWTNYC-26OCT12-B50.5": {"yes_ask": "0.30"},
            "KXGTEMP-27-P0": {"yes_ask": "0.55"},
        },
        generated_at=now,
        health={"current_tickers": 3, "series_count": 2},
    )
    assert payload["quote_count"] == 2
    assert set(payload["quotes"]) == {"KXHIGHNY-26OCT12-B70.5", "KXLOWTNYC-26OCT12-B50.5"}
    assert payload["scope"] == "DAILY_TEMPERATURE_BBO_FROM_ALL_WEATHER_WS"


def test_live_book_state_never_cross_contaminates_market_tickers():
    received = datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc)
    books = {}
    quotes = {}
    a = {
        "type": "orderbook_snapshot", "seq": 1,
        "msg": {
            "market_ticker": "A",
            "yes_dollars_fp": [["0.20", "2"]],
            "no_dollars_fp": [["0.70", "3"]],
        },
    }
    b = {
        "type": "orderbook_snapshot", "seq": 2,
        "msg": {
            "market_ticker": "B",
            "yes_dollars_fp": [["0.60", "4"]],
            "no_dollars_fp": [["0.30", "5"]],
        },
    }
    assert update_live_book_state(a, received, books, quotes) == "A"
    assert update_live_book_state(b, received, books, quotes) == "B"
    assert set(books) == {"A", "B"}
    assert quotes["A"]["yes_bid"] == "0.20"
    assert quotes["A"]["yes_ask"] == "0.30"
    assert quotes["B"]["yes_bid"] == "0.60"
    assert quotes["B"]["yes_ask"] == "0.70"
