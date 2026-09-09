from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.markets.weather_catalog import CatalogStatus, classify_kalshi_market, looks_weather_like

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"


def candidate_tickers(pmxt_path: Path) -> list[str]:
    table = pq.read_table(pmxt_path, columns=["market_ticker"])
    unique = sorted(set(str(x) for x in table.column("market_ticker").to_pylist() if x))
    return [ticker for ticker in unique if looks_weather_like({"ticker": ticker})]


def load_cached_metadata(path: Path) -> dict[str, dict[str, Any]]:
    cached: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return cached
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                market = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            ticker = str(market.get("ticker") or "")
            if ticker:
                cached[ticker] = market
    return cached


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _get_with_retry(
    url: str,
    client: httpx.Client,
    *,
    params: dict[str, Any] | None = None,
    label: str = "request",
    max_attempts: int = 8,
    base_delay_s: float = 1.0,
) -> httpx.Response:
    for attempt in range(max_attempts):
        r = client.get(url, params=params)
        if r.status_code != 429 and not (500 <= r.status_code < 600):
            return r
        retry_after = r.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else base_delay_s * (2 ** attempt)
        except ValueError:
            delay = base_delay_s * (2 ** attempt)
        delay = min(delay, 60.0) + random.uniform(0.0, 0.25)
        print(f"retry label={label} status={r.status_code} attempt={attempt+1}/{max_attempts} sleep={delay:.2f}s")
        time.sleep(delay)
    raise RuntimeError(f"Kalshi metadata retries exhausted for {label}")


def fetch_market_batch(
    tickers: list[str],
    client: httpx.Client,
    *,
    historical: bool,
) -> list[dict[str, Any]]:
    """Fetch many exact market tickers in one request.

    Kalshi documents `tickers` as a comma-separated filter on both `/markets`
    and `/historical/markets`. Using the bulk endpoint avoids hundreds of
    per-market requests and is the preferred recovery path for archive joins.
    """
    if not tickers:
        return []
    source = "historical" if historical else "current"
    endpoint = f"{KALSHI_BASE}/historical/markets" if historical else f"{KALSHI_BASE}/markets"
    r = _get_with_retry(
        endpoint,
        client,
        params={"tickers": ",".join(tickers), "limit": 1000, "mve_filter": "exclude"},
        label=f"{source}-batch-{tickers[0]}-{len(tickers)}",
    )
    if r.status_code == 400 and "mve_filter" in r.text.lower():
        # Older deployments may reject mve_filter with tickers. Retry without it.
        r = _get_with_retry(
            endpoint,
            client,
            params={"tickers": ",".join(tickers), "limit": 1000},
            label=f"{source}-batch-fallback-{tickers[0]}-{len(tickers)}",
        )
    r.raise_for_status()
    payload = r.json()
    markets = payload.get("markets", []) if isinstance(payload, dict) else []
    out: list[dict[str, Any]] = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        copy = dict(market)
        copy["_metadata_source"] = source
        out.append(copy)
    return out


def bulk_fetch_missing(
    tickers: list[str],
    cached: dict[str, dict[str, Any]],
    client: httpx.Client,
    raw_handle,
    *,
    batch_size: int = 100,
    request_delay_s: float = 0.5,
) -> tuple[int, Counter[str]]:
    """Recover metadata for uncached PMXT candidates with O(n/batch) requests."""
    missing = [ticker for ticker in tickers if ticker not in cached]
    source_counts: Counter[str] = Counter()
    new_records = 0

    for batch_num, batch in enumerate(_chunks(missing, batch_size), 1):
        unresolved = set(batch)
        for historical in (True, False):
            if not unresolved:
                break
            request_tickers = sorted(unresolved)
            try:
                markets = fetch_market_batch(request_tickers, client, historical=historical)
            except (httpx.HTTPError, RuntimeError) as exc:
                print(f"batch_failed source={'historical' if historical else 'current'} batch={batch_num} error={exc}")
                continue
            for market in markets:
                ticker = str(market.get("ticker") or "")
                if not ticker or ticker not in unresolved:
                    continue
                cached[ticker] = market
                source = str(market.get("_metadata_source") or "unknown")
                source_counts[source] += 1
                raw_handle.write(json.dumps(market, sort_keys=True, default=str) + "\n")
                raw_handle.flush()
                unresolved.discard(ticker)
                new_records += 1
            time.sleep(request_delay_s)

        print(
            f"bulk_batch={batch_num} requested={len(batch)} recovered={len(batch)-len(unresolved)} "
            f"still_missing={len(unresolved)} cached={len(cached)}"
        )
    return new_records, source_counts


