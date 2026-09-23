from datetime import datetime, timezone
from decimal import Decimal

from weather_alpha.backtest.causal_trade_backtest import executable_buy, kalshi_fee, snapshot_at_or_after, terminal_pnl


def snap(ts: str):
    return {
        "book_time": ts,
        "yes_bids": [{"price": "0.40", "size": "10"}],
        "no_bids": [{"price": "0.70", "size": "2"}, {"price": "0.60", "size": "10"}],
    }


def test_executable_buy_uses_complementary_book_depth():
    fill = executable_buy(snap("2026-06-11T00:00:00+00:00"), "YES", Decimal("5"))
    assert fill.complete
    assert fill.filled == Decimal("5")
    assert fill.vwap == Decimal("0.36")


def test_kalshi_fee_rounds_up_to_next_cent():
    fee = kalshi_fee(coefficient=Decimal("0.07"), contracts=Decimal("1"), price=Decimal("0.01"))
    assert fee == Decimal("0.01")


def test_snapshot_latency_and_terminal_pnl():
    rows = [snap("2026-06-11T00:00:00+00:00"), snap("2026-06-11T00:01:00+00:00")]
    signal = datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc)
    chosen = snapshot_at_or_after(rows, signal, 30)
    assert chosen["book_time"].startswith("2026-06-11T00:01")
    pnl = terminal_pnl(side="NO", price=Decimal("0.25"), contracts=Decimal("1"), official_yes_winner=False, fee=Decimal("0.01"))
    assert pnl == Decimal("0.74")
