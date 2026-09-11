from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.markets.source_family import detect_settlement_source_family
from weather_alpha.markets.kalshi_weather_resolver import ticker_event_date

BASE = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_SERIES = ("KXHIGHNY", "KXHIGHCHI", "KXHIGHMIA", "KXHIGHLAX", "KXHIGHDEN")

# These are predictive ASOS proxies only. They are NOT asserted to be the
# Weather Company settlement station/source.
ASOS_PROXY_BY_SERIES = {
    "KXHIGHNY": "KNYC",
    "KXHIGHCHI": "KMDW",
    "KXHIGHMIA": "KMIA",
    "KXHIGHLAX": "KLAX",
    "KXHIGHDEN": "KDEN",
}


def get_json(client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    for attempt in range(7):
        r = client.get(BASE + path, params=params)
        if r.status_code != 429 and not 500 <= r.status_code < 600:
            r.raise_for_status()
            payload = r.json()
            return payload if isinstance(payload, dict) else {}
        time.sleep(min(30.0, 2 ** attempt))
    raise RuntimeError(f"retries exhausted: {path} params={params}")


def parse_day(market: dict[str, Any]) -> date | None:
    event_ticker = str(market.get("event_ticker") or market.get("ticker") or "")
    d = ticker_event_date(event_ticker)
    if d is not None:
        return d
    for key in ("occurrence_datetime", "expiration_time", "close_time", "expected_expiration_time"):
        raw = market.get(key)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return None


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as h:
        h.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description="Build a recent Kalshi Weather Company regime catalog from full historical market rules")
    p.add_argument("--series", default=",".join(DEFAULT_SERIES))
    p.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 8, 1))
    p.add_argument("--end-date", type=date.fromisoformat, default=None)
    p.add_argument("--output", type=Path, default=Path("/data/weather/normalized/markets/weather_company_regime_history.parquet"))
    p.add_argument("--raw-audit", type=Path, default=Path("/data/weather/raw/kalshi/market_metadata/weather_company_regime_history.jsonl"))
    args = p.parse_args()

    series_ids = [s.strip().upper() for s in args.series.split(",") if s.strip()]
    args.output.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent":"weather-alpha-regime/0.1"}) as client:
        series_cache: dict[str, dict[str, Any]] = {}
        event_cache: dict[str, dict[str, Any]] = {}

        for series in series_ids:
            sp = get_json(client, f"/series/{series}", {"include_product_metadata":"true"})
            srow = sp.get("series", sp)
            series_cache[series] = srow if isinstance(srow, dict) else {}

            cursor = ""
            while True:
                params: dict[str, Any] = {"limit": 1000, "series_ticker": series}
                if cursor:
                    params["cursor"] = cursor
                payload = get_json(client, "/historical/markets", params)
                markets = payload.get("markets") or []
                for listed in markets:
                    if not isinstance(listed, dict):
                        continue
                    ticker = str(listed.get("ticker") or "")
                    if not ticker:
                        continue
                    day = parse_day(listed)
                    if day is None or day < args.start_date or (args.end_date and day > args.end_date):
                        continue

                    detail_payload = get_json(client, f"/historical/markets/{ticker}")
                    detail = detail_payload.get("market", detail_payload)
                    market = dict(listed)
                    if isinstance(detail, dict):
                        market.update(detail)

                    event_id = str(market.get("event_ticker") or "")
                    if event_id and event_id not in event_cache:
                        try:
                            ep = get_json(client, f"/events/{event_id}")
                            ev = ep.get("event", ep)
                            event_cache[event_id] = ev if isinstance(ev, dict) else {}
                        except httpx.HTTPStatusError:
                            event_cache[event_id] = {}
                    event = event_cache.get(event_id, {})
                    detection = detect_settlement_source_family(market, event, series_cache.get(series))
                    counts[detection.family] += 1

                    day = parse_day(market) or day
                    proxy = ASOS_PROXY_BY_SERIES.get(series)
                    row = {
                        "venue": "kalshi",
                        "series_ticker": series,
                        "contract_id": ticker,
                        "event_id": event_id,
                        "settlement_date": day.isoformat(),
                        "source_family": detection.family,
                        "source_evidence": list(detection.evidence),
                        "asos_proxy_station": proxy,
                        "proxy_role": "PREDICTIVE_ASOS_PROXY_NOT_SETTLEMENT_SOURCE" if proxy else None,
                        "title": market.get("title"),
                        "subtitle": market.get("subtitle") or market.get("yes_sub_title"),
                        "rules_primary": market.get("rules_primary"),
                        "rules_secondary": market.get("rules_secondary"),
                        "close_time": market.get("close_time") or market.get("expiration_time") or market.get("expected_expiration_time"),
                        "settlement_ts": market.get("settlement_ts"),
                        "result": market.get("result"),
                        "expiration_value": market.get("expiration_value"),
                        "settlement_value_dollars": market.get("settlement_value_dollars"),
                        "floor_strike": market.get("floor_strike"),
                        "cap_strike": market.get("cap_strike"),
                        "strike_type": market.get("strike_type"),
                        "volume_fp": market.get("volume_fp"),
                        "open_interest_fp": market.get("open_interest_fp"),
                    }
                    rows.append(row)
                    append_jsonl(args.raw_audit, {"market": market, "event": event, "series": series_cache.get(series), "row": row})

                cursor = str(payload.get("cursor") or "")
                if not cursor:
                    break

    rows.sort(key=lambda r: (r["settlement_date"], r["series_ticker"], r["contract_id"]), reverse=True)
    pq.write_table(pa.Table.from_pylist(rows), args.output, compression="zstd")
    wc = [r for r in rows if r["source_family"] == "WEATHER_COMPANY"]
    print(f"rows={len(rows)} source_counts={dict(counts)}")
    print(f"weather_company_rows={len(wc)} weather_company_dates={len({r['settlement_date'] for r in wc})}")
    for r in wc[:20]:
        print(f"recent={r['settlement_date']} {r['series_ticker']} {r['contract_id']} result={r['result']} expiration_value={r['expiration_value']}")
    print(f"output={args.output}")
    print(f"raw_audit={args.raw_audit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
