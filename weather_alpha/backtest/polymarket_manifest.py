from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

import httpx

GAMMA_SEARCH_URL = "https://gamma-api.polymarket.com/public-search"


@dataclass(frozen=True)
class PolymarketWeatherMarket:
    gamma_market_id: str
    condition_id: str
    event_id: str
    event_slug: str
    event_title: str
    market_slug: str
    question: str
    start_date: Optional[str]
    end_date: Optional[str]
    closed: bool
    resolution_source: Optional[str]
    outcomes: tuple[str, ...]
    token_ids: tuple[str, ...]
    description: Optional[str]

    @property
    def token_by_outcome(self) -> dict[str, str]:
        return dict(zip(self.outcomes, self.token_ids, strict=False))


def _json_array(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    return []


def search_weather_markets(
    *,
    query: str = "highest temperature",
    max_pages: int = 200,
    limit_per_type: int = 50,
    timeout_s: float = 30.0,
) -> Iterator[PolymarketWeatherMarket]:
    """Discover historical Polymarket weather markets through Gamma public search.

    The pmxt archive only stores condition/token ids and orderbook events. This
    metadata step supplies the human contract definition needed to decide which
    archived assets are weather markets and how they resolved.
    """
    seen: set[str] = set()
    with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
        for page in range(1, max_pages + 1):
            response = client.get(
                GAMMA_SEARCH_URL,
                params={
                    "q": query,
                    "events_status": "all",
                    "limit_per_type": limit_per_type,
                    "page": page,
                    "keep_closed_markets": 1,
                    "search_tags": "false",
                    "search_profiles": "false",
                },
            )
            response.raise_for_status()
            payload = response.json()
            events = payload.get("events") or []
            for event in events:
                event_id = str(event.get("id") or "")
                event_slug = str(event.get("slug") or "")
                event_title = str(event.get("title") or "")
                for market in event.get("markets") or []:
                    condition_id = str(market.get("conditionId") or "")
                    if not condition_id or condition_id in seen:
                        continue
                    outcomes = tuple(_json_array(market.get("outcomes")))
                    tokens = tuple(_json_array(market.get("clobTokenIds")))
                    if not outcomes or len(outcomes) != len(tokens):
                        continue
                    question = str(market.get("question") or "")
                    combined = f"{event_title} {question}".lower()
                    # Prevent broad search results from contaminating the weather set.
                    if "temperature" not in combined and "weather" not in combined:
                        continue
                    seen.add(condition_id)
                    yield PolymarketWeatherMarket(
                        gamma_market_id=str(market.get("id") or ""),
                        condition_id=condition_id,
                        event_id=event_id,
                        event_slug=event_slug,
                        event_title=event_title,
                        market_slug=str(market.get("slug") or ""),
                        question=question,
                        start_date=market.get("startDate") or event.get("startDate"),
                        end_date=market.get("endDate") or event.get("endDate"),
                        closed=bool(market.get("closed")),
                        resolution_source=market.get("resolutionSource") or event.get("resolutionSource"),
                        outcomes=outcomes,
                        token_ids=tokens,
                        description=market.get("description") or event.get("description"),
                    )
            pagination = payload.get("pagination") or {}
            if not pagination.get("hasMore"):
                break


def write_manifest(path: Path, markets: Iterator[PolymarketWeatherMarket]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for market in markets:
            row = asdict(market)
            row["outcomes"] = list(market.outcomes)
            row["token_ids"] = list(market.token_ids)
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            count += 1
    return count


def load_manifest(path: Path) -> list[PolymarketWeatherMarket]:
    rows: list[PolymarketWeatherMarket] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            row["outcomes"] = tuple(row.get("outcomes") or [])
            row["token_ids"] = tuple(row.get("token_ids") or [])
            rows.append(PolymarketWeatherMarket(**row))
    return rows
