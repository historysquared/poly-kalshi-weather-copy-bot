#!/usr/bin/env python3
"""Cache recent Kalshi weather trades and 1-minute candles from Kalshi itself.

This is intentionally independent of PMXT. It uses only public market-data REST
endpoints and writes append-free, deterministic per-ticker JSON files for replay.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq

BASE = "https://external-api.kalshi.com/trade-api/v2"


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_json(client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    for attempt in range(7):
        r = client.get(BASE + path, params=params)
        if r.status_code not in {429} and not 500 <= r.status_code < 600:
            if r.status_code == 404:
                return {}
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {}
        time.sleep(min(30.0, 2**attempt))
    raise RuntimeError(f"retries exhausted path={path}")


def fetch_trades(client: httpx.Client, ticker: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = {}
    for endpoint in ("/markets/trades", "/historical/trades"):
        cursor = ""
        while True:
            params: dict[str, Any] = {
                "ticker": ticker,
                "min_ts": start_ts,
                "max_ts": end_ts,
                "limit": 1000,
                "is_block_trade": "false",
            }
            if cursor:
                params["cursor"] = cursor
            payload = get_json(client, endpoint, params)
            rows = payload.get("trades") or []
            if not isinstance(rows, list):
                rows = []
            for row in rows:
                if isinstance(row, dict):
                    key = str(row.get("trade_id") or json.dumps(row, sort_keys=True))
                    combined[key] = row
            cursor = str(payload.get("cursor") or "")
            if not cursor:
                break
    return sorted(combined.values(), key=lambda r: str(r.get("created_time") or ""))


def fetch_candles(client: httpx.Client, series: str, ticker: str, start_ts: int, end_ts: int) -> tuple[str, list[dict[str, Any]]]:
    params = {"start_ts": start_ts, "end_ts": end_ts, "period_interval": 1}
    live = get_json(client, f"/series/{series}/markets/{ticker}/candlesticks", params)
    rows = live.get("candlesticks") or []
    if rows:
        return "live", rows
    hist = get_json(client, f"/historical/markets/{ticker}/candlesticks", params)
    return "historical", hist.get("candlesticks") or []


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    p = argparse.ArgumentParser(description="Download recent Kalshi weather trades and 1m candles")
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_recent_resolved.parquet"))
    p.add_argument("--output-dir", type=Path, default=Path("/data/weather/raw/kalshi/recent_weather_marketdata"))
    p.add_argument("--hours-before-close", type=int, default=12)
    p.add_argument("--hours-after-close", type=int, default=1)
    p.add_argument("--max-contracts", type=int, default=0, help="0 means all exact contracts")
    p.add_argument("--sleep", type=float, default=0.03)
    args = p.parse_args()

    rows = pq.read_table(args.catalog).to_pylist()
    exact = [r for r in rows if r.get("status") == "EXACT" and r.get("contract_id")]
    exact.sort(key=lambda r: (str(r.get("settlement_date") or ""), str(r.get("contract_id") or "")), reverse=True)
    if args.max_contracts:
        exact = exact[: args.max_contracts]

    manifest: list[dict[str, Any]] = []
    trades_dir = args.output_dir / "trades"
    candles_dir = args.output_dir / "candles_1m"

    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent":"weather-alpha-kalshi-recent/0.1"}) as client:
        for i, row in enumerate(exact, 1):
            ticker = str(row["contract_id"])
            series = ticker.split("-", 1)[0]
            close = parse_dt(row.get("close_time"))
            if close is None:
                day = datetime.fromisoformat(str(row["settlement_date"])[:10]).replace(tzinfo=timezone.utc)
                close = day + timedelta(days=1)
            start = close - timedelta(hours=args.hours_before_close)
            end = close + timedelta(hours=args.hours_after_close)
            start_ts, end_ts = int(start.timestamp()), int(end.timestamp())

            status = "OK"
            error = None
            trades: list[dict[str, Any]] = []
            candles: list[dict[str, Any]] = []
            candle_source = None
            try:
                trades = fetch_trades(client, ticker, start_ts, end_ts)
                candle_source, candles = fetch_candles(client, series, ticker, start_ts, end_ts)
                atomic_json(
                    trades_dir / f"{ticker}.json",
                    {"ticker": ticker, "start": start.isoformat(), "end": end.isoformat(), "trades": trades},
                )
                atomic_json(
                    candles_dir / f"{ticker}.json",
                    {"ticker": ticker, "start": start.isoformat(), "end": end.isoformat(), "source": candle_source, "candlesticks": candles},
                )
            except Exception as exc:
                status = f"ERROR:{type(exc).__name__}"
                error = str(exc)

            manifest.append(
                {
                    "ticker": ticker,
                    "station": row.get("station"),
                    "settlement_date": row.get("settlement_date"),
                    "window_start": start.isoformat(),
                    "window_end": end.isoformat(),
                    "trades": len(trades),
                    "candles_1m": len(candles),
                    "candle_source": candle_source,
                    "status": status,
                    "error": error,
                }
            )
            if i % 20 == 0 or status != "OK":
                print(f"progress={i}/{len(exact)} ticker={ticker} trades={len(trades)} candles={len(candles)} status={status}", flush=True)
                atomic_json(args.output_dir / "manifest.json", manifest)
            time.sleep(max(0.0, args.sleep))

    atomic_json(args.output_dir / "manifest.json", manifest)
    ok = sum(x["status"] == "OK" for x in manifest)
    trade_rows = sum(int(x["trades"]) for x in manifest)
    candle_rows = sum(int(x["candles_1m"]) for x in manifest)
    dates = sorted({str(x["settlement_date"])[:10] for x in manifest if x.get("settlement_date")})
    print(f"contracts={len(manifest)} ok={ok} trades={trade_rows} candles_1m={candle_rows}")
    print(f"dates={len(dates)} oldest={dates[0] if dates else None} newest={dates[-1] if dates else None}")
    print(f"manifest={args.output_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
