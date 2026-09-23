from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

import httpx
import pyarrow as pa
import pyarrow.dataset as ds

PMXT_V2_BASE = "https://r2v2.pmxt.dev"
PMXT_V2_START = datetime(2026, 4, 13, 19, tzinfo=timezone.utc)


@dataclass(frozen=True)
class PmxtEvent:
    timestamp_received: datetime
    timestamp: datetime
    market: str
    event_type: str
    asset_id: str
    bids: Optional[list[list[str]]]
    asks: Optional[list[list[str]]]
    price: Optional[float]
    size: Optional[float]
    side: Optional[str]
    best_bid: Optional[float]
    best_ask: Optional[float]
    fee_rate_bps: Optional[int]


def hourly_filename(hour: datetime) -> str:
    hour = hour.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if hour < PMXT_V2_START:
        raise ValueError(f"pmxt v2 begins at {PMXT_V2_START.isoformat()}")
    return f"polymarket_orderbook_{hour:%Y-%m-%dT%H}.parquet"


def hourly_url(hour: datetime) -> str:
    return f"{PMXT_V2_BASE}/{hourly_filename(hour)}"


def download_hour(hour: datetime, output_dir: Path, *, overwrite: bool = False, timeout_s: float = 180.0) -> Path:
    """Download one pmxt v2 hourly partition into the weather project's cache.

    A 404 means that pmxt has no object for that UTC hour. The caller decides
    whether that should be treated as an empty hour or as a coverage problem.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / hourly_filename(hour)
    if dest.exists() and not overwrite:
        return dest

    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", hourly_url(hour), follow_redirects=True, timeout=timeout_s) as response:
        if response.status_code == 404:
            raise FileNotFoundError(hourly_url(hour))
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    tmp.replace(dest)
    return dest


def _decode_market(value: object) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("ascii")
    return str(value)


def _json_depth(value: object) -> Optional[list[list[str]]]:
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return json.loads(str(value))


def iter_events(
    parquet_paths: Iterable[Path],
    *,
    condition_ids: Optional[set[str]] = None,
    asset_ids: Optional[set[str]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    event_types: Optional[set[str]] = None,
    batch_size: int = 250_000,
) -> Iterator[PmxtEvent]:
    """Stream filtered pmxt rows without loading the archive into memory.

    Filters are deliberately applied on columns for which the archive has useful
    Parquet locality: market, asset_id and timestamp_received. This reader is
    suitable for replay and as-of joins. It never reorders rows across a file.
    """
    paths = [str(Path(p)) for p in parquet_paths]
    if not paths:
        return

    dataset = ds.dataset(paths, format="parquet")
    filt = None

    def add(expr: ds.Expression) -> None:
        nonlocal filt
        filt = expr if filt is None else filt & expr

    if condition_ids:
        # market is fixed_size_binary in pmxt v2.
        encoded = [x.encode("ascii") for x in condition_ids]
        add(ds.field("market").isin(encoded))
    if asset_ids:
        add(ds.field("asset_id").isin(sorted(asset_ids)))
    if start is not None:
        add(ds.field("timestamp_received") >= pa.scalar(start.astimezone(timezone.utc), type=pa.timestamp("ms", tz="UTC")))
    if end is not None:
        add(ds.field("timestamp_received") < pa.scalar(end.astimezone(timezone.utc), type=pa.timestamp("ms", tz="UTC")))
    if event_types:
        add(ds.field("event_type").isin(sorted(event_types)))

    columns = [
        "timestamp_received", "timestamp", "market", "event_type", "asset_id",
        "bids", "asks", "price", "size", "side", "best_bid", "best_ask", "fee_rate_bps",
    ]

    scanner = dataset.scanner(columns=columns, filter=filt, batch_size=batch_size)
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            yield PmxtEvent(
                timestamp_received=row["timestamp_received"],
                timestamp=row["timestamp"],
                market=_decode_market(row["market"]),
                event_type=row["event_type"],
                asset_id=row["asset_id"],
                bids=_json_depth(row["bids"]),
                asks=_json_depth(row["asks"]),
                price=float(row["price"]) if row["price"] is not None else None,
                size=float(row["size"]) if row["size"] is not None else None,
                side=row["side"],
                best_bid=float(row["best_bid"]) if row["best_bid"] is not None else None,
                best_ask=float(row["best_ask"]) if row["best_ask"] is not None else None,
                fee_rate_bps=int(row["fee_rate_bps"]) if row["fee_rate_bps"] is not None else None,
            )


@dataclass
class BookState:
    bids: dict[float, float]
    asks: dict[float, float]
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None

    @classmethod
    def empty(cls) -> "BookState":
        return cls({}, {})

    def apply(self, event: PmxtEvent) -> None:
        """Apply pmxt book/price-change events to reconstruct an executable book.

        `book` replaces depth. `price_change` updates/removes one price level. The
        archive's best_bid/best_ask are retained as a cross-check when populated.
        """
        if event.event_type == "book":
            self.bids = {float(p): float(s) for p, s in (event.bids or []) if float(s) > 0}
            self.asks = {float(p): float(s) for p, s in (event.asks or []) if float(s) > 0}
        elif event.event_type == "price_change" and event.price is not None and event.size is not None and event.side:
            levels = self.bids if event.side.upper() == "BUY" else self.asks
            if event.size <= 0:
                levels.pop(event.price, None)
            else:
                levels[event.price] = event.size

        calculated_bid = max(self.bids) if self.bids else None
        calculated_ask = min(self.asks) if self.asks else None
        self.best_bid = event.best_bid if event.best_bid is not None else calculated_bid
        self.best_ask = event.best_ask if event.best_ask is not None else calculated_ask

    def executable_buy(self, shares: float) -> tuple[Optional[float], float]:
        """Return VWAP and filled shares for a market buy through current asks."""
        remaining = max(0.0, shares)
        cost = 0.0
        filled = 0.0
        for price, size in sorted(self.asks.items()):
            take = min(remaining, size)
            if take <= 0:
                continue
            cost += take * price
            filled += take
            remaining -= take
            if remaining <= 1e-12:
                break
        return ((cost / filled) if filled > 0 else None, filled)
