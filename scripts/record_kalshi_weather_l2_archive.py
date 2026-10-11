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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import websockets

from weather_alpha.live.kalshi_ws import KalshiBook, WS_URL, websocket_auth_headers
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


def load_all_weather_catalog_series_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return sum(1 for series in payload.get("active_series") or [] if isinstance(series, dict))


def save_ticker_cache(path: Path, tickers: set[str] | list[str]) -> None:
    atomic_json(path, {"generated_at": utcnow().isoformat(), "collector_version": COLLECTOR_VERSION,
                       "tickers": sorted(set(tickers))})


def _complement_price(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return format(Decimal("1") - Decimal(str(value)), "f")
    except (InvalidOperation, ValueError):
        return None


def book_quote(book: KalshiBook, received: datetime) -> dict[str, Any]:
    summary = book.summary()
    def first_size(key: str) -> str | None:
        levels = summary.get(key) or []
        return str(levels[0][1]) if levels else None

    return {
        "ticker": book.ticker,
        "exchange_time": summary.get("exchange_ts"),
        "local_receive_timestamp": received.isoformat(),
        "sequence": summary.get("sequence"),
        "yes_bid": summary.get("yes_best_bid"),
        "yes_ask": summary.get("yes_best_ask"),
        "no_bid": summary.get("no_best_bid"),
        "no_ask": summary.get("no_best_ask"),
        "yes_bid_size": first_size("yes_bid_levels_top5"),
        "yes_ask_size": first_size("yes_ask_levels_top5"),
        "no_bid_size": first_size("no_bid_levels_top5"),
        "no_ask_size": first_size("no_ask_levels_top5"),
        "yes_spread": summary.get("yes_spread"),
        "no_spread": summary.get("no_spread"),
        "yes_bid_depth_top5": summary.get("yes_bid_depth_top5"),
        "yes_ask_depth_top5": summary.get("yes_ask_depth_top5"),
        "no_bid_depth_top5": summary.get("no_bid_depth_top5"),
        "no_ask_depth_top5": summary.get("no_ask_depth_top5"),
        # Full level arrays remain in raw L2; this current-state file stays compact.
    }


def ticker_quote(msg: dict[str, Any], received: datetime) -> dict[str, Any] | None:
    ticker = str(msg.get("market_ticker") or "")
    if not ticker:
        return None
    yes_bid = msg.get("yes_bid_dollars")
    yes_ask = msg.get("yes_ask_dollars")
    yes_bid_size = msg.get("yes_bid_size_fp")
    yes_ask_size = msg.get("yes_ask_size_fp")
    return {
        "ticker": ticker,
        "exchange_time": msg.get("time"),
        "exchange_ts_ms": msg.get("ts_ms"),
        "local_receive_timestamp": received.isoformat(),
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": _complement_price(yes_ask),
        "no_ask": _complement_price(yes_bid),
        "yes_bid_size": yes_bid_size,
        "yes_ask_size": yes_ask_size,
        "no_bid_size": yes_ask_size,
        "no_ask_size": yes_bid_size,
        "last_trade_price": msg.get("price_dollars"),
        "last_trade_size": msg.get("last_trade_size_fp"),
        "volume": msg.get("volume_fp"),
        "open_interest": msg.get("open_interest_fp"),
    }


def bbo_payload(quotes: dict[str, dict[str, Any]], *, generated_at: datetime,
                health: dict[str, Any]) -> dict[str, Any]:
    daily_series = set(ALL_DAILY_TEMPERATURE_SERIES)
    daily_quotes = {
        ticker: quote
        for ticker, quote in quotes.items()
        if ticker.split("-", 1)[0].upper() in daily_series
    }
    return {
        "schema_version": 1,
        "collector_version": COLLECTOR_VERSION,
        "scope": "DAILY_TEMPERATURE_BBO_FROM_ALL_WEATHER_WS",
        "generated_at": generated_at.isoformat(),
        "all_weather_tickers": health.get("current_tickers"),
        "all_weather_series_count": health.get("series_count"),
        "quote_count": len(daily_quotes),
        "quotes": daily_quotes,
    }


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


def update_live_book_state(
    data: dict[str, Any],
    received: datetime,
    books: dict[str, KalshiBook],
    bbo_quotes: dict[str, dict[str, Any]],
) -> str | None:
    msg_type = str(data.get("type") or "")
    msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
    ticker = str(msg.get("market_ticker") or "") or None
    if msg_type == "orderbook_snapshot" and ticker:
        book = books.setdefault(ticker, KalshiBook(ticker))
        book.apply_snapshot(msg, data.get("seq"))
        previous = bbo_quotes.get(ticker) or {}
        quote = book_quote(book, received)
        for key in ("last_trade_price", "last_trade_size", "volume", "open_interest"):
            if previous.get(key) is not None:
                quote[key] = previous[key]
        bbo_quotes[ticker] = quote
    elif msg_type == "orderbook_delta" and ticker:
        book = books.setdefault(ticker, KalshiBook(ticker))
        book.apply_delta(msg, data.get("seq"))
        previous = bbo_quotes.get(ticker) or {}
        quote = book_quote(book, received)
        for key in ("last_trade_price", "last_trade_size", "volume", "open_interest"):
            if previous.get(key) is not None:
                quote[key] = previous[key]
        bbo_quotes[ticker] = quote
    elif msg_type == "ticker":
        quote = ticker_quote(msg, received)
        if quote is not None:
            existing = bbo_quotes.get(quote["ticker"])
            if existing is None:
                bbo_quotes[quote["ticker"]] = quote
            else:
                existing["ticker_exchange_time"] = quote.get("exchange_time")
                existing["ticker_receive_time"] = quote.get("local_receive_timestamp")
                for key in ("last_trade_price", "last_trade_size", "volume", "open_interest"):
                    existing[key] = quote.get(key)
    return ticker


async def run_connection(args, series: list[str], writer: HourlyWriter,
                         health: dict[str, Any], books: dict[str, KalshiBook],
                         bbo_quotes: dict[str, dict[str, Any]], generation: int) -> None:
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
    if args.all_weather_catalog:
        health["series_count"] = load_all_weather_catalog_series_count(args.all_weather_catalog)
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
        last_bbo_write = 0.0
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
                if args.all_weather_catalog:
                    health["series_count"] = load_all_weather_catalog_series_count(args.all_weather_catalog)
                for ticker in removed:
                    bbo_quotes.pop(ticker, None)
                    books.pop(ticker, None)
                save_ticker_cache(args.ticker_cache, subscribed)
                next_refresh = time.monotonic() + args.refresh_seconds

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                now = utcnow()
                if time.monotonic() - last_health >= args.health_interval:
                    health["generated_at"] = now.isoformat()
                    health["bbo_quote_count"] = len(bbo_quotes)
                    atomic_json(args.health_output, health)
                    last_health = time.monotonic()
                if time.monotonic() - last_bbo_write >= args.bbo_interval:
                    atomic_json(args.bbo_output, bbo_payload(bbo_quotes, generated_at=now, health=health))
                    last_bbo_write = time.monotonic()
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

            ticker = update_live_book_state(data, received, books, bbo_quotes)
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
                health["bbo_quote_count"] = len(bbo_quotes)
                atomic_json(args.health_output, health)
                last_health = time.monotonic()
            if time.monotonic() - last_bbo_write >= args.bbo_interval:
                atomic_json(args.bbo_output, bbo_payload(bbo_quotes, generated_at=received, health=health))
                last_bbo_write = time.monotonic()


async def main_async(args) -> int:
    series = [x.strip().upper() for x in args.series.split(",") if x.strip()]
    health = health_template(series)
    health["scope"] = "ALL_WEATHER_CATALOG" if args.all_weather_catalog else "CONFIGURED_SERIES"
    health["catalog_path"] = str(args.all_weather_catalog) if args.all_weather_catalog else None
    writer = HourlyWriter(args.raw_dir, args.flush_every)
    books: dict[str, KalshiBook] = {}
    bbo_quotes: dict[str, dict[str, Any]] = {}
    generation = 0
    backoff = 1.0
    try:
        while True:
            try:
                await run_connection(args, series, writer, health, books, bbo_quotes, generation)
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
    p.add_argument("--bbo-output", type=Path, default=Path("/data/weather/status/kalshi_all_weather_bbo.json"))
    p.add_argument("--bbo-interval", type=float, default=5.0)
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
