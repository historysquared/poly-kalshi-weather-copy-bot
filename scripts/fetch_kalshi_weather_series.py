from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
import pyarrow.parquet as pq

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"


def series_from_contract(contract_id: str) -> str:
    return contract_id.split("-", 1)[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch authoritative Kalshi series metadata for weather catalog contracts")
    ap.add_argument("catalog", type=Path)
    ap.add_argument("--output", type=Path, default=Path("/data/weather/raw/kalshi/market_metadata/kalshi_weather_series.jsonl"))
    args = ap.parse_args()

    rows = pq.read_table(args.catalog, columns=["contract_id"]).to_pylist()
    series = sorted({series_from_contract(str(r["contract_id"])) for r in rows if r.get("contract_id")})
    args.output.parent.mkdir(parents=True, exist_ok=True)

    print(f"series_count={len(series)}")
    with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": "weather-alpha-lab/0.2"}) as client, args.output.open("w", encoding="utf-8") as handle:
        for ticker in series:
            response = client.get(f"{KALSHI_BASE}/series/{ticker}", params={"include_product_metadata": "true"})
            response.raise_for_status()
            payload = response.json()
            item = payload.get("series", payload)
            if not isinstance(item, dict):
                continue
            handle.write(json.dumps(item, sort_keys=True, default=str) + "\n")
            settlements = item.get("settlement_sources") or []
            print({
                "ticker": ticker,
                "title": item.get("title"),
                "category": item.get("category"),
                "settlement_sources": settlements,
                "product_metadata": item.get("product_metadata"),
            })

    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
