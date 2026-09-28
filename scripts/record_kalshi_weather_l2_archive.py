#!/usr/bin/env python3
"""Read-only, strategy-independent Kalshi weather L2 raw archive collector."""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import websockets

from weather_alpha.live.kalshi_ws import WS_URL, websocket_auth_headers
from weather_alpha.markets.kalshi_weather_series import ALL_DAILY_TEMPERATURE_SERIES

BASE = "https://external-api.kalshi.com/trade-api/v2"
SCHEMA_VERSION = 1
COLLECTOR_VERSION = "kalshi-weather-l2-archive-v1"
CHANNELS = ("orderbook_delta", "trade", "ticker")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


class HourlyWriter:
    def __init__(self, root: Path, flush_every: int = 25):
        self.root = root
        self.flush_every = max(1, flush_every)
        self.hour: str | None = None
        self.fh = None
        self.count = 0

    def write(self, received: datetime, row: dict[str, Any]) -> None:
        hour = received.strftime("%Y-%m-%dT%H")
        if hour != self.hour:
            self.close()
            day_dir = self.root / received.strftime("%Y/%m/%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            self.fh = gzip.open(day_dir / f"kalshi_weather_l2_{hour}.jsonl.gz", "at", encoding="utf-8")
            self.hour = hour
            self.count = 0
        self.fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        self.count += 1
        if self.count % self.flush_every == 0:
            self.fh.flush()

    def close(self) -> None:
        if self.fh is not None:
            self.fh.flush()
            self.fh.close()
        self.fh = None
        self.hour = None


def market_series(market: dict[str, Any]) -> str:
    value = str(market.get("series_ticker") or "").upper()
    if value:
        return value
    event = str(market.get("event_ticker") or "").upper()
    return event.split("-", 1)[0] if event else ""


async def discover_open_tickers(series: set[str], timeout: float) -> list[str]:
    tickers: set[str] = set()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": "predictionbots-weather-l2/1"}) as client:
        for series_id in sorted(series):
            params = {"series_ticker": series_id, "status": "open", "limit": 1000, "mve_filter": "exclude"}
            response = None
            for attempt in range(7):
                response = await client.get(BASE + "/markets", params=params)
                if response.status_code == 200:
                    break
                if response.status_code != 429 and not 500 <= response.status_code < 600:
                    response.raise_for_status()
                await asyncio.sleep(min(20.0, 1.0 + 2.0 ** attempt))
            if response is None:
                raise RuntimeError(f"market discovery produced no response for {series_id}")
            response.raise_for_status()
            for market in response.json().get("markets", []):
                if not isinstance(market, dict) or market_series(market) != series_id:
                    continue
                ticker = str(market.get("ticker") or "")
                if ticker:
                    tickers.add(ticker)
            await asyncio.sleep(0.45)
    return sorted(tickers)


def load_ticker_cache(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    values = payload.get("tickers") if isinstance(payload, dict) else None
    return sorted({str(x) for x in values or [] if x})


def load_all_weather_catalog_tickers(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    tickers: set[str] = set()
    for series in payload.get("active_series") or []:
        if not isinstance(series, dict):
            continue
        for market in series.get("markets") or []:
            if isinstance(market, dict) and market.get("ticker"):
                tickers.add(str(market["ticker"]))
    return sorted(tickers)


def save_ticker_cache(path: Path, tickers: set[str] | list[str]) -> None:
    atomic_json(path, {"generated_at": utcnow().isoformat(), "collector_version": COLLECTOR_VERSION,
                       "tickers": sorted(set(tickers))})


def health_template(series: list[str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "collector_version": COLLECTOR_VERSION,
        "read_only": True,
        "live_order_submission": False,
        "series_count": len(series),
        "messages": 0,
        "message_types": {},
        "sequence_gaps": 0,
        "duplicates_or_reorders": 0,
        "reconnects": 0,
        "last_message_at": None,
        "last_error": None,
    }


def update_type_count(health: dict[str, Any], msg_type: str) -> None:
    counts = Counter(health.get("message_types") or {})
    counts[msg_type] += 1
    health["message_types"] = dict(counts)


async def run_connection(args, series: list[str], writer: HourlyWriter,
                         health: dict[str, Any], generation: int) -> None:
    tickers = load_all_weather_catalog_tickers(args.all_weather_catalog) if args.all_weather_catalog else []
    if not tickers:
        tickers = load_ticker_cache(args.ticker_cache)
    used_cache = bool(tickers)
    if not tickers:
        tickers = await discover_open_tickers(set(series), args.rest_timeout)
        save_ticker_cache(args.ticker_cache, tickers)
    if not tickers:
        raise RuntimeError("no open configured weather tickers discovered")
    headers = websocket_auth_headers(key_id=args.key_id, private_key_path=args.private_key_path)
    connection_id = str(uuid4())
    health.update({
        "connection_id": connection_id,
        "generation": generation,
        "connected_at": utcnow().isoformat(),
        "current_tickers": len(tickers),
        "last_error": None,
    })
    connect_kwargs = dict(ping_interval=20, ping_timeout=20, max_queue=16384)
    try:
        cm = websockets.connect(args.ws_url, additional_headers=headers, **connect_kwargs)
    except TypeError:
        cm = websockets.connect(args.ws_url, extra_headers=headers, **connect_kwargs)

    async with cm as ws:
        command_id = 1
        await ws.send(json.dumps({"id": command_id, "cmd": "subscribe", "params": {
            "channels": list(CHANNELS), "market_tickers": tickers}}))
        subscribed = set(tickers)
        channel_sids: dict[str, int] = {}
        last_seq: dict[int, int] = {}
        next_refresh = time.monotonic() if used_cache else time.monotonic() + args.refresh_seconds
        refresh_task: asyncio.Task[list[str]] | None = None
        last_health = 0.0
        print(f"archive_ws connected generation={generation} tickers={len(tickers)}", flush=True)
        while True:
            now_mono = time.monotonic()
            if now_mono >= next_refresh and refresh_task is None and len(channel_sids) == len(CHANNELS):
                refresh_task = asyncio.create_task(asyncio.to_thread(load_all_weather_catalog_tickers, args.all_weather_catalog)) if args.all_weather_catalog else asyncio.create_task(discover_open_tickers(set(series), args.rest_timeout))
                next_refresh = float("inf")
            if refresh_task is not None and refresh_task.done():
                fresh = set(refresh_task.result())
                refresh_task = None
                added, removed = fresh - subscribed, subscribed - fresh
                for channel, sid in sorted(channel_sids.items()):
                    if added:
                        command_id += 1
                        await ws.send(json.dumps({"id": command_id, "cmd": "update_subscription", "params": {
                            "sids": [sid], "market_tickers": sorted(added), "action": "add_markets"}}))
                        if channel == "orderbook_delta":
                            command_id += 1
                            await ws.send(json.dumps({"id": command_id, "cmd": "update_subscription", "params": {
                                "sids": [sid], "market_tickers": sorted(added), "action": "get_snapshot"}}))
                    if removed:
                        command_id += 1
                        await ws.send(json.dumps({"id": command_id, "cmd": "update_subscription", "params": {
                            "sids": [sid], "market_tickers": sorted(removed), "action": "delete_markets"}}))
                if added or removed:
                    print(f"ticker_refresh added={len(added)} removed={len(removed)} total={len(fresh)}", flush=True)
                subscribed = fresh
                health["current_tickers"] = len(subscribed)
                save_ticker_cache(args.ticker_cache, subscribed)
                next_refresh = time.monotonic() + args.refresh_seconds

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                if time.monotonic() - last_health >= args.health_interval:
                    health["generated_at"] = utcnow().isoformat()
                    atomic_json(args.health_output, health)
                    last_health = time.monotonic()
                continue
            received = utcnow()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"type": "malformed", "raw_text": str(raw)}
            msg_type = str(data.get("type") or "")
            msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
            if msg_type == "subscribed":
                channel = str(msg.get("channel") or "")
                sid = msg.get("sid")
                if channel in CHANNELS and isinstance(sid, int):
                    channel_sids[channel] = sid

            sid = data.get("sid")
            seq = data.get("seq")
            if isinstance(sid, int) and isinstance(seq, int):
                previous = last_seq.get(sid)
                if previous is not None:
                    if seq > previous + 1:
                        health["sequence_gaps"] += seq - previous - 1
                        health["last_gap"] = {"sid": sid, "previous": previous, "current": seq,
                                              "at": received.isoformat()}
                    elif seq <= previous:
                        health["duplicates_or_reorders"] += 1
                last_seq[sid] = max(seq, previous or seq)

            ticker = str(msg.get("market_ticker") or "") or None
            health["messages"] += 1
            update_type_count(health, msg_type)
            health["last_message_at"] = received.isoformat()
            row = {
                "schema_version": SCHEMA_VERSION,
                "collector_version": COLLECTOR_VERSION,
                "local_receive_timestamp": received.isoformat(),
                "connection_id": connection_id,
                "generation": generation,
                "message_type": msg_type,
                "ticker": ticker,
                "subscription_id": sid,
                "sequence": seq,
                "raw": data,
            }
            writer.write(received, row)
            if msg_type == "error":
                health["last_error"] = msg or data
            if time.monotonic() - last_health >= args.health_interval:
                health["generated_at"] = received.isoformat()
                health["subscription_sids"] = channel_sids
                atomic_json(args.health_output, health)
                last_health = time.monotonic()


async def main_async(args) -> int:
    series = [x.strip().upper() for x in args.series.split(",") if x.strip()]
    health = health_template(series)
    health["scope"] = "ALL_WEATHER_CATALOG" if args.all_weather_catalog else "CONFIGURED_SERIES"
    health["catalog_path"] = str(args.all_weather_catalog) if args.all_weather_catalog else None
    writer = HourlyWriter(args.raw_dir, args.flush_every)
    generation = 0
    backoff = 1.0
    try:
        while True:
            try:
                await run_connection(args, series, writer, health, generation)
                backoff = 1.0
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                health["reconnects"] += 1
                health["last_error"] = f"{type(exc).__name__}:{exc}"
                health["generated_at"] = utcnow().isoformat()
                atomic_json(args.health_output, health)
                print(f"archive_ws_error {health['last_error']} retry_s={backoff}", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(args.max_backoff, backoff * 2)
                generation += 1
    finally:
        writer.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Read-only Kalshi weather L2 raw archive collector")
    p.add_argument("--series", default=",".join(ALL_DAILY_TEMPERATURE_SERIES))
    p.add_argument("--ws-url", default=WS_URL)
    p.add_argument("--key-id", default=os.getenv("KALSHI_API_KEY_ID") or os.getenv("KALSHI_ACCESS_KEY"))
    p.add_argument("--private-key-path", default=os.getenv("KALSHI_PRIVATE_KEY_PATH"))
    p.add_argument("--raw-dir", type=Path, default=Path("/data/weather/raw/kalshi_l2"))
    p.add_argument("--health-output", type=Path, default=Path("/data/weather/status/kalshi_l2_archive_health.json"))
    p.add_argument("--ticker-cache", type=Path, default=Path("/data/weather/status/kalshi_weather_open_tickers.json"))
    p.add_argument("--all-weather-catalog", type=Path, default=None, help="Subscribe every ticker in a prebuilt Kalshi all-weather catalog")
    p.add_argument("--refresh-seconds", type=float, default=300.0)
    p.add_argument("--health-interval", type=float, default=2.0)
    p.add_argument("--flush-every", type=int, default=25)
    p.add_argument("--rest-timeout", type=float, default=30.0)
    p.add_argument("--max-backoff", type=float, default=30.0)
    args = p.parse_args()
    if not args.key_id or not args.private_key_path:
        raise SystemExit("KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH are required")
    print("mode=KALSHI_WEATHER_L2_ARCHIVE read_only=true live_order_submission=false", flush=True)
    print(f"series={len([x for x in args.series.split(',') if x])} raw_dir={args.raw_dir}", flush=True)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
