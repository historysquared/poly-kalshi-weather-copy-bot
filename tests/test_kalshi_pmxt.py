from datetime import datetime, timedelta, timezone

import pytest

from weather_alpha.backtest.kalshi_pmxt import (
    HistoricalOrderBook,
    PmxtKalshiHistoricalClient,
    kalshi_hourly_filename,
    kalshi_hourly_url,
    parse_order_book,
)


def test_archive_filename_and_url_are_utc():
    dt = datetime(2026, 6, 10, 19, 37, tzinfo=timezone.utc)
    assert kalshi_hourly_filename(dt) == "kalshi_orderbook_2026-06-10T19.parquet"
    assert kalshi_hourly_url(dt).endswith("/kalshi_orderbook_2026-06-10T19.parquet")


def test_parse_book_dict_levels_and_vwap():
    book = parse_order_book(
        "KXHIGHNY-26JUN10-B85.5",
        "yes",
        {
            "timestamp": 1781118000000,
            "bids": [{"price": 0.40, "size": 20, "orderCount": 2}],
            "asks": [
                {"price": 0.50, "size": 10, "orderCount": 1},
                {"price": 0.55, "size": 20, "orderCount": 2},
            ],
            "lastTradePrice": 0.51,
            "sourceMetadata": {"exchange": "kalshi"},
        },
    )
    assert book.best_bid == 0.40
    assert book.best_ask == 0.50
    vwap, filled = book.executable_buy(20)
    assert filled == 20
    assert round(vwap, 4) == 0.525
    assert book.last_trade_price == 0.51


def test_parse_book_array_levels_and_datetime_fallback():
    book = parse_order_book(
        "TEST-TICKER",
        "no",
        {
            "datetime": "2026-06-10T19:00:00Z",
            "bids": [[0.20, 5], [0.18, 2, 3]],
            "asks": [[0.25, 4]],
        },
    )
    assert book.outcome == "no"
    assert book.timestamp == datetime(2026, 6, 10, 19, tzinfo=timezone.utc)
    assert book.bids[1].order_count == 3


def test_naive_time_rejected():
    with pytest.raises(ValueError):
        kalshi_hourly_filename(datetime(2026, 6, 10, 19))


def test_range_validation():
    client = PmxtKalshiHistoricalClient(api_key="x")
    start = datetime(2026, 6, 10, 19, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        client.range("TEST", start=start, end=start)
    with pytest.raises(ValueError):
        client.range("TEST", start=start, end=start + timedelta(hours=1), limit=1001)


def test_snapshot_no_lookahead_guard(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": True,
                "data": {
                    "timestamp": 1781118060000,
                    "bids": [{"price": 0.40, "size": 1}],
                    "asks": [{"price": 0.50, "size": 1}],
                },
            }

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            return Response()

    import weather_alpha.backtest.kalshi_pmxt as module

    monkeypatch.setattr(module.httpx, "Client", Client)
    at = datetime.fromtimestamp(1781118000, tz=timezone.utc)
    client = PmxtKalshiHistoricalClient(api_key="x")
    with pytest.raises(ValueError, match="lookahead"):
        client.snapshot("TEST", at=at)
