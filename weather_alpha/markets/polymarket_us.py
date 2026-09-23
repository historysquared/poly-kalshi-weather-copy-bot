from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable

import httpx

from .contracts import POLYMARKET_US_STATIONS, ContractShape, parse_temperature_outcome

POLYMARKET_US_API = "https://api.polymarket.us"

_TEMP_MARKET = re.compile(
    r"(?:\btemperature\b|\btemp\b|\bdaily\s+(?:high|low)\b|\b(?:highest|lowest|high|low)\s+temperature\b|°\s*[FC]\b)",
    re.I,
)
_MONTHS = {name.lower(): i for i, name in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1
)}
_DATE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(\d{1,2})(?:,\s*(20\d{2}))?\b",
    re.I,
)


@dataclass(frozen=True)
class PolymarketUSOutcome:
    event_id: str
    event_title: str
    event_slug: str
    market_id: int | None
    market_slug: str
    outcome_label: str
    station: str
    settlement_date: date | None
    shape: ContractShape
    lower_f: float | None
    upper_f: float | None
    volume: float
    liquidity: float
    active: bool
    closed: bool
    raw: dict[str, Any]

    @property
    def weather_event_id(self) -> str | None:
        if self.settlement_date is None:
            return None
        return f"{self.station}_{self.settlement_date.isoformat()}_DAILY_HIGH"


class PolymarketUSClient:
    """Public discovery adapter for the official Polymarket US API.

    The official SDK exposes `/v1/events`, `/v1/markets`, market books, BBO and
    authenticated order resources. This research adapter intentionally uses only
    public discovery until settlement mapping/backtests/paper trading pass.
    """

    def __init__(self, timeout: float = 20.0, base_url: str = POLYMARKET_US_API) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @staticmethod
    def station_for_text(text: str) -> str | None:
        upper = text.upper()
        for city, station in POLYMARKET_US_STATIONS.items():
            if city in upper or (city == "NYC" and ("NEW YORK" in upper or "NEW YORK CITY" in upper)):
                return station
        return None

    @staticmethod
    def _settlement_date(text: str, event: dict[str, Any] | None = None) -> date | None:
        m = _DATE.search(text)
        if not m:
            return None
        year = int(m.group(3)) if m.group(3) else None
        if year is None and event:
            for key in ("startTime", "endTime"):
                value = event.get(key)
                if value:
                    try:
                        year = datetime.fromisoformat(str(value).replace("Z", "+00:00")).year
                        break
                    except ValueError:
                        pass
        if year is None:
            return None
        try:
            return date(year, _MONTHS[m.group(1).lower()], int(m.group(2)))
        except ValueError:
            return None

    @staticmethod
    def normalize_market(event: dict[str, Any], market: dict[str, Any]) -> PolymarketUSOutcome | None:
        event_title = str(event.get("title") or "")
        market_title = str(market.get("title") or "")
        outcome_label = str(market.get("outcome") or market_title)
        description = str(event.get("description") or "")
        combined = " ".join((event_title, market_title, outcome_label, description))
        if not _TEMP_MARKET.search(combined):
            return None
        station = PolymarketUSClient.station_for_text(combined)
        if not station:
            return None
        try:
            shape, lower, upper = parse_temperature_outcome(outcome_label)
        except ValueError:
            try:
                shape, lower, upper = parse_temperature_outcome(market_title)
            except ValueError:
                return None
        return PolymarketUSOutcome(
            event_id=str(event.get("id") or ""),
            event_title=event_title,
            event_slug=str(event.get("slug") or ""),
            market_id=int(market["id"]) if market.get("id") is not None else None,
            market_slug=str(market.get("slug") or ""),
            outcome_label=outcome_label,
            station=station,
            settlement_date=PolymarketUSClient._settlement_date(combined, event),
            shape=shape,
            lower_f=lower,
            upper_f=upper,
            volume=float(market.get("volume") or 0.0),
            liquidity=float(market.get("liquidity") or 0.0),
            active=bool(market.get("active", False)),
            closed=bool(market.get("closed", False)),
            raw=dict(market),
        )

    @staticmethod
    def normalize_events(events: Iterable[dict[str, Any]]) -> list[PolymarketUSOutcome]:
        out: dict[str, PolymarketUSOutcome] = {}
        for event in events:
            for market in event.get("markets", []) or []:
                if not isinstance(market, dict):
                    continue
                normalized = PolymarketUSClient.normalize_market(event, market)
                if normalized and normalized.market_slug:
                    out[normalized.market_slug] = normalized
        return list(out.values())

    def _list_events(self, *, offset: int, limit: int, active: bool | None, closed: bool | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(f"{self.base_url}/v1/events", params=params)
            response.raise_for_status()
            payload = response.json()
        rows = payload.get("events", []) if isinstance(payload, dict) else []
        return [dict(row) for row in rows if isinstance(row, dict)]

    def discover_weather(self, *, include_closed: bool = False, max_pages: int = 100) -> list[PolymarketUSOutcome]:
        found: dict[str, PolymarketUSOutcome] = {}
        for page in range(max_pages):
            rows = self._list_events(
                offset=page * 100,
                limit=100,
                active=None if include_closed else True,
                closed=None if include_closed else False,
            )
            if not rows:
                break
            for item in self.normalize_events(rows):
                found[item.market_slug] = item
            if len(rows) < 100:
                break
        return sorted(found.values(), key=lambda x: (x.settlement_date or date.max, x.event_slug, x.market_slug))

    def market_book(self, slug: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(f"{self.base_url}/v1/markets/{slug}/book")
            response.raise_for_status()
            return dict(response.json())

    def market_bbo(self, slug: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(f"{self.base_url}/v1/markets/{slug}/bbo")
            response.raise_for_status()
            return dict(response.json())
