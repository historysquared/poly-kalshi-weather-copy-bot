from datetime import datetime, timezone
from decimal import Decimal

from weather_alpha.backtest.kalshi_pmxt import ReconstructedKalshiBook
from weather_alpha.backtest.settlement_money import executable_buy, kalshi_fee_from_coefficient, terminal_buy_economics


def book() -> ReconstructedKalshiBook:
    return ReconstructedKalshiBook(
        market_ticker="KXTEST",
        market_id="",
        effective_timestamp=datetime(2026, 6, 10, 19, tzinfo=timezone.utc),
        received_timestamp=datetime(2026, 6, 10, 19, tzinfo=timezone.utc),
        exchange_timestamp=None,
        yes_bids=((Decimal("0.40"), Decimal("10")), (Decimal("0.39"), Decimal("10"))),
        no_bids=((Decimal("0.55"), Decimal("2")), (Decimal("0.50"), Decimal("10"))),
    )


def test_yes_executable_buy_uses_complement_no_bids():
    x = executable_buy(book(), "yes", Decimal("5"))
    assert x.filled == Decimal("5")
    assert x.complete
    # 2 @ .45, then 3 @ .50
    assert x.vwap == Decimal("0.48")


def test_terminal_economics_preserves_decimal_depth_math():
    x = terminal_buy_economics(book(), outcome="yes", contracts=Decimal("5"), official_yes_winner=True)
    assert x.execution_price == Decimal("0.48")
    assert x.gross_pnl == Decimal("2.60")
    assert x.fee is None
    assert x.net_pnl is None


def test_fee_requires_explicit_coefficient():
    fee = kalshi_fee_from_coefficient(Decimal("0.50"), Decimal("10"), Decimal("0.07"))
    assert fee == Decimal("0.1750")
