#!/usr/bin/env python3
"""Build a read-only catalog of every currently open Kalshi Climate and Weather series."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

BASE = "https://external-api.kalshi.com/trade-api/v2"
CATEGORY = "Climate and Weather"


def get_json(client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    for attempt in range(7):
        response = client.get(BASE + path, params=params)
        if response.status_code == 200:
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        if response.status_code != 429 and not 500 <= response.status_code < 600:
            response.raise_for_status()
        time.sleep(min(20.0, 1.0 + 2.0 ** attempt))
    raise RuntimeError(f"retries exhausted: {path} {params}")


def main() -> int:
    p = argparse.ArgumentParser(description="Discover all currently open Kalshi Climate and Weather markets")
    p.add_argument("--output", type=Path, default=Path("/data/weather/status/kalshi_all_weather_live_catalog.json"))
    p.add_argument("--delay-seconds", type=float, default=0.55)
    p.add_argument("--timeout", type=float, default=30.0)
    args = p.parse_args()

    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "category": CATEGORY,
        "read_only": True,
        "live_order_submission": False,
        "active_series": [],
    }
    with httpx.Client(timeout=args.timeout, follow_redirects=True,
                      headers={"User-Agent": "weather-alpha-catalog/1"}) as client:
        series = get_json(client, "/series", {"category": CATEGORY}).get("series") or []
        for index, item in enumerate(series, 1):
            if not isinstance(item, dict) or not item.get("ticker"):
                continue
            series_id = str(item["ticker"])
            markets = get_json(client, "/markets", {
                "series_ticker": series_id, "status": "open", "limit": 1000, "mve_filter": "exclude",
            }).get("markets") or []
            markets = [m for m in markets if isinstance(m, dict)]
            if markets:
                result["active_series"].append({
                    "ticker": series_id, "title": item.get("title"), "frequency": item.get("frequency"),
                    "tags": item.get("tags") or [], "settlement_sources": item.get("settlement_sources") or [],
                    "open_market_count": len(markets), "markets": markets,
                })
            if index % 25 == 0:
                print(f"progress={index}/{len(series)} active_series={len(result['active_series'])}", flush=True)
            time.sleep(max(0.0, args.delay_seconds))

    result["catalog_series_count"] = len(series)
    result["active_series_count"] = len(result["active_series"])
    result["open_market_count"] = sum(int(x["open_market_count"]) for x in result["active_series"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(args.output)
    print(
        f"catalog_series={result['catalog_series_count']} active_series={result['active_series_count']} "
        f"open_markets={result['open_market_count']} output={args.output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
