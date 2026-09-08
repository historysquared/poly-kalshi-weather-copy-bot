from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Iterator, Optional

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


@dataclass(frozen=True)
class RawKalshiEvent:
    """One row from PMXT's validated Kalshi hourly Parquet archive.

    `exchange_timestamp` is nullable in the source. `received_timestamp` is
    guaranteed by the archive schema. `effective_timestamp` is the causal clock
    used for replay: exchange time when present, otherwise receipt time.
    """

    received_timestamp: datetime
    exchange_timestamp: datetime | None
    effective_timestamp: datetime
    market_ticker: str
    market_id: str
    event_type: str
    yes_bids: tuple[tuple[Decimal, Decimal], ...]
    no_bids: tuple[tuple[Decimal, Decimal], ...]
    price: Decimal | None
    delta: Decimal | None
    side: str


@dataclass(frozen=True)
class ReconstructedKalshiBook:
    ticker: str
    market_id: str
    exchange_timestamp: datetime | None
    received_timestamp: datetime
    effective_timestamp: datetime
    event_type: str
    yes_bids: tuple[tuple[Decimal, Decimal], ...]
    no_bids: tuple[tuple[Decimal, Decimal], ...]

    def outcome_book(self, outcome: str) -> HistoricalOrderBook:
        return reconstruct_outcome_book(self, outcome)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("historical PMXT timestamps must be timezone-aware")
    return dt.astimezone(timezone.utc)


def _coerce_dt(value: object | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value)
    text = str(value)
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def _to_ms(dt: datetime) -> int:
    return int(_utc(dt).timestamp() * 1000)


def _outcome(value: str) -> str:
    side = value.lower()
    if side not in {"yes", "no"}:
        raise ValueError("outcome must be 'yes' or 'no'")
    return side


def _parse_timestamp(row: dict) -> datetime:
    ts = row.get("timestamp")
    if ts is not None:
        value = float(ts)
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
    side = _outcome(outcome)
    return HistoricalOrderBook(
        ticker=ticker,
        outcome=side,
        timestamp=_parse_timestamp(row),
        bids=_parse_levels(row.get("bids")),
        asks=_parse_levels(row.get("asks")),
        last_trade_price=float(row["lastTradePrice"]) if row.get("lastTradePrice") is not None else None,
        source_metadata=row.get("sourceMetadata"),
    )


def _decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _raw_levels(values: object | None) -> tuple[tuple[Decimal, Decimal], ...]:
    """Parse PMXT raw list<struct<1:price,2:size>> without losing decimals."""
    if not values:
        return ()
    result: list[tuple[Decimal, Decimal]] = []
    for item in values:  # type: ignore[assignment]
        if isinstance(item, dict):
            price = item.get("1", item.get(1, item.get("price")))
            size = item.get("2", item.get(2, item.get("size")))
        else:
            price, size = item[0], item[1]
        p, q = _decimal(price), _decimal(size)
        if p is None or q is None or q <= 0:
            continue
        result.append((p, q))
    return tuple(result)


def parse_kalshi_parquet_row(row: dict) -> RawKalshiEvent:
    """Parse one row of the validated PMXT Kalshi archive schema."""
    received = _coerce_dt(row.get("timestamp_received"))
    if received is None:
        raise ValueError("raw PMXT Kalshi row missing timestamp_received")
    exchange = _coerce_dt(row.get("timestamp"))
    ticker = str(row.get("market_ticker") or "")
    market_id = str(row.get("market_id") or "")
    event_type = str(row.get("event_type") or "")
    if not ticker or not market_id or not event_type:
        raise ValueError("raw PMXT Kalshi row missing market_ticker/market_id/event_type")
    return RawKalshiEvent(
        received_timestamp=received,
        exchange_timestamp=exchange,
        effective_timestamp=exchange or received,
        market_ticker=ticker,
        market_id=market_id,
        event_type=event_type,
        yes_bids=_raw_levels(row.get("yes_bids")),
        no_bids=_raw_levels(row.get("no_bids")),
        price=_decimal(row.get("price")),
        delta=_decimal(row.get("delta")),
        side=str(row.get("side") or "").strip().lower(),
    )


def read_kalshi_parquet(path: Path | str, *, columns: list[str] | None = None) -> Iterator[RawKalshiEvent]:
    """Stream one raw hourly archive file in bounded Arrow batches."""
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("Install pyarrow to read PMXT Kalshi Parquet") from exc
    required = [
        "timestamp_received", "timestamp", "market_ticker", "market_id", "event_type",
        "yes_bids", "no_bids", "price", "delta", "side",
    ]
    selected = columns or required
    missing = set(required) - set(pq.ParquetFile(path).schema_arrow.names)
    if missing:
        raise ValueError(f"unexpected PMXT Kalshi schema; missing {sorted(missing)}")
    for batch in pq.ParquetFile(path).iter_batches(columns=selected, batch_size=8192):
        for row in batch.to_pylist():
            yield parse_kalshi_parquet_row(row)


def _book_dict(levels: Iterable[tuple[Decimal, Decimal]]) -> dict[Decimal, Decimal]:
    return {p: q for p, q in levels if q > 0}


def _apply_delta(book: dict[Decimal, Decimal], price: Decimal, delta: Decimal) -> None:
    new_size = book.get(price, Decimal("0")) + delta
    if new_size <= 0:
        book.pop(price, None)
    else:
        book[price] = new_size


