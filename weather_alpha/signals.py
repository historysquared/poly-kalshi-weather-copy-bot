from __future__ import annotations
from .models import MarketQuote, Signal

def signal_from_probability(
    market: MarketQuote,
    p_yes: float,
    min_edge: float = 0.06,
    min_volume: float = 10.0,
    max_spread: float = 0.12,
) -> Signal | None:
    if market.volume < min_volume:
        return None
    if market.yes_ask is None or market.no_ask is None:
        return None
    yes_bid = market.yes_bid if market.yes_bid is not None else max(0.0, 1.0 - market.no_ask)
    spread = max(0.0, market.yes_ask - yes_bid)
    if spread > max_spread:
        return None
    yes_edge = p_yes - market.yes_ask
    p_no = 1.0 - p_yes
    no_edge = p_no - market.no_ask
    if yes_edge >= min_edge and yes_edge >= no_edge:
        return Signal(market.ticker, "yes", p_yes, market.yes_ask, yes_edge, yes_edge,
                      f"model YES {p_yes:.1%} vs executable ask {market.yes_ask:.1%}")
    if no_edge >= min_edge:
        return Signal(market.ticker, "no", p_no, market.no_ask, no_edge, no_edge,
                      f"model NO {p_no:.1%} vs executable ask {market.no_ask:.1%}")
    return None
