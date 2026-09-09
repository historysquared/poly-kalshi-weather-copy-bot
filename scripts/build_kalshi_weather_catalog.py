from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.markets.weather_catalog import CatalogStatus, classify_kalshi_market, looks_weather_like

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"


def candidate_tickers(pmxt_path: Path) -> list[str]:
    """Cheap first-stage candidate discovery from archive tickers only.

    This is intentionally recall-oriented and is NOT final classification. Every
    retained candidate is verified against official Kalshi metadata before it can
    become EXACT.
    """
    table = pq.read_table(pmxt_path, columns=["market_ticker"])
    unique = sorted(set(str(x) for x in table.column("market_ticker").to_pylist() if x))
    candidates=[]
    for ticker in unique:
        if looks_weather_like({"ticker": ticker}):
            candidates.append(ticker)
    return candidates


def fetch_market(ticker: str, client: httpx.Client) -> dict[str, Any] | None:
    r = client.get(f"{KALSHI_BASE}/markets/{ticker}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    payload = r.json()
    market = payload.get("market", payload)
    return market if isinstance(market, dict) else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Build strict weather-only catalog from a PMXT Kalshi archive file")
    ap.add_argument("pmxt_path", type=Path)
    ap.add_argument("--output", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog.parquet"))
    ap.add_argument("--metadata-jsonl", type=Path, default=Path("/data/weather/raw/kalshi/market_metadata/kalshi_weather_metadata.jsonl"))
    ap.add_argument("--max-candidates", type=int, default=0, help="0 = all candidate tickers")
    args = ap.parse_args()

    tickers = candidate_tickers(args.pmxt_path)
    if args.max_candidates > 0:
        tickers = tickers[:args.max_candidates]
    print(f"candidate_tickers={len(tickers)}")

    args.metadata_jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows=[]
    missing=0
    with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent":"weather-alpha-lab/0.1"}) as client, args.metadata_jsonl.open("a", encoding="utf-8") as raw:
        for i,ticker in enumerate(tickers,1):
            market=fetch_market(ticker,client)
            if market is None:
                missing += 1
                continue
            raw.write(json.dumps(market, sort_keys=True, default=str)+"\n")
            rec=classify_kalshi_market(market)
            if rec.status != CatalogStatus.REJECT:
                rows.append({
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
                })
            if i % 100 == 0:
                print(f"fetched={i}/{len(tickers)} retained={len(rows)} missing={missing}")

    table=pa.Table.from_pylist(rows) if rows else pa.table({"contract_id": pa.array([], type=pa.string())})
    pq.write_table(table,args.output)
    counts=Counter(r["status"] for r in rows)
    print(f"official_metadata_missing={missing}")
    print(f"retained={len(rows)}")
    print(f"status_counts={dict(counts)}")
    print(f"output={args.output}")
    print(f"metadata_jsonl={args.metadata_jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
