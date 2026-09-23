from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from weather_alpha.backtest.kalshi_pmxt import ReconstructedKalshiBook


@dataclass(frozen=True)
class DepthExecution:
    requested: Decimal
    filled: Decimal
    vwap: Decimal | None
    complete: bool


@dataclass(frozen=True)
class TerminalTradeEconomics:
    outcome: str
    settlement_value: Decimal
    execution_price: Decimal | None
    filled: Decimal
    gross_pnl: Decimal | None
    fee: Decimal | None
    net_pnl: Decimal | None


def _levels_for_buy(book: ReconstructedKalshiBook, outcome: str) -> list[tuple[Decimal, Decimal]]:
    side = outcome.lower()
    if side == "yes":
        # YES asks are complements of NO bids.
        return sorted(((Decimal("1") - p, q) for p, q in book.no_bids), key=lambda x: x[0])
    if side == "no":
        return sorted(((Decimal("1") - p, q) for p, q in book.yes_bids), key=lambda x: x[0])
    raise ValueError("outcome must be yes or no")


def executable_buy(book: ReconstructedKalshiBook, outcome: str, contracts: Decimal | int | str) -> DepthExecution:
    requested = contracts if isinstance(contracts, Decimal) else Decimal(str(contracts))
    if requested <= 0:
        return DepthExecution(requested, Decimal("0"), None, False)
    remaining = requested
    filled = Decimal("0")
    cost = Decimal("0")
    for price, size in _levels_for_buy(book, outcome):
        if remaining <= 0:
            break
        take = min(remaining, size)
        if take <= 0:
            continue
        cost += take * price
        filled += take
        remaining -= take
    vwap = None if filled == 0 else cost / filled
    return DepthExecution(requested, filled, vwap, filled >= requested)


def kalshi_fee_from_coefficient(price: Decimal, contracts: Decimal, coefficient: Decimal) -> Decimal:
    """Research helper for a verified/explicitly supplied Kalshi fee coefficient.

    The caller must supply the coefficient; this module deliberately has no
    default because weather-series fee applicability must be verified rather
    than silently assumed.
    """
    p = min(Decimal("1"), max(Decimal("0"), price))
    return contracts * coefficient * p * (Decimal("1") - p)


def terminal_buy_economics(
    book: ReconstructedKalshiBook,
    *,
    outcome: str,
    contracts: Decimal | int | str,
    official_yes_winner: bool,
    fee_coefficient: Decimal | None = None,
) -> TerminalTradeEconomics:
    execution = executable_buy(book, outcome, contracts)
    side = outcome.lower()
    settlement_value = Decimal("1") if (official_yes_winner if side == "yes" else not official_yes_winner) else Decimal("0")
    if execution.vwap is None or execution.filled == 0:
        return TerminalTradeEconomics(side, settlement_value, None, execution.filled, None, None, None)
    gross = execution.filled * (settlement_value - execution.vwap)
    fee = None
    net = None
    if fee_coefficient is not None:
        fee = kalshi_fee_from_coefficient(execution.vwap, execution.filled, fee_coefficient)
        net = gross - fee
    return TerminalTradeEconomics(side, settlement_value, execution.vwap, execution.filled, gross, fee, net)


def best_bid_ask(book: ReconstructedKalshiBook, outcome: str) -> tuple[Decimal | None, Decimal | None]:
    side = outcome.lower()
    if side == "yes":
        return book.yes_best_bid, book.yes_best_ask
    if side == "no":
        return book.no_best_bid, book.no_best_ask
    raise ValueError("outcome must be yes or no")
