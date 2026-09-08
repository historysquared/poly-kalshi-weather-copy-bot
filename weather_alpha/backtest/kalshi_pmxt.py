from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import httpx

PMXT_HOSTED_BASE = "https://api.pmxt.dev/api"
PMXT_KALSHI_ARCHIVE_BASE = "https://r2kalshi.pmxt.dev"


@dataclass(frozen=True)
class OrderLevel:
    price: float
    size: float
    order_count: Optional[int] = None


@dataclass(frozen=True)
class HistoricalOrderBook:
    ticker: str
    outcome: str
    timestamp: datetime
    bids: tuple[OrderLevel, ...]
    asks: tuple[OrderLevel, ...]
    last_trade_price: Optional[float] = None
    source_metadata: Optional[dict] = None

    @property
    def best_bid(self) -> Optional[float]:
        return max((level.price for level in self.bids), default=None)

    @property
    def best_ask(self) -> Optional[float]:
        return min((level.price for level in self.asks), default=None)

    def executable_buy(self, contracts: float) -> tuple[Optional[float], float]:
        remaining = max(0.0, float(contracts))
        cost = 0.0
        filled = 0.0
        for level in sorted(self.asks, key=lambda x: x.price):
            if remaining <= 1e-12:
                break
            take = min(remaining, level.size)
            if take <= 0:
                continue
            cost += take * level.price
            filled += take
            remaining -= take
        return ((cost / filled) if filled > 0 else None, filled)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("historical PMXT timestamps must be timezone-aware")
    return dt.astimezone(timezone.utc)


def _to_ms(dt: datetime) -> int:
    return int(_utc(dt).timestamp() * 1000)


def _parse_timestamp(row: dict) -> datetime:
    ts = row.get("timestamp")
    if ts is not None:
        value = float(ts)
        # Hosted PMXT order-book timestamps are documented in milliseconds.
        if value > 10_000_000_000:
            value /= 1000.0
        return datetime.fromtimestamp(value, tz=timezone.utc)

    text = row.get("datetime")
    if not text:
        raise ValueError("PMXT order-book response has no timestamp/datetime")
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)


def _parse_levels(values: object) -> tuple[OrderLevel, ...]:
    if not values:
        return ()
    out: list[OrderLevel] = []
    for item in values:  # type: ignore[assignment]
        if isinstance(item, dict):
            price = float(item["price"])
            size = float(item["size"])
            count = item.get("orderCount")
        else:
            price = float(item[0])
            size = float(item[1])
            count = item[2] if len(item) > 2 else None
        if size <= 0:
            continue
        out.append(OrderLevel(price=price, size=size, order_count=int(count) if count is not None else None))
    return tuple(out)


def parse_order_book(ticker: str, outcome: str, row: dict) -> HistoricalOrderBook:
    side = outcome.lower()
    if side not in {"yes", "no"}:
        raise ValueError("outcome must be 'yes' or 'no'")
    return HistoricalOrderBook(
        ticker=ticker,
        outcome=side,
        timestamp=_parse_timestamp(row),
        bids=_parse_levels(row.get("bids")),
        asks=_parse_levels(row.get("asks")),
        last_trade_price=float(row["lastTradePrice"]) if row.get("lastTradePrice") is not None else None,
        source_metadata=row.get("sourceMetadata"),
    )


class PmxtKalshiHistoricalClient:
    """Historical Kalshi L2 adapter backed by PMXT's hosted archive API.

    PMXT documents Kalshi's unified outcome id as the Kalshi market ticker. A
    historical lookup uses `since` (milliseconds) and optionally `until`; PMXT
    returns a fully reconstructed L2 book, not a delta stream.

    The hosted API may require a PMXT API key. Set PMXT_API_KEY or pass api_key.
    This adapter is read-only and never sends Kalshi trading credentials.
    """

    def __init__(self, *, api_key: Optional[str] = None, timeout_s: float = 30.0, base_url: str = PMXT_HOSTED_BASE):
        self.api_key = api_key or os.getenv("PMXT_API_KEY")
        self.timeout_s = timeout_s
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    @staticmethod
    def _unwrap(payload: dict) -> object:
        if payload.get("success") is False:
            error = payload.get("error") or {}
            raise RuntimeError(error.get("message") or "PMXT historical query failed")
        return payload.get("data", payload)

    def snapshot(self, ticker: str, *, at: datetime, outcome: str = "yes") -> HistoricalOrderBook:
        params = {
            "outcomeId": ticker,
            "outcome": outcome.lower(),
            "since": _to_ms(at),
        }
        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            r = client.get(f"{self.base_url}/kalshi/fetchOrderBook", params=params, headers=self._headers())
            r.raise_for_status()
            data = self._unwrap(r.json())
        if not isinstance(data, dict):
            raise ValueError("PMXT snapshot response was not an order-book object")
        book = parse_order_book(ticker, outcome, data)
        # No-lookahead guard: PMXT says a single historical snapshot is the
        # nearest reconstructed snapshot at or before `since`.
        if book.timestamp > _utc(at):
            raise ValueError(f"PMXT returned lookahead book {book.timestamp.isoformat()} > {_utc(at).isoformat()}")
        return book

    def range(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
        outcome: str = "yes",
        limit: int = 1000,
    ) -> list[HistoricalOrderBook]:
        if _utc(end) <= _utc(start):
            raise ValueError("end must be after start")
        if not 1 <= int(limit) <= 1000:
            raise ValueError("PMXT historical range limit must be 1..1000")
        params = {
            "outcomeId": ticker,
            "outcome": outcome.lower(),
            "since": _to_ms(start),
            "until": _to_ms(end),
            "limit": int(limit),
        }
        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            r = client.get(f"{self.base_url}/kalshi/fetchOrderBook", params=params, headers=self._headers())
            r.raise_for_status()
            data = self._unwrap(r.json())
        if not isinstance(data, list):
            raise ValueError("PMXT range response was not a list of reconstructed books")
        books = [parse_order_book(ticker, outcome, row) for row in data]
        lower, upper = _utc(start), _utc(end)
        for book in books:
            if not (lower <= book.timestamp <= upper):
                raise ValueError(f"PMXT range returned out-of-window timestamp {book.timestamp.isoformat()}")
        books.sort(key=lambda x: x.timestamp)
        return books


def kalshi_hourly_filename(hour: datetime) -> str:
    h = _utc(hour).replace(minute=0, second=0, microsecond=0)
    return f"kalshi_orderbook_{h:%Y-%m-%dT%H}.parquet"


def kalshi_hourly_url(hour: datetime) -> str:
    return f"{PMXT_KALSHI_ARCHIVE_BASE}/{kalshi_hourly_filename(hour)}"


def download_kalshi_hour(
    hour: datetime,
    output_dir: Path,
    *,
    overwrite: bool = False,
    timeout_s: float = 180.0,
) -> Path:
    """Download one raw PMXT Kalshi hourly Parquet partition.

    Parsing of raw Kalshi Parquet is intentionally separate from the hosted
    historical adapter until its schema has been validated against a real file.
    This prevents silently applying the Polymarket-v2 schema to Kalshi data.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / kalshi_hourly_filename(hour)
    if dest.exists() and not overwrite:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", kalshi_hourly_url(hour), follow_redirects=True, timeout=timeout_s) as response:
        if response.status_code == 404:
            raise FileNotFoundError(kalshi_hourly_url(hour))
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    tmp.replace(dest)
    return dest
