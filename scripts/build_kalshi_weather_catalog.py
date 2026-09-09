from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from pathlib import Path
from typing import Any

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
    """Load prior successful responses so interrupted/rate-limited runs resume."""
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


def fetch_market(
    ticker: str,
    client: httpx.Client,
    *,
    max_attempts: int = 8,
    base_delay_s: float = 1.0,
) -> dict[str, Any] | None:
    """Fetch official metadata with bounded exponential backoff for 429/5xx."""
    url = f"{KALSHI_BASE}/markets/{ticker}"
    for attempt in range(max_attempts):
        r = client.get(url)
        if r.status_code == 404:
            return None
        if r.status_code == 429 or 500 <= r.status_code < 600:
            retry_after = r.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else base_delay_s * (2 ** attempt)
            except ValueError:
                delay = base_delay_s * (2 ** attempt)
            delay = min(delay, 60.0) + random.uniform(0.0, 0.25)
            print(f"retry ticker={ticker} status={r.status_code} attempt={attempt+1}/{max_attempts} sleep={delay:.2f}s")
            time.sleep(delay)
            continue
        r.raise_for_status()
        payload = r.json()
        market = payload.get("market", payload)
        return market if isinstance(market, dict) else None
    raise RuntimeError(f"Kalshi metadata retries exhausted for {ticker}")


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
        "reasons": list(rec.reasons),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Build strict weather-only catalog from a PMXT Kalshi archive file")
    ap.add_argument("pmxt_path", type=Path)
    ap.add_argument("--output", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog.parquet"))
    ap.add_argument("--metadata-jsonl", type=Path, default=Path("/data/weather/raw/kalshi/market_metadata/kalshi_weather_metadata.jsonl"))
    ap.add_argument("--max-candidates", type=int, default=0, help="0 = all candidate tickers")
    ap.add_argument("--request-delay", type=float, default=0.35, help="polite delay after each uncached request")
    args = ap.parse_args()

    tickers = candidate_tickers(args.pmxt_path)
    if args.max_candidates > 0:
        tickers = tickers[:args.max_candidates]
    print(f"candidate_tickers={len(tickers)}")

    args.metadata_jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cached = load_cached_metadata(args.metadata_jsonl)
    print(f"cached_metadata={len(cached)}")
    rows: list[dict[str, Any]] = []
    missing = 0
    fetched = 0

    with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": "weather-alpha-lab/0.1"}) as client, args.metadata_jsonl.open("a", encoding="utf-8") as raw:
        for i, ticker in enumerate(tickers, 1):
            market = cached.get(ticker)
            if market is None:
                try:
                    market = fetch_market(ticker, client)
                except (httpx.HTTPError, RuntimeError) as exc:
                    # Preserve progress. A later run will resume this ticker.
                    print(f"fetch_failed ticker={ticker} error={exc}")
                    continue
                fetched += 1
                if market is None:
                    missing += 1
                    time.sleep(args.request_delay)
                    continue
                raw.write(json.dumps(market, sort_keys=True, default=str) + "\n")
                raw.flush()
                cached[ticker] = market
                time.sleep(args.request_delay)

            row = catalog_row(market)
            if row is not None:
                rows.append(row)
            if i % 50 == 0:
                print(f"processed={i}/{len(tickers)} retained={len(rows)} cached={len(cached)} new_fetches={fetched} missing={missing}")

    # Deterministic one-row-per-contract output even across resumed runs.
    by_contract = {row["contract_id"]: row for row in rows}
    rows = [by_contract[key] for key in sorted(by_contract)]
    table = pa.Table.from_pylist(rows) if rows else pa.table({"contract_id": pa.array([], type=pa.string())})
    pq.write_table(table, args.output)
    counts = Counter(r["status"] for r in rows)
    print(f"official_metadata_missing={missing}")
    print(f"new_fetches={fetched}")
    print(f"cached_metadata={len(cached)}")
    print(f"retained={len(rows)}")
    print(f"status_counts={dict(counts)}")
    print(f"output={args.output}")
    print(f"metadata_jsonl={args.metadata_jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
