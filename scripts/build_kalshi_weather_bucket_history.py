#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

BASE = "https://external-api.kalshi.com/trade-api/v2"
SERIES_BY_CITY = {
    "new_york_city": "KXHIGHNY", "chicago": "KXHIGHCHI", "miami": "KXHIGHMIA",
    "los_angeles": "KXHIGHLAX", "san_francisco": "KXHIGHTSFO", "denver": "KXHIGHDEN",
    "boston": "KXHIGHTBOS", "austin": "KXHIGHAUS", "seattle": "KXHIGHTSEA",
    "atlanta": "KXHIGHTATL", "las_vegas": "KXHIGHTLV", "minneapolis": "KXHIGHTMIN",
    "new_orleans": "KXHIGHTNOLA", "washington_dc": "KXHIGHTDC", "philadelphia": "KXHIGHPHIL",
    "san_antonio": "KXHIGHTSATX", "dallas": "KXHIGHTDAL", "oklahoma_city": "KXHIGHTOKC",
    "phoenix": "KXHIGHTPHX", "houston": "KXHIGHTHOU",
}
MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def event_date(value: Any) -> date | None:
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})(?:$|-)", str(value or ""))
    if not m:
        return None
    return date(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3)))


def get_markets(client: httpx.Client, series: str) -> list[dict[str, Any]]:
    for attempt in range(8):
        r = client.get(f"{BASE}/markets", params={"series_ticker": series, "status": "settled", "limit": 1000})
        if r.status_code == 200:
            return [dict(x) for x in r.json().get("markets", []) if isinstance(x, dict)]
        if r.status_code != 429:
            r.raise_for_status()
        time.sleep(min(30.0, 2.0 ** (attempt + 1)))
    raise RuntimeError(f"Kalshi rate-limit retries exhausted for {series}")


def normalized_contract(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": m.get("ticker"),
        "result": m.get("result"),
        "floor_strike": m.get("floor_strike"),
        "cap_strike": m.get("cap_strike"),
        "strike_type": m.get("strike_type"),
        "title": m.get("title"),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Reconstruct exact resolved Kalshi daily-high bucket ladders")
    p.add_argument("--start-date", type=parse_date, required=True)
    p.add_argument("--end-date", type=parse_date, required=True)
    p.add_argument("--output", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_bucket_history.json"))
    args = p.parse_args()

    rows: list[dict[str, Any]] = []
    headers = {"User-Agent": "weather-alpha-bucket-history/0.1"}
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
        for city, series in SERIES_BY_CITY.items():
            by_event: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for market in get_markets(client, series):
                day = event_date(market.get("event_ticker") or market.get("ticker"))
                if day is None or not (args.start_date <= day <= args.end_date):
                    continue
                event = str(market.get("event_ticker") or "")
                by_event.setdefault((day.isoformat(), event), []).append(market)
            for (day, event), markets in sorted(by_event.items()):
                values = {str(m.get("expiration_value")) for m in markets if m.get("expiration_value") not in (None, "")}
                winners = [m for m in markets if str(m.get("result") or "").lower() == "yes"]
                rows.append({
                    "city": city,
                    "series": series,
                    "date": day,
                    "event_id": event,
                    "expiration_value": float(next(iter(values))) if len(values) == 1 else None,
                    "expiration_value_set": sorted(values),
                    "winner_contracts": [m.get("ticker") for m in winners],
                    "contracts": [normalized_contract(m) for m in markets],
                    "validation_ok": len(values) == 1 and len(winners) == 1,
                })
            print(f"city={city} series={series} events={len(by_event)}", flush=True)
            time.sleep(0.5)

    rows.sort(key=lambda x: (x["date"], x["city"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    bad = [r for r in rows if not r["validation_ok"]]
    print(f"output={args.output} rows={len(rows)} cities={len({r['city'] for r in rows})} invalid={len(bad)}")
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
