from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VenueQuote:
    venue: str
    market_id: str
    outcome: str
    executable_buy_price: float
    fee_per_share: float = 0.0
    slippage_reserve: float = 0.0

    @property
    def all_in_price(self) -> float:
        return self.executable_buy_price + self.fee_per_share + self.slippage_reserve


@dataclass(frozen=True)
class VenueEdge:
    quote: VenueQuote
    fair_probability: float

    @property
    def edge(self) -> float:
        return self.fair_probability - self.quote.all_in_price


def rank_venues(fair_probability: float, quotes: list[VenueQuote]) -> list[VenueEdge]:
    """Rank economically comparable contracts by executable edge, not midpoint."""
    return sorted(
        (VenueEdge(q, fair_probability) for q in quotes),
        key=lambda x: x.edge,
        reverse=True,
    )


def locked_pair_edge(yes_quote: VenueQuote, no_quote: VenueQuote) -> float:
    """Return locked $1 payout edge for complementary YES/NO purchases.

    Positive means combined all-in cost is below $1. This must only be used after
    settlement semantics are proven equivalent across venues.
    """
    return 1.0 - yes_quote.all_in_price - no_quote.all_in_price
