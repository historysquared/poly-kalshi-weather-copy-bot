from datetime import datetime, timedelta, timezone
from decimal import Decimal

from weather_alpha.backtest.kalshi_pmxt import (
    iter_market_snapshots,
    nearest_book_at_or_before,
    parse_kalshi_parquet_row,
    reconstruct_outcome_book,
)


def _row(**overrides):
    base = {
        "timestamp_received": datetime(2026, 6, 10, 19, 0, 0, 500000, tzinfo=timezone.utc),
        "timestamp": None,
        "market_ticker": "KXHIGHNY-26JUN10-B85.5",
        "market_id": "m1",
        "event_type": "orderbook_snapshot",
        "yes_bids": [{"1": Decimal("0.4000"), "2": Decimal("10.500000")}],
        "no_bids": [{"1": Decimal("0.5500"), "2": Decimal("7.250000")}],
        "price": None,
        "delta": None,
        "side": "",
    }
    base.update(overrides)
    return base


def test_parse_raw_row_preserves_decimals_and_received_fallback():
    event = parse_kalshi_parquet_row(_row())
    assert event.exchange_timestamp is None
    assert event.effective_timestamp == event.received_timestamp
    assert event.yes_bids == ((Decimal("0.4000"), Decimal("10.500000")),)
    assert event.no_bids == ((Decimal("0.5500"), Decimal("7.250000")),)


def test_exchange_timestamp_is_preferred_when_present():
    exchange = datetime(2026, 6, 10, 19, 0, 0, 100000, tzinfo=timezone.utc)
    event = parse_kalshi_parquet_row(_row(timestamp=exchange))
    assert event.exchange_timestamp == exchange
    assert event.effective_timestamp == exchange


def test_complement_asks_are_reconstructed_correctly():
    snap = next(iter_market_snapshots([parse_kalshi_parquet_row(_row())]))
    yes = reconstruct_outcome_book(snap, "yes")
    no = reconstruct_outcome_book(snap, "no")
    assert yes.best_bid == 0.4
    assert yes.best_ask == 0.45
    assert no.best_bid == 0.55
    assert no.best_ask == 0.6
    assert yes.asks[0].size == 7.25
    assert no.asks[0].size == 10.5
    assert yes.source_metadata["clock"] == "received_fallback"


def test_delta_mutates_only_named_bid_side():
    t0 = datetime(2026, 6, 10, 19, 0, 0, tzinfo=timezone.utc)
    snapshot = parse_kalshi_parquet_row(_row(timestamp_received=t0))
    delta = parse_kalshi_parquet_row(
        _row(
            timestamp_received=t0 + timedelta(seconds=1),
            event_type="orderbook_delta",
            yes_bids=[],
            no_bids=[],
            price=Decimal("0.4000"),
            delta=Decimal("-3.500000"),
            side="yes",
        )
    )
    books = list(iter_market_snapshots([snapshot, delta]))
    assert len(books) == 2
    assert books[-1].yes_bids == ((Decimal("0.4000"), Decimal("7.000000")),)
    assert books[-1].no_bids == ((Decimal("0.5500"), Decimal("7.250000")),)


def test_delta_can_delete_price_level():
    t0 = datetime(2026, 6, 10, 19, 0, 0, tzinfo=timezone.utc)
    snapshot = parse_kalshi_parquet_row(_row(timestamp_received=t0))
    delete = parse_kalshi_parquet_row(
        _row(
            timestamp_received=t0 + timedelta(seconds=1),
            event_type="orderbook_delta",
            yes_bids=[],
            no_bids=[],
            price=Decimal("0.4000"),
            delta=Decimal("-10.500000"),
            side="yes",
        )
    )
    books = list(iter_market_snapshots([snapshot, delete]))
    assert books[-1].yes_bids == ()


def test_unknown_non_snapshot_event_is_not_guessed_into_book():
    t0 = datetime(2026, 6, 10, 19, 0, 0, tzinfo=timezone.utc)
    snapshot = parse_kalshi_parquet_row(_row(timestamp_received=t0))
    trade_like = parse_kalshi_parquet_row(
        _row(
            timestamp_received=t0 + timedelta(seconds=1),
            event_type="trade",
            yes_bids=[],
            no_bids=[],
            price=Decimal("0.5000"),
            delta=None,
            side="yes",
        )
    )
    books = list(iter_market_snapshots([snapshot, trade_like]))
    assert len(books) == 1


def test_nearest_book_at_or_before_enforces_no_lookahead():
    t0 = datetime(2026, 6, 10, 19, 0, 0, tzinfo=timezone.utc)
    rows = [
        parse_kalshi_parquet_row(_row(timestamp_received=t0)),
        parse_kalshi_parquet_row(_row(timestamp_received=t0 + timedelta(seconds=10))),
    ]
    books = list(iter_market_snapshots(rows))
    selected = nearest_book_at_or_before(books, ticker="KXHIGHNY-26JUN10-B85.5", at=t0 + timedelta(seconds=5))
    assert selected is not None
    assert selected.effective_timestamp == t0


def test_fractional_depth_vwap_survives_raw_conversion():
    snap = next(iter_market_snapshots([parse_kalshi_parquet_row(_row())]))
    yes = reconstruct_outcome_book(snap, "yes")
    vwap, filled = yes.executable_buy(2.5)
    assert vwap == 0.45
    assert filled == 2.5
