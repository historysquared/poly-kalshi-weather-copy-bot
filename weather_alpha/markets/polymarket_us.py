from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from .contracts import POLYMARKET_US_STATIONS, parse_temperature_outcome


@dataclass(frozen=True)
class PolymarketUSOutcome:
    event_id: str
    event_title: str
    outcome_label: str
    station: str
    price: float | None
    raw: dict[str, Any]


class PolymarketUSClient:
    """Polymarket US discovery adapter.

    Public web discovery is kept separate from authenticated trading. Polymarket US
    currently exposes API-key creation through its Developer page; credentials are
    deliberately not required by the research layer.
    """

    WEB_BASE = "https://polymarket.us"

    def __init__(self, timeout: float = 15.0) -> None:
        self.http = httpx.Client(timeout=timeout, follow_redirects=True)

    @staticmethod
    def station_for_title(title: str) -> str | None:
        upper = title.upper()
        for city, station in POLYMARKET_US_STATIONS.items():
            if city in upper or (city == "NYC" and "NEW YORK" in upper):
                return station
        return None

    @staticmethod
    def normalize_outcomes(event_id: str, title: str, outcomes: Iterable[dict[str, Any]]) -> list[PolymarketUSOutcome]:
        station = PolymarketUSClient.station_for_title(title)
        if not station:
            return []
        normalized: list[PolymarketUSOutcome] = []
        for item in outcomes:
            label = str(item.get("label") or item.get("title") or "")
            parse_temperature_outcome(label)  # fail closed on unknown contract shapes
            price = item.get("price")
            normalized.append(PolymarketUSOutcome(
                event_id=event_id,
                event_title=title,
                outcome_label=label,
                station=station,
                price=float(price) if price is not None else None,
                raw=item,
            ))
        return normalized