def iter_market_snapshots(events: Iterable[RawKalshiEvent], *, ticker: str | None = None) -> Iterator[ReconstructedKalshiBook]:
    """Causally reconstruct books from snapshot/delta rows.

    Snapshot-like rows replace both bid ladders. Delta-like rows mutate the side
    named by `side` using `price` and `delta`. Unknown non-snapshot event types
    that cannot be interpreted as a bid delta are ignored rather than guessed.
    """
    state: dict[str, tuple[str, dict[Decimal, Decimal], dict[Decimal, Decimal]]] = {}
    for event in sorted(events, key=lambda x: (x.effective_timestamp, x.received_timestamp)):
        if ticker is not None and event.market_ticker != ticker:
            continue
        current = state.get(event.market_ticker)
        is_snapshot = "snapshot" in event.event_type.lower()
        if is_snapshot:
            yes, no = _book_dict(event.yes_bids), _book_dict(event.no_bids)
            state[event.market_ticker] = (event.market_id, yes, no)
        else:
            if current is None:
                continue
            market_id, yes, no = current
            if event.price is not None and event.delta is not None and event.side in {"yes", "no"}:
                _apply_delta(yes if event.side == "yes" else no, event.price, event.delta)
                state[event.market_ticker] = (event.market_id or market_id, yes, no)
            else:
                continue
        market_id, yes, no = state[event.market_ticker]
        yield ReconstructedKalshiBook(
            ticker=event.market_ticker,
            market_id=market_id,
            exchange_timestamp=event.exchange_timestamp,
            received_timestamp=event.received_timestamp,
            effective_timestamp=event.effective_timestamp,
            event_type=event.event_type,
            yes_bids=tuple(sorted(yes.items(), key=lambda x: x[0], reverse=True)),
            no_bids=tuple(sorted(no.items(), key=lambda x: x[0], reverse=True)),
        )


def reconstruct_outcome_book(book: ReconstructedKalshiBook, outcome: str) -> HistoricalOrderBook:
    """Convert Kalshi YES/NO bid ladders into an executable outcome book.

    Kalshi's opposite-side bids imply complementary asks:
      YES ask = 1 - NO bid
      NO ask  = 1 - YES bid
    Size is preserved exactly before the final float conversion at the trading
    model boundary.
    """
    side = _outcome(outcome)
    direct = book.yes_bids if side == "yes" else book.no_bids
    opposite = book.no_bids if side == "yes" else book.yes_bids
    bids = tuple(OrderLevel(float(p), float(q)) for p, q in direct)
    asks = tuple(
        sorted(
            (OrderLevel(float(Decimal("1") - p), float(q)) for p, q in opposite),
            key=lambda level: level.price,
        )
    )
    return HistoricalOrderBook(
        ticker=book.ticker,
        outcome=side,
        timestamp=book.effective_timestamp,
        bids=bids,
        asks=asks,
        source_metadata={
            "market_id": book.market_id,
            "event_type": book.event_type,
            "exchange_timestamp": book.exchange_timestamp.isoformat() if book.exchange_timestamp else None,
            "received_timestamp": book.received_timestamp.isoformat(),
            "clock": "exchange" if book.exchange_timestamp else "received_fallback",
        },
    )


def nearest_book_at_or_before(
    books: Iterable[ReconstructedKalshiBook], *, ticker: str, at: datetime
) -> ReconstructedKalshiBook | None:
    target = _utc(at)
    eligible = [book for book in books if book.ticker == ticker and book.effective_timestamp <= target]
    return max(eligible, key=lambda x: (x.effective_timestamp, x.received_timestamp), default=None)


class PmxtKalshiHistoricalClient:
    """Historical Kalshi L2 adapter backed by PMXT's hosted archive API."""

    def __init__(self, *, api_key: Optional[str] = None, timeout_s: float = 30.0, base_url: str = PMXT_HOSTED_BASE):
        self.api_key = api_key or os.getenv("PMXT_API_KEY")
        self.timeout_s = timeout_s
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    @staticmethod
    def _unwrap(payload: dict) -> object:
        if payload.get("success") is False:
            error = payload.get("error") or {}
            raise RuntimeError(error.get("message") or "PMXT historical query failed")
        return payload.get("data", payload)

    def snapshot(self, ticker: str, *, at: datetime, outcome: str = "yes") -> HistoricalOrderBook:
        side = _outcome(outcome)
        params = {"outcomeId": ticker, "outcome": side, "since": _to_ms(at)}
        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            r = client.get(f"{self.base_url}/kalshi/fetchOrderBook", params=params, headers=self._headers())
            r.raise_for_status()
            data = self._unwrap(r.json())
        if not isinstance(data, dict):
            raise ValueError("PMXT snapshot response was not an order-book object")
        book = parse_order_book(ticker, side, data)
        if book.timestamp > _utc(at):
            raise ValueError(f"PMXT returned lookahead book {book.timestamp.isoformat()} > {_utc(at).isoformat()}")
        return book

    def range(self, ticker: str, *, start: datetime, end: datetime, outcome: str = "yes", limit: int = 1000) -> list[HistoricalOrderBook]:
        side = _outcome(outcome)
        if _utc(end) <= _utc(start):
            raise ValueError("end must be after start")
        if not 1 <= int(limit) <= 1000:
            raise ValueError("PMXT historical range limit must be 1..1000")
        params = {"outcomeId": ticker, "outcome": side, "since": _to_ms(start), "until": _to_ms(end), "limit": int(limit)}
        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            r = client.get(f"{self.base_url}/kalshi/fetchOrderBook", params=params, headers=self._headers())
            r.raise_for_status()
            data = self._unwrap(r.json())
        if not isinstance(data, list):
            raise ValueError("PMXT range response was not a list of reconstructed books")
        books = [parse_order_book(ticker, side, row) for row in data]
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


def download_kalshi_hour(hour: datetime, output_dir: Path, *, overwrite: bool = False, timeout_s: float = 180.0) -> Path:
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
