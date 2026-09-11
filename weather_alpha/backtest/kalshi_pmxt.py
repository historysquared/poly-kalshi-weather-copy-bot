from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Iterator, Optional

import httpx

PMXT_HOSTED_BASE = "https://api.pmxt.dev/api"
PMXT_KALSHI_ARCHIVE_BASE = os.getenv("PMXT_KALSHI_ARCHIVE_BASE", "https://archive.pmxt.dev/Kalshi").rstrip("/")


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
    def best_bid(self) -> Optional[float]: return max((level.price for level in self.bids), default=None)
    @property
    def best_ask(self) -> Optional[float]: return min((level.price for level in self.asks), default=None)

    def executable_buy(self, contracts: float) -> tuple[Optional[float], float]:
        remaining, cost, filled = max(0.0, float(contracts)), 0.0, 0.0
        for level in sorted(self.asks, key=lambda x: x.price):
            if remaining <= 1e-12: break
            take = min(remaining, level.size)
            if take <= 0: continue
            cost += take * level.price; filled += take; remaining -= take
        return ((cost / filled) if filled > 0 else None, filled)


@dataclass(frozen=True)
class RawKalshiEvent:
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
    market_ticker: str
    market_id: str
    effective_timestamp: datetime
    received_timestamp: datetime
    exchange_timestamp: datetime | None
    yes_bids: tuple[tuple[Decimal, Decimal], ...]
    no_bids: tuple[tuple[Decimal, Decimal], ...]

    @property
    def yes_best_bid(self): return max((p for p, _ in self.yes_bids), default=None)
    @property
    def no_best_bid(self): return max((p for p, _ in self.no_bids), default=None)
    @property
    def yes_best_ask(self): return (Decimal("1") - self.no_best_bid) if self.no_best_bid is not None else None
    @property
    def no_best_ask(self): return (Decimal("1") - self.yes_best_bid) if self.yes_best_bid is not None else None

    def outcome_book(self, outcome: str) -> HistoricalOrderBook:
        side = _outcome(outcome)
        bids_raw = self.yes_bids if side == "yes" else self.no_bids
        complement = self.no_bids if side == "yes" else self.yes_bids
        bids = tuple(OrderLevel(float(p), float(q)) for p, q in bids_raw)
        asks = tuple(OrderLevel(float(Decimal("1") - p), float(q)) for p, q in complement)
        clock = "exchange" if self.exchange_timestamp is not None else "received_fallback"
        return HistoricalOrderBook(
            self.market_ticker,
            side,
            self.effective_timestamp,
            bids,
            tuple(sorted(asks, key=lambda level: level.price)),
            source_metadata={
                "market_id": self.market_id,
                "timestamp_received": self.received_timestamp.isoformat(),
                "exchange_timestamp": self.exchange_timestamp.isoformat() if self.exchange_timestamp else None,
                "clock": clock,
            },
        )


def reconstruct_outcome_book(book: ReconstructedKalshiBook, outcome: str) -> HistoricalOrderBook:
    return book.outcome_book(outcome)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None: raise ValueError("historical PMXT timestamps must be timezone-aware")
    return dt.astimezone(timezone.utc)

def _coerce_dt(value: object | None) -> datetime | None:
    if value is None: return None
    if isinstance(value, datetime): return _utc(value)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)

def _to_ms(dt: datetime) -> int: return int(_utc(dt).timestamp() * 1000)

def _outcome(value: str) -> str:
    side = value.lower()
    if side not in {"yes", "no"}: raise ValueError("outcome must be 'yes' or 'no'")
    return side


def _parse_timestamp(row: dict) -> datetime:
    ts = row.get("timestamp")
    if ts is not None:
        value = float(ts)
        if value > 10_000_000_000: value /= 1000.0
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = row.get("datetime")
    if not text: raise ValueError("PMXT order-book response has no timestamp/datetime")
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)


def _parse_levels(values: object) -> tuple[OrderLevel, ...]:
    if not values: return ()
    out=[]
    for item in values:
        if isinstance(item, dict): price,size,count=float(item["price"]),float(item["size"]),item.get("orderCount")
        else: price,size,count=float(item[0]),float(item[1]),item[2] if len(item)>2 else None
        if size>0: out.append(OrderLevel(price,size,int(count) if count is not None else None))
    return tuple(out)


