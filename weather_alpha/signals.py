from __future__ import annotations

from datetime import datetime, timezone

from .engine.models import ExecutionQuality, Side, Signal
from .models import MarketQuote


def signal_from_probability(
    market: MarketQuote,
    p_yes: float,
    min_edge: float = 0.06,
    min_volume: float = 10.0,
    max_spread: float = 0.12,
) -> Signal | None:
    if market.volume < min_volume or market.yes_ask is None or market.no_ask is None:
        return None
    yes_bid = market.yes_bid if market.yes_bid is not None else max(0.0, 1.0 - market.no_ask)
    spread = max(0.0, market.yes_ask - yes_bid)
    if spread > max_spread:
        return None
    yes_edge = p_yes - market.yes_ask
    p_no = 1.0 - p_yes
    no_edge = p_no - market.no_ask
    if yes_edge >= min_edge and yes_edge >= no_edge:
        side, probability, ask, edge = Side.YES, p_yes, market.yes_ask, yes_edge
    elif no_edge >= min_edge:
        side, probability, ask, edge = Side.NO, p_no, market.no_ask, no_edge
    else:
        return None
    reason = f"model {side.value} {probability:.1%} vs executable ask {ask:.1%}"
    return Signal(
        timestamp=market.updated_time or datetime.now(timezone.utc),
        strategy="simple_probability_scan_v1",
        venue="kalshi",
        contract_id=market.ticker,
        weather_event_id=market.event_ticker or market.ticker,
        side=side,
        model_probability=probability,
        market_probability=ask,
        executable_price=ask,
        raw_edge=edge,
        executable_edge=edge,
        expected_value_per_contract=edge,
        execution_quality=ExecutionQuality.TOUCH_EXECUTION,
        metadata={"reasoning": reason},
    )