def catalog_row(market: dict[str, Any]) -> dict[str, Any] | None:
    rec = classify_kalshi_market(market)
    if rec.status == CatalogStatus.REJECT:
        return None
    return {
        "venue": rec.venue,
        "contract_id": rec.contract_id,
        "event_id": rec.event_id,
        "status": rec.status.value,
        "measurement": rec.measurement.value,
        "station": rec.station,
        "settlement_date": rec.settlement_date.isoformat() if rec.settlement_date else None,
        "weather_event_id": rec.weather_event_id,
        "shape": rec.shape.value if rec.shape else None,
        "lower": rec.lower,
        "upper": rec.upper,
        "settlement_source": rec.settlement_source,
        "title": rec.title,
        "subtitle": rec.subtitle,
        "close_time": rec.close_time.isoformat() if rec.close_time else None,
        "metadata_source": market.get("_metadata_source"),
        "reasons": list(rec.reasons),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Build strict weather-only catalog from a PMXT Kalshi archive file")
    ap.add_argument("pmxt_path", type=Path)
    ap.add_argument("--output", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog.parquet"))
    ap.add_argument("--metadata-jsonl", type=Path, default=Path("/data/weather/raw/kalshi/market_metadata/kalshi_weather_metadata.jsonl"))
    ap.add_argument("--max-candidates", type=int, default=0, help="0 = all candidate tickers")
    ap.add_argument("--batch-size", type=int, default=100, help="tickers per official Kalshi metadata request")
    ap.add_argument("--request-delay", type=float, default=0.5, help="polite delay between batch requests")
    args = ap.parse_args()

    if not 1 <= args.batch_size <= 250:
        raise SystemExit("--batch-size must be between 1 and 250")

    tickers = candidate_tickers(args.pmxt_path)
    if args.max_candidates > 0:
        tickers = tickers[:args.max_candidates]
    print(f"candidate_tickers={len(tickers)}")

    args.metadata_jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cached = load_cached_metadata(args.metadata_jsonl)
    print(f"cached_metadata={len(cached)}")

    new_fetches = 0
    new_source_counts: Counter[str] = Counter()
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": "weather-alpha-lab/0.1"}) as client, args.metadata_jsonl.open("a", encoding="utf-8") as raw:
        new_fetches, new_source_counts = bulk_fetch_missing(
            tickers,
            cached,
            client,
            raw,
            batch_size=args.batch_size,
            request_delay_s=args.request_delay,
        )

    rows: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    for ticker in tickers:
        market = cached.get(ticker)
        if market is None:
            continue
        source_counts[str(market.get("_metadata_source") or "cached_legacy")] += 1
        row = catalog_row(market)
        if row is not None:
            rows.append(row)

    by_contract = {row["contract_id"]: row for row in rows}
    rows = [by_contract[key] for key in sorted(by_contract)]
    table = pa.Table.from_pylist(rows) if rows else pa.table({"contract_id": pa.array([], type=pa.string())})
    pq.write_table(table, args.output)

    unresolved = [ticker for ticker in tickers if ticker not in cached]
    counts = Counter(r["status"] for r in rows)
    print(f"official_metadata_missing={len(unresolved)}")
    print(f"new_fetches={new_fetches}")
    print(f"cached_metadata={len(cached)}")
    print(f"new_metadata_source_counts={dict(new_source_counts)}")
    print(f"metadata_source_counts={dict(source_counts)}")
    print(f"retained={len(rows)}")
    print(f"status_counts={dict(counts)}")
    print(f"output={args.output}")
    print(f"metadata_jsonl={args.metadata_jsonl}")
    if unresolved:
        print("unresolved_sample=" + repr(unresolved[:25]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