def parse_order_book(ticker: str, outcome: str, row: dict) -> HistoricalOrderBook:
    side=_outcome(outcome)
    return HistoricalOrderBook(ticker,side,_parse_timestamp(row),_parse_levels(row.get("bids")),_parse_levels(row.get("asks")),
        float(row["lastTradePrice"]) if row.get("lastTradePrice") is not None else None,row.get("sourceMetadata"))


def _decimal(value: object | None) -> Decimal | None:
    if value is None: return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _field(item: dict, *keys: object) -> object | None:
    for key in keys:
        if key in item: return item[key]
        text=str(key)
        if text in item: return item[text]
    return None


def _raw_levels(values: object | None) -> tuple[tuple[Decimal, Decimal], ...]:
    if not values: return ()
    result=[]
    for item in values:
        if isinstance(item, dict):
            price=_field(item, "1", 1, "price")
            size=_field(item, "2", 2, "size")
        else: price,size=item[0],item[1]
        p,q=_decimal(price),_decimal(size)
        if p is None or q is None or q<=0: continue
        result.append((p,q))
    return tuple(result)


def _row_field(row: dict, name: str, position: int) -> object | None:
    if name in row: return row[name]
    if position in row: return row[position]
    if str(position) in row: return row[str(position)]
    return None


def parse_kalshi_parquet_row(row: dict) -> RawKalshiEvent:
    received=_coerce_dt(_row_field(row,"timestamp_received",0))
    if received is None: raise ValueError(f"raw PMXT Kalshi row missing timestamp_received; keys={list(row)[:12]}")
    exchange=_coerce_dt(_row_field(row,"timestamp",1))
    ticker=str(_row_field(row,"market_ticker",2) or "")
    market_id=str(_row_field(row,"market_id",3) or "")
    event_type=str(_row_field(row,"event_type",4) or "")
    # Real PMXT archives contain valid snapshot rows with an empty market_id.
    # Ticker is the stable replay key; preserve market_id exactly rather than inventing one.
    if not ticker or not event_type:
        raise ValueError(f"raw PMXT Kalshi row missing market_ticker/event_type; keys={list(row)[:12]}")
    return RawKalshiEvent(received,exchange,exchange or received,ticker,market_id,event_type,
        _raw_levels(_row_field(row,"yes_bids",5)),_raw_levels(_row_field(row,"no_bids",6)),
        _decimal(_row_field(row,"price",7)),_decimal(_row_field(row,"delta",8)),
        str(_row_field(row,"side",9) or "").strip().lower())


def read_kalshi_parquet(path: Path | str, *, columns: list[str] | None=None) -> Iterator[RawKalshiEvent]:
    try: import pyarrow.parquet as pq
    except ImportError as exc: raise RuntimeError("Install pyarrow to read PMXT Kalshi Parquet") from exc
    required=["timestamp_received","timestamp","market_ticker","market_id","event_type","yes_bids","no_bids","price","delta","side"]
    pf=pq.ParquetFile(path)
    missing=set(required)-set(pf.schema_arrow.names)
    if missing: raise ValueError(f"unexpected PMXT Kalshi schema; missing {sorted(missing)}")
    selected=columns or required
    for batch in pf.iter_batches(columns=selected,batch_size=8192):
        names=batch.schema.names
        for values in zip(*(batch.column(i).to_pylist() for i in range(batch.num_columns))):
            yield parse_kalshi_parquet_row(dict(zip(names,values)))


def _book_dict(levels): return {p:q for p,q in levels if q>0}
def _apply_delta(book,price,delta):
    new_size=book.get(price,Decimal("0"))+delta
    if new_size<=0: book.pop(price,None)
    else: book[price]=new_size


def iter_market_snapshots(events: Iterable[RawKalshiEvent], *, ticker: str|None=None) -> Iterator[ReconstructedKalshiBook]:
    state={}
    for event in sorted(events,key=lambda x:(x.effective_timestamp,x.received_timestamp)):
        if ticker is not None and event.market_ticker!=ticker: continue
        current=state.get(event.market_ticker); is_snapshot="snapshot" in event.event_type.lower()
        if is_snapshot: yes,no=_book_dict(event.yes_bids),_book_dict(event.no_bids)
        elif current and event.price is not None and event.delta is not None and event.side in {"yes","no"}:
            _,yes,no=current; yes,no=dict(yes),dict(no); _apply_delta(yes if event.side=="yes" else no,event.price,event.delta)
        else: continue
        state[event.market_ticker]=(event.market_id,yes,no)
        yield ReconstructedKalshiBook(event.market_ticker,event.market_id,event.effective_timestamp,event.received_timestamp,event.exchange_timestamp,
            tuple(sorted(yes.items(),reverse=True)),tuple(sorted(no.items(),reverse=True)))


