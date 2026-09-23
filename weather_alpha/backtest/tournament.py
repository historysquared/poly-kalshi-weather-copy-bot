from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Iterable, Sequence


class Strategy(str, Enum):
    BLIND_NO = "blind_no"
    MODEL_NO = "model_no"
    BEST_YES = "best_yes"
    LATE_ELIMINATION_NO = "late_elimination_no"
    ADJACENT_YES_BASKET = "adjacent_yes_basket"


@dataclass(frozen=True)
class ContractSnapshot:
    venue: str
    event_id: str
    market_id: str
    timestamp_ms: int
    lower_f: float | None
    upper_f: float | None
    yes_ask: float | None
    no_ask: float | None
    fair_yes: float | None
    resolved_yes: bool | None
    observed_high_f: float | None = None
    minutes_to_close: float | None = None
    fees_per_share: float = 0.0
    slippage_per_share: float = 0.0

    def all_in_yes(self) -> float | None:
        if self.yes_ask is None:
            return None
        return self.yes_ask + self.fees_per_share + self.slippage_per_share

    def all_in_no(self) -> float | None:
        if self.no_ask is None:
            return None
        return self.no_ask + self.fees_per_share + self.slippage_per_share


@dataclass(frozen=True)
class Trade:
    strategy: Strategy
    venue: str
    event_id: str
    market_id: str
    timestamp_ms: int
    side: str
    entry_price: float
    fair_probability: float | None
    resolved_win: bool
    pnl_per_share: float

    @property
    def roi(self) -> float:
        return self.pnl_per_share / self.entry_price if self.entry_price > 0 else 0.0


def _settle(side: str, price: float, resolved_yes: bool) -> tuple[bool, float]:
    win = resolved_yes if side == "YES" else not resolved_yes
    payout = 1.0 if win else 0.0
    return win, payout - price


def blind_no(snapshot: ContractSnapshot, min_no_price: float = 0.85, max_no_price: float = 0.99) -> Trade | None:
    price = snapshot.all_in_no()
    if snapshot.resolved_yes is None or price is None or not (min_no_price <= price <= max_no_price):
        return None
    win, pnl = _settle("NO", price, snapshot.resolved_yes)
    return Trade(Strategy.BLIND_NO, snapshot.venue, snapshot.event_id, snapshot.market_id,
                 snapshot.timestamp_ms, "NO", price, None, win, pnl)


def model_no(snapshot: ContractSnapshot, min_edge: float = 0.02) -> Trade | None:
    price = snapshot.all_in_no()
    if snapshot.resolved_yes is None or price is None or snapshot.fair_yes is None:
        return None
    fair_no = 1.0 - snapshot.fair_yes
    if fair_no - price < min_edge:
        return None
    win, pnl = _settle("NO", price, snapshot.resolved_yes)
    return Trade(Strategy.MODEL_NO, snapshot.venue, snapshot.event_id, snapshot.market_id,
                 snapshot.timestamp_ms, "NO", price, fair_no, win, pnl)


def best_yes(snapshot: ContractSnapshot, min_edge: float = 0.02) -> Trade | None:
    price = snapshot.all_in_yes()
    if snapshot.resolved_yes is None or price is None or snapshot.fair_yes is None:
        return None
    if snapshot.fair_yes - price < min_edge:
        return None
    win, pnl = _settle("YES", price, snapshot.resolved_yes)
    return Trade(Strategy.BEST_YES, snapshot.venue, snapshot.event_id, snapshot.market_id,
                 snapshot.timestamp_ms, "YES", price, snapshot.fair_yes, win, pnl)


def late_elimination_no(snapshot: ContractSnapshot, min_locked_edge: float = 0.005) -> Trade | None:
    """Buy NO only when a bucket is already mathematically impossible from observed high.

    For a daily-high contract, if observed_high_f is greater than the bucket upper bound,
    that bucket cannot subsequently win. This is a settlement-state strategy, not a forecast.
    """
    price = snapshot.all_in_no()
    if snapshot.resolved_yes is None or price is None or snapshot.upper_f is None or snapshot.observed_high_f is None:
        return None
    if snapshot.observed_high_f <= snapshot.upper_f:
        return None
    if 1.0 - price < min_locked_edge:
        return None
    win, pnl = _settle("NO", price, snapshot.resolved_yes)
    return Trade(Strategy.LATE_ELIMINATION_NO, snapshot.venue, snapshot.event_id, snapshot.market_id,
                 snapshot.timestamp_ms, "NO", price, 1.0, win, pnl)


def adjacent_yes_basket(snapshots: Sequence[ContractSnapshot], min_edge: float = 0.02,
                        max_legs: int = 6) -> tuple[list[Trade], float] | None:
    """Choose the contiguous fair-probability mass with the best positive basket edge.

    Assumes mutually exclusive buckets within one event and one timestamp. Basket payoff is
    $1 if any included bucket wins. Uses executable YES asks, never mids.
    """
    rows = [s for s in snapshots if s.lower_f is not None and s.upper_f is not None and
            s.all_in_yes() is not None and s.fair_yes is not None and s.resolved_yes is not None]
    rows.sort(key=lambda s: float(s.lower_f))
    best: tuple[float, int, int] | None = None
    for i in range(len(rows)):
        fair = cost = 0.0
        for j in range(i, min(len(rows), i + max_legs)):
            fair += float(rows[j].fair_yes)
            cost += float(rows[j].all_in_yes())
            edge = fair - cost
            if best is None or edge > best[0]:
                best = (edge, i, j)
    if best is None or best[0] < min_edge:
        return None
    edge, i, j = best
    chosen = rows[i:j + 1]
    trades: list[Trade] = []
    for s in chosen:
        price = float(s.all_in_yes())
        win, pnl = _settle("YES", price, bool(s.resolved_yes))
        trades.append(Trade(Strategy.ADJACENT_YES_BASKET, s.venue, s.event_id, s.market_id,
                            s.timestamp_ms, "YES", price, s.fair_yes, win, pnl))
    return trades, edge


@dataclass(frozen=True)
class Summary:
    strategy: str
    trades: int
    wins: int
    win_rate: float
    pnl_per_share: float
    average_roi: float


def summarize(trades: Iterable[Trade]) -> list[Summary]:
    grouped: dict[str, list[Trade]] = {}
    for trade in trades:
        grouped.setdefault(trade.strategy.value, []).append(trade)
    out: list[Summary] = []
    for name, rows in sorted(grouped.items()):
        n = len(rows)
        wins = sum(int(r.resolved_win) for r in rows)
        pnl = sum(r.pnl_per_share for r in rows)
        avg_roi = sum(r.roi for r in rows) / n if n else 0.0
        out.append(Summary(name, n, wins, wins / n if n else 0.0, pnl, avg_roi))
    return out
