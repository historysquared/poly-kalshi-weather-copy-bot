from datetime import datetime, timezone

from weather_alpha.backtest.pmxt import BookState, PmxtEvent, hourly_filename


def event(*, event_type: str, price=None, size=None, side=None, bids=None, asks=None, best_bid=None, best_ask=None):
    ts = datetime(2026, 4, 14, 12, tzinfo=timezone.utc)
    return PmxtEvent(
        timestamp_received=ts,
        timestamp=ts,
        market="0x" + "1" * 64,
        event_type=event_type,
        asset_id="123",
        bids=bids,
        asks=asks,
        price=price,
        size=size,
        side=side,
        best_bid=best_bid,
        best_ask=best_ask,
        fee_rate_bps=None,
    )


def test_hourly_filename_is_utc():
    dt = datetime(2026, 4, 14, 12, 37, tzinfo=timezone.utc)
    assert hourly_filename(dt) == "polymarket_orderbook_2026-04-14T12.parquet"


def test_book_replay_and_depth_vwap():
    book = BookState.empty()
    book.apply(event(event_type="book", bids=[["0.40", "100"]], asks=[["0.50", "10"], ["0.55", "20"]]))
    assert book.best_bid == 0.40
    assert book.best_ask == 0.50

    vwap, filled = book.executable_buy(20)
    assert filled == 20
    assert round(vwap, 4) == 0.525

    book.apply(event(event_type="price_change", price=0.50, size=0.0, side="SELL", best_ask=0.55))
    assert book.best_ask == 0.55
