from decimal import Decimal

from weather_alpha.live.kalshi_ws import KalshiBook


def test_snapshot_delta_and_implied_asks():
    book = KalshiBook("TEST")
    book.apply_snapshot(
        {
            "yes_dollars_fp": [["0.40", "3"], ["0.39", "4"]],
            "no_dollars_fp": [["0.55", "2"], ["0.54", "5"]],
        },
        10,
    )
    assert book.yes_best_bid == Decimal("0.40")
    assert book.yes_best_ask == Decimal("0.45")
    assert book.no_best_bid == Decimal("0.55")
    assert book.no_best_ask == Decimal("0.60")

    book.apply_delta({"side": "no", "price_dollars": "0.55", "delta_fp": "-2", "ts_ms": 1}, 11)
    assert book.no_best_bid == Decimal("0.54")
    assert book.yes_best_ask == Decimal("0.46")
    assert book.sequence == 11


def test_vwap_uses_complement_book_depth():
    book = KalshiBook("TEST")
    book.apply_snapshot(
        {
            "yes_dollars_fp": [["0.38", "10"]],
            "no_dollars_fp": [["0.60", "2"], ["0.58", "10"]],
        }
    )
    vwap, filled = book.executable_buy_vwap("YES", 5)
    assert filled == Decimal("5")
    assert vwap == (Decimal("0.40") * 2 + Decimal("0.42") * 3) / Decimal("5")
