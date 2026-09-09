from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.markets.kalshi_weather_resolver import enrich_catalog_record, resolve_weather_rules
from weather_alpha.markets.weather_catalog import classify_kalshi_market

BASE = "https://external-api.kalshi.com/trade-api/v2"


def load_jsonl(path: Path, key: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        value = str(row.get(key) or "")
        if value:
            out[value] = row
    return out


def get_json(client: httpx.Client, path: str) -> dict[str, Any]:
    for attempt in range(7):
        r = client.get(BASE + path)
        if r.status_code != 429 and not 500 <= r.status_code < 600:
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {}
        time.sleep(min(30.0, 2 ** attempt))
    raise RuntimeError(f"retries exhausted: {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Resolve Kalshi weather station/date/source from official market+event+series payloads")
    ap.add_argument("catalog", type=Path)
    ap.add_argument("--market-jsonl", type=Path, default=Path("/data/weather/raw/kalshi/market_metadata/kalshi_weather_metadata.jsonl"))
    ap.add_argument("--series-jsonl", type=Path, default=Path("/data/weather/raw/kalshi/market_metadata/kalshi_weather_series.jsonl"))
    ap.add_argument("--event-jsonl", type=Path, default=Path("/data/weather/raw/kalshi/market_metadata/kalshi_weather_events.jsonl"))
    ap.add_argument("--output", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_resolved.parquet"))
    args = ap.parse_args()

    market_cache = load_jsonl(args.market_jsonl, "ticker")
    series_cache = load_jsonl(args.series_jsonl, "ticker")
    event_cache = load_jsonl(args.event_jsonl, "event_ticker")
    # Some event responses use ticker rather than event_ticker.
    event_cache.update(load_jsonl(args.event_jsonl, "ticker"))
    catalog = pq.read_table(args.catalog).to_pylist()
    args.event_jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    event_ids = sorted({str(r.get("event_id") or "") for r in catalog if r.get("event_id")})
    series_ids = sorted({str(r.get("contract_id") or "").split("-", 1)[0] for r in catalog if r.get("contract_id")})
    with httpx.Client(timeout=25.0, follow_redirects=True, headers={"User-Agent": "weather-alpha-lab/0.3"}) as client:
        with args.event_jsonl.open("a", encoding="utf-8") as eh:
            for event_id in event_ids:
                if event_id in event_cache:
                    continue
                payload = get_json(client, f"/events/{event_id}")
                item = payload.get("event", payload)
                if isinstance(item, dict):
                    item = dict(item); item.setdefault("event_ticker", event_id)
                    event_cache[event_id] = item
                    eh.write(json.dumps(item, sort_keys=True, default=str) + "\n"); eh.flush()
                time.sleep(0.15)
        with args.series_jsonl.open("a", encoding="utf-8") as sh:
            for series_id in series_ids:
                if series_id in series_cache:
                    continue
                payload = get_json(client, f"/series/{series_id}?include_product_metadata=true")
                item = payload.get("series", payload)
                if isinstance(item, dict):
                    item = dict(item); item.setdefault("ticker", series_id)
                    series_cache[series_id] = item
                    sh.write(json.dumps(item, sort_keys=True, default=str) + "\n"); sh.flush()
                time.sleep(0.15)

    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for old in catalog:
        ticker = str(old.get("contract_id") or "")
        market = market_cache.get(ticker)
        if market is None:
            # The normalized catalog intentionally does not contain enough raw rule text to prove EXACT.
            counts["MISSING_MARKET_RAW"] += 1
            continue
        event_id = str(market.get("event_ticker") or old.get("event_id") or "")
        series_id = ticker.split("-", 1)[0]
        rec = classify_kalshi_market(market)
        ev = resolve_weather_rules(market, event_cache.get(event_id), series_cache.get(series_id))
        enriched = enrich_catalog_record(rec, ev)
        counts[enriched.status.value] += 1
        rows.append({
            "venue": enriched.venue,
            "contract_id": enriched.contract_id,
            "event_id": enriched.event_id,
            "status": enriched.status.value,
            "measurement": enriched.measurement.value,
            "station": enriched.station,
            "settlement_date": enriched.settlement_date.isoformat() if enriched.settlement_date else None,
            "weather_event_id": enriched.weather_event_id,
            "shape": enriched.shape.value if enriched.shape else None,
            "lower": enriched.lower,
            "upper": enriched.upper,
            "settlement_source": enriched.settlement_source,
            "close_time": enriched.close_time.isoformat() if enriched.close_time else None,
            "ticker_date_check": ev.ticker_date_check.isoformat() if ev.ticker_date_check else None,
            "ticker_date_matches": ev.ticker_date_matches,
            "station_evidence": list(ev.station_evidence),
            "date_evidence": list(ev.date_evidence),
            "source_evidence": list(ev.source_evidence),
            "reasons": list(enriched.reasons),
        })

    pq.write_table(pa.Table.from_pylist(rows), args.output)
    print(f"rows={len(rows)} status_counts={dict(counts)}")
    print(f"events_cached={len(event_cache)} series_cached={len(series_cache)}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
