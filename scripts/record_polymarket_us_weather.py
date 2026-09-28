#!/usr/bin/env python3
"""Read-only Polymarket US weather catalog/quote snapshot collector."""
from __future__ import annotations

import argparse
import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weather_alpha.markets.polymarket_us import PolymarketUSClient

SCHEMA_VERSION = 1
COLLECTOR_VERSION = "polymarket-us-weather-snapshot-v1"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _quote_value(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compact_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event.get("id") or "")
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            rows.append({
                "event_id": event_id,
                "event_slug": event.get("slug"),
                "event_title": event.get("title"),
                "category": event.get("category"),
                "market_id": market.get("id"),
                "market_slug": market.get("slug"),
                "market_title": market.get("title"),
                "status": market.get("status"),
                "active": market.get("active"),
                "closed": market.get("closed"),
                "best_bid": _quote_value(market.get("bestBidQuote")),
                "best_ask": _quote_value(market.get("bestAskQuote")),
                "last_price": _quote_value(market.get("lastTradePrice")),
                "tick_size": market.get("orderPriceMinTickSize"),
                "fee_coefficient": market.get("feeCoefficient"),
                "minimum_trade_qty": market.get("minimumTradeQty"),
            })
    return rows


class HourlyWriter:
    def __init__(self, root: Path):
        self.root = root
        self.hour: str | None = None
        self.fh = None

    def write(self, ts: datetime, row: dict[str, Any]) -> None:
        hour = ts.strftime("%Y-%m-%dT%H")
        if hour != self.hour:
            self.close()
            day = self.root / ts.strftime("%Y/%m/%d")
            day.mkdir(parents=True, exist_ok=True)
            self.fh = gzip.open(day / f"polymarket_us_weather_{hour}.jsonl.gz", "at", encoding="utf-8")
            self.hour = hour
        self.fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        self.fh.flush()

    def close(self) -> None:
        if self.fh is not None:
            self.fh.close()
        self.fh = None
        self.hour = None


def collect_once(client: PolymarketUSClient, now: datetime) -> dict[str, Any]:
    events = client.discover_weather_events(include_closed=False)
    markets = compact_events(events)
    return {
        "schema_version": SCHEMA_VERSION,
        "collector_version": COLLECTOR_VERSION,
        "local_receive_timestamp": now.isoformat(),
        "read_only": True,
        "live_order_submission": False,
        "event_count": len(events),
        "market_count": len(markets),
        "markets": markets,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Read-only Polymarket US all-weather snapshot collector")
    p.add_argument("--interval-seconds", type=float, default=60.0)
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--raw-dir", type=Path, default=Path("/data/weather/raw/polymarket_us/weather_snapshots"))
    p.add_argument("--current-output", type=Path, default=Path("/data/weather/status/polymarket_us_weather_current.json"))
    p.add_argument("--health-output", type=Path, default=Path("/data/weather/status/polymarket_us_weather_health.json"))
    p.add_argument("--once", action="store_true")
    args = p.parse_args()
    if args.interval_seconds < 10:
        raise SystemExit("--interval-seconds must be >=10")

    client = PolymarketUSClient(timeout=args.timeout)
    writer = HourlyWriter(args.raw_dir)
    failures = 0
    print("mode=POLYMARKET_US_WEATHER_SNAPSHOT read_only=true live_order_submission=false", flush=True)
    try:
        while True:
            now = utcnow()
            try:
                row = collect_once(client, now)
                writer.write(now, row)
                atomic_json(args.current_output, row)
                failures = 0
                health = {
                    "generated_at": now.isoformat(),
                    "collector_version": COLLECTOR_VERSION,
                    "read_only": True,
                    "event_count": row["event_count"],
                    "market_count": row["market_count"],
                    "last_error": None,
                    "consecutive_failures": 0,
                }
                atomic_json(args.health_output, health)
                print(f"snapshot events={row['event_count']} markets={row['market_count']} at={now.isoformat()}", flush=True)
            except Exception as exc:
                failures += 1
                atomic_json(args.health_output, {
                    "generated_at": now.isoformat(), "collector_version": COLLECTOR_VERSION,
                    "read_only": True, "last_error": f"{type(exc).__name__}:{exc}",
                    "consecutive_failures": failures,
                })
                print(f"snapshot_error={type(exc).__name__}:{exc}", flush=True)
            if args.once:
                break
            time.sleep(args.interval_seconds)
    finally:
        writer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