def nearest_book_at_or_before(events: Iterable[RawKalshiEvent] | Iterable[ReconstructedKalshiBook], ticker: str, at: datetime) -> ReconstructedKalshiBook|None:
    cutoff=_utc(at)
    items=list(events)
    if not items: return None
    if isinstance(items[0], ReconstructedKalshiBook):
        books=(book for book in items if book.market_ticker==ticker)
    else:
        books=iter_market_snapshots(items, ticker=ticker)
    nearest=None
    for book in books:
        if book.effective_timestamp<=cutoff: nearest=book
        else: break
    return nearest


class PmxtKalshiHistoricalClient:
    def __init__(self,*,api_key=None,timeout_s=30.0,base_url=PMXT_HOSTED_BASE): self.api_key=api_key or os.getenv("PMXT_API_KEY"); self.timeout_s=timeout_s; self.base_url=base_url.rstrip("/")
    def _headers(self): return {"Authorization":f"Bearer {self.api_key}"} if self.api_key else {}
    @staticmethod
    def _unwrap(payload):
        if payload.get("success") is False: raise RuntimeError((payload.get("error") or {}).get("message") or "PMXT historical query failed")
        return payload.get("data",payload)
    def snapshot(self,ticker,*,at,outcome="yes"):
        side=_outcome(outcome); params={"outcomeId":ticker,"outcome":side,"since":_to_ms(at)}
        with httpx.Client(timeout=self.timeout_s,follow_redirects=True) as client: r=client.get(f"{self.base_url}/kalshi/fetchOrderBook",params=params,headers=self._headers()); r.raise_for_status(); data=self._unwrap(r.json())
        if not isinstance(data,dict): raise ValueError("PMXT snapshot response was not an order-book object")
        book=parse_order_book(ticker,side,data)
        if book.timestamp>_utc(at): raise ValueError("PMXT returned lookahead book")
        return book
    def range(self,ticker,*,start,end,outcome="yes",limit=1000):
        side=_outcome(outcome)
        if _utc(end)<=_utc(start): raise ValueError("end must be after start")
        if not 1<=int(limit)<=1000: raise ValueError("PMXT historical range limit must be 1..1000")
        params={"outcomeId":ticker,"outcome":side,"since":_to_ms(start),"until":_to_ms(end),"limit":int(limit)}
        with httpx.Client(timeout=self.timeout_s,follow_redirects=True) as client: r=client.get(f"{self.base_url}/kalshi/fetchOrderBook",params=params,headers=self._headers()); r.raise_for_status(); data=self._unwrap(r.json())
        if not isinstance(data,list): raise ValueError("PMXT range response was not a list of reconstructed books")
        books=[parse_order_book(ticker,side,row) for row in data]; lower,upper=_utc(start),_utc(end)
        for book in books:
            if not(lower<=book.timestamp<=upper): raise ValueError("PMXT range returned out-of-window timestamp")
        return sorted(books,key=lambda x:x.timestamp)


def kalshi_hourly_filename(hour: datetime)->str:
    h=_utc(hour).replace(minute=0,second=0,microsecond=0); return f"kalshi_orderbook_{h:%Y-%m-%dT%H}.parquet"
def kalshi_hourly_url(hour: datetime)->str: return f"{PMXT_KALSHI_ARCHIVE_BASE}/{kalshi_hourly_filename(hour)}"
def download_kalshi_hour(hour: datetime,output_dir: Path,*,overwrite=False,timeout_s=180.0)->Path:
    output_dir.mkdir(parents=True,exist_ok=True); dest=output_dir/kalshi_hourly_filename(hour)
    if dest.exists() and not overwrite: return dest
    tmp=dest.with_suffix(dest.suffix+".part")
    with httpx.stream("GET",kalshi_hourly_url(hour),follow_redirects=True,timeout=timeout_s) as response:
        if response.status_code==404: raise FileNotFoundError(kalshi_hourly_url(hour))
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_bytes(): handle.write(chunk)
    tmp.replace(dest); return dest
