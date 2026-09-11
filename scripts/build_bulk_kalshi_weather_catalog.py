from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.markets.kalshi_weather_resolver import enrich_catalog_record, resolve_weather_rules
from weather_alpha.markets.weather_catalog import classify_kalshi_market

BASE = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_SERIES = ("KXHIGHNY", "KXHIGHCHI", "KXHIGHMIA", "KXHIGHLAX", "KXHIGHDEN")


def get_json(client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    for attempt in range(7):
        response = client.get(BASE + path, params=params)
        if response.status_code != 429 and not 500 <= response.status_code < 600:
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        time.sleep(min(30.0, 2 ** attempt))
    raise RuntimeError(f"retries exhausted: {path} params={params}")


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



def main() -> int:
    p = argparse.ArgumentParser(description="Fetch all historical Kalshi daily-high weather markets for configured recurring series and resolve exact settlement truth")
    p.add_argument("--series", default=",".join(DEFAULT_SERIES))
    p.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 1, 1))
    p.add_argument("--end-date", type=date.fromisoformat, default=None)
    p.add_argument("--market-jsonl", type=Path, default=Path("/data/weather/raw/kalshi/market_metadata/kalshi_weather_historical_bulk.jsonl"))
    p.add_argument("--event-jsonl", type=Path, default=Path("/data/weather/raw/kalshi/market_metadata/kalshi_weather_events_bulk.jsonl"))
    p.add_argument("--series-jsonl", type=Path, default=Path("/data/weather/raw/kalshi/market_metadata/kalshi_weather_series.jsonl"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_bulk_resolved.parquet"))
    args = p.parse_args()

    series_ids = [x.strip().upper() for x in args.series.split(",") if x.strip()]
    event_cache = load_jsonl(args.event_jsonl, "event_ticker")
    series_cache = load_jsonl(args.series_jsonl, "ticker")
    args.market_jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    raw_markets: dict[str, dict[str, Any]] = {}
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent":"weather-alpha-lab/0.5"}) as client:
        with args.market_jsonl.open("a", encoding="utf-8") as mh:
            for series in series_ids:
                source_counts: Counter[str] = Counter()
                pages = 0
                # Kalshi partitions settled markets between the live and historical APIs.
                # Recent settled markets (roughly the rolling live window) are absent from
                # /historical/markets, so query BOTH tiers and de-duplicate by ticker.
                for endpoint, source_name in (("/markets", "live"), ("/historical/markets", "historical")):
                    cursor = ""
                    while True:
                        params: dict[str, Any] = {"limit": 1000, "series_ticker": series}
                        if cursor:
                            params["cursor"] = cursor
                        payload = get_json(client, endpoint, params)
                        markets = payload.get("markets") or []
                        pages += 1
                        for market in markets:
                            if not isinstance(market, dict):
                                continue
                            ticker = str(market.get("ticker") or "")
                            event_ticker = str(market.get("event_ticker") or "")
                            if not ticker:
                                continue
                            # Guard against a server/filter regression without inferring dates
                            # from ticker text. Series events are documented to share the series
                            # ticker prefix.
                            if event_ticker and not event_ticker.startswith(series + "-"):
                                source_counts["wrong_series_skipped"] += 1
                                continue
                            item = dict(market)
                            item["_queried_series"] = series
                            item["_api_tier"] = source_name
                            raw_markets[ticker] = item
                            source_counts[source_name] += 1
                            mh.write(json.dumps(item, sort_keys=True, default=str) + "\n")
                        cursor = str(payload.get("cursor") or "")
                        if not cursor:
                            break
                print(f"series={series} pages={pages} source_counts={dict(source_counts)} total_unique_markets={sum(1 for m in raw_markets.values() if m.get('_queried_series') == series)}", flush=True)

        event_ids = sorted({str(m.get("event_ticker") or "") for m in raw_markets.values() if m.get("event_ticker")})
        with args.event_jsonl.open("a", encoding="utf-8") as eh:
            for i, event_id in enumerate(event_ids, 1):
                if event_id in event_cache:
                    continue
                payload = get_json(client, f"/events/{event_id}")
                item = payload.get("event", payload)
                if isinstance(item, dict):
                    item = dict(item)
                    item.setdefault("event_ticker", event_id)
                    event_cache[event_id] = item
                    eh.write(json.dumps(item, sort_keys=True, default=str) + "\n")
                    eh.flush()
                if i % 100 == 0:
                    print(f"events_fetched={i}/{len(event_ids)}")
                time.sleep(0.05)

        for series in series_ids:
            if series in series_cache:
                continue
            payload = get_json(client, f"/series/{series}", {"include_product_metadata":"true"})
            item = payload.get("series", payload)
            if isinstance(item, dict):
                item = dict(item)
                item.setdefault("ticker", series)
                series_cache[series] = item

    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    event_counts: Counter[str] = Counter()
    for ticker, market in sorted(raw_markets.items()):
        event_id = str(market.get("event_ticker") or "")
        series_id = str(market.get("_queried_series") or ticker.split("-", 1)[0])
        rec = classify_kalshi_market(market)
        evidence = resolve_weather_rules(market, event_cache.get(event_id), series_cache.get(series_id))
        enriched = enrich_catalog_record(rec, evidence)
        # Date filtering must happen AFTER rules resolution. Kalshi explicitly warns
        # clients not to infer relationships/dates from ticker strings.
        if enriched.settlement_date and enriched.settlement_date < args.start_date:
            continue
        if args.end_date and enriched.settlement_date and enriched.settlement_date > args.end_date:
            continue
        counts[enriched.status.value] += 1
        if enriched.weather_event_id:
            event_counts[enriched.weather_event_id] += 1
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
            "result": market.get("result"),
            "volume_fp": market.get("volume_fp"),
            "open_interest_fp": market.get("open_interest_fp"),
            "ticker_date_check": evidence.ticker_date_check.isoformat() if evidence.ticker_date_check else None,
            "ticker_date_matches": evidence.ticker_date_matches,
            "series_drift_risk": evidence.series_drift_risk,
            "station_evidence": list(evidence.station_evidence),
            "date_evidence": list(evidence.date_evidence),
            "source_evidence": list(evidence.source_evidence),
            "reasons": list(enriched.reasons),
        })

    pq.write_table(pa.Table.from_pylist(rows), args.output, compression="zstd")
    exact = [r for r in rows if r["status"] == "EXACT"]
    dates = {r["settlement_date"] for r in exact if r.get("settlement_date")}
    stations = {r["station"] for r in exact if r.get("station")}
    events = {r["weather_event_id"] for r in exact if r.get("weather_event_id")}
    print(f"rows={len(rows)} status_counts={dict(counts)}")
    print(f"exact_contracts={len(exact)} exact_events={len(events)} settlement_dates={len(dates)} stations={len(stations)}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
