#!/usr/bin/env python3
"""Authenticated, read-only Kalshi weather L2/trade recorder plus paper-signal markouts.

No order-entry client is imported or constructed. Raw WebSocket messages are preserved
with local receive timestamps, while reconstructed books are used only for research
summaries and post-signal latency markouts.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import scripts.run_experimental_weather_company_paper as paper
from weather_alpha.live.kalshi_ws import KalshiBook, WS_URL, websocket_auth_headers


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, sort_keys=True, default=str) + "\n")


class HourlyGzipWriter:
    def __init__(self, directory: Path, prefix: str, flush_every: int = 100):
        self.directory = directory
        self.prefix = prefix
        self.flush_every = max(1, flush_every)
        self._hour: str | None = None
        self._fh = None
        self._count = 0

    def write(self, received: datetime, row: dict[str, Any]) -> None:
        hour = received.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
        if hour != self._hour:
            self.close()
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{self.prefix}_{hour}.jsonl.gz"
            self._fh = gzip.open(path, "at", encoding="utf-8")
            self._hour = hour
            self._count = 0
        assert self._fh is not None
        self._fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        self._count += 1
        if self._count % self.flush_every == 0:
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
        self._fh = None
        self._hour = None


@dataclass(slots=True)
class PendingMark:
    source: str
    ticker: str
    side: str
    signal_time: datetime
    target_time: datetime
    horizon_seconds: int
    payload: dict[str, Any]


class SignalTailer:
    def __init__(self, paths: list[tuple[str, Path]], replay_existing: bool = False):
        self.paths = paths
        self.offsets: dict[Path, int] = {}
        for _, path in paths:
            if replay_existing or not path.exists():
                self.offsets[path] = 0
            else:
                self.offsets[path] = path.stat().st_size

    def poll(self) -> list[tuple[str, dict[str, Any]]]:
        out: list[tuple[str, dict[str, Any]]] = []
        for source, path in self.paths:
            if not path.exists():
                continue
            size = path.stat().st_size
            pos = self.offsets.get(path, 0)
            if size < pos:
                pos = 0
            with path.open("r", encoding="utf-8") as fh:
                fh.seek(pos)
                for line in fh:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        out.append((source, row))
                self.offsets[path] = fh.tell()
        return out


class MarkoutManager:
    def __init__(self, output: Path, horizons: list[int]):
        self.output = output
        self.horizons = sorted(set(horizons))
        self.pending: list[PendingMark] = []
        self.completed: set[tuple[str, str, str, str, int]] = set()
        if output.exists():
            for line in output.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (
                    str(row.get("source") or ""),
                    str(row.get("ticker") or ""),
                    str(row.get("side") or ""),
                    str(row.get("signal_time") or ""),
                    int(row.get("horizon_seconds") or 0),
                )
                self.completed.add(key)

    def ingest(self, source: str, payload: dict[str, Any]) -> None:
        ticker = str(payload.get("ticker") or "")
        side = str(payload.get("side") or payload.get("tournament_side") or "").upper()
        signal_time = parse_dt(payload.get("signal_time") or payload.get("snapshot_time"))
        if not ticker or side not in {"YES", "NO"} or signal_time is None:
            return
        for horizon in self.horizons:
            key = (source, ticker, side, signal_time.isoformat(), horizon)
            if key in self.completed:
                continue
            self.pending.append(
                PendingMark(
                    source=source,
                    ticker=ticker,
                    side=side,
                    signal_time=signal_time,
                    target_time=signal_time + timedelta(seconds=horizon),
                    horizon_seconds=horizon,
                    payload=payload,
                )
            )
            self.completed.add(key)

    def emit_due(self, now: datetime, books: dict[str, KalshiBook]) -> int:
        remain: list[PendingMark] = []
        emitted = 0
        for mark in self.pending:
            if now < mark.target_time:
                remain.append(mark)
                continue
            book = books.get(mark.ticker)
            row: dict[str, Any] = {
                "source": mark.source,
                "ticker": mark.ticker,
                "side": mark.side,
                "signal_time": mark.signal_time.isoformat(),
                "target_time": mark.target_time.isoformat(),
                "observed_time": now.isoformat(),
                "mark_delay_seconds": (now - mark.target_time).total_seconds(),
                "horizon_seconds": mark.horizon_seconds,
                "signal_entry_ask": mark.payload.get("signal_entry_ask") or mark.payload.get("tournament_entry_ask") or mark.payload.get("entry_ask"),
                "signal_gross_edge": mark.payload.get("gross_edge") or mark.payload.get("tournament_gross_edge"),
                "signal_net_edge": mark.payload.get("net_edge_after_fee") or mark.payload.get("tournament_net_edge"),
                "station": mark.payload.get("station"),
                "event_id": mark.payload.get("event_id"),
                "track": mark.payload.get("track"),
                "live_order_submission": False,
            }
            if book is None:
                row["status"] = "NO_L2_BOOK"
            else:
                summary = book.summary()
                prefix = "yes" if mark.side == "YES" else "no"
                row.update(
                    {
                        "status": "BOOK_MARK",
                        "best_bid": summary.get(f"{prefix}_best_bid"),
                        "best_ask": summary.get(f"{prefix}_best_ask"),
                        "spread": summary.get(f"{prefix}_spread"),
                        "bid_depth_top5": summary.get(f"{prefix}_bid_depth_top5"),
                        "ask_depth_top5": summary.get(f"{prefix}_ask_depth_top5"),
                        "buy_vwap_1": summary.get(f"{prefix}_buy_vwap_1"),
                        "buy_filled_1": summary.get(f"{prefix}_buy_filled_1"),
                        "buy_vwap_5": summary.get(f"{prefix}_buy_vwap_5"),
                        "buy_filled_5": summary.get(f"{prefix}_buy_filled_5"),
                        "book_sequence": summary.get("sequence"),
                        "book_exchange_ts": summary.get("exchange_ts"),
                    }
                )
            append_jsonl(self.output, row)
            emitted += 1
        self.pending = remain
        return emitted


def current_weather_tickers(series: str) -> list[str]:
    series_ids = [x.strip().upper() for x in series.split(",") if x.strip()]
    return sorted({c.ticker for c in paper.fetch_contracts(series_ids)})


def state_payload(books: dict[str, KalshiBook], *, generated_at: datetime, health: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": generated_at.isoformat(),
        "health": health,
        "books": {ticker: book.summary() for ticker, book in sorted(books.items())},
    }


async def run_session(args, books: dict[str, KalshiBook], raw_writer: HourlyGzipWriter,
                      tailer: SignalTailer, markouts: MarkoutManager, generation: int) -> dict[str, Any]:
    tickers = await asyncio.to_thread(current_weather_tickers, args.series)
    if not tickers:
        raise RuntimeError("no open Weather Company weather tickers discovered")

    headers = websocket_auth_headers(
        key_id=args.key_id,
        private_key_path=args.private_key_path,
    )
    import websockets

    connection_id = str(uuid4())
    started = utcnow()
    health = {
        "connection_id": connection_id,
        "generation": generation,
        "connected_at": started.isoformat(),
        "subscribed_tickers": len(tickers),
        "messages": 0,
        "orderbook_messages": 0,
        "trade_messages": 0,
        "ticker_messages": 0,
        "last_message_at": None,
        "last_error": None,
    }

    connect_kwargs = dict(ping_interval=20, ping_timeout=20, max_queue=8192)
    try:
        ws_cm = websockets.connect(args.ws_url, additional_headers=headers, **connect_kwargs)
    except TypeError:
        ws_cm = websockets.connect(args.ws_url, extra_headers=headers, **connect_kwargs)

    async with ws_cm as ws:
        subscribe = {
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta", "trade", "ticker"],
                "market_tickers": tickers,
            },
        }
        await ws.send(json.dumps(subscribe))
        print(f"kalshi_ws connected generation={generation} tickers={len(tickers)}", flush=True)
        deadline = time.monotonic() + args.refresh_seconds
        last_state_write = 0.0

        while time.monotonic() < deadline:
            now = utcnow()
            for source, payload in tailer.poll():
                markouts.ingest(source, payload)
            markouts.emit_due(now, books)

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                if time.monotonic() - last_state_write >= args.state_interval:
                    atomic_json(args.latest_books, state_payload(books, generated_at=now, health=health))
                    last_state_write = time.monotonic()
                continue

            received = utcnow()
            health["messages"] += 1
            health["last_message_at"] = received.isoformat()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"type": "malformed", "raw_text": str(raw)}
            msg_type = str(data.get("type") or "")
            msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
            ticker = str(msg.get("market_ticker") or "")

            normalized = None
            if msg_type == "orderbook_snapshot" and ticker:
                book = books.setdefault(ticker, KalshiBook(ticker))
                book.apply_snapshot(msg, data.get("seq"))
                normalized = book.summary()
                health["orderbook_messages"] += 1
            elif msg_type == "orderbook_delta" and ticker:
                book = books.setdefault(ticker, KalshiBook(ticker))
                book.apply_delta(msg, data.get("seq"))
                normalized = book.summary()
                health["orderbook_messages"] += 1
            elif msg_type == "trade":
                health["trade_messages"] += 1
            elif msg_type == "ticker":
                health["ticker_messages"] += 1
            elif msg_type == "error":
                health["last_error"] = msg

            raw_writer.write(
                received,
                {
                    "local_receive_timestamp": received.isoformat(),
                    "connection_id": connection_id,
                    "generation": generation,
                    "message_type": msg_type,
                    "ticker": ticker or None,
                    "sequence": data.get("seq"),
                    "subscription_id": data.get("sid"),
                    "normalized_book": normalized,
                    "raw": data,
                },
            )
            if time.monotonic() - last_state_write >= args.state_interval:
                atomic_json(args.latest_books, state_payload(books, generated_at=received, health=health))
                last_state_write = time.monotonic()

    return health


async def main_async(args) -> int:
    books: dict[str, KalshiBook] = {}
    raw_writer = HourlyGzipWriter(args.raw_dir, "kalshi_weather_ws", flush_every=args.flush_every)
    tailer = SignalTailer(
        [
            ("paper", args.paper_signals),
            ("tournament", args.tournament_signals),
            ("diagnostic", args.diagnostic_signals),
        ],
        replay_existing=args.replay_existing_signals,
    )
    markouts = MarkoutManager(args.markouts_output, args.markout_horizons)
    generation = 0
    reconnects = 0
    backoff = 1.0
    try:
        while True:
            try:
                health = await run_session(args, books, raw_writer, tailer, markouts, generation)
                reconnects += 1
                generation += 1
                health["reconnects"] = reconnects
                atomic_json(args.health_output, health)
                backoff = 1.0
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                reconnects += 1
                error = {
                    "at": utcnow().isoformat(),
                    "generation": generation,
                    "reconnects": reconnects,
                    "error": f"{type(exc).__name__}:{exc}",
                }
                atomic_json(args.health_output, error)
                print(f"kalshi_ws_error {error['error']} retry_s={backoff}", flush=True)
                await asyncio.sleep(backoff)
                generation += 1
                backoff = min(args.max_backoff, backoff * 2)
    finally:
        raw_writer.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Read-only Kalshi weather L2/trade recorder and paper markout engine")
    p.add_argument("--series", default=",".join(paper.SERIES_STATION))
    p.add_argument("--ws-url", default=WS_URL)
    p.add_argument("--key-id", default=os.getenv("KALSHI_API_KEY_ID") or os.getenv("KALSHI_ACCESS_KEY"))
    p.add_argument("--private-key-path", default=os.getenv("KALSHI_PRIVATE_KEY_PATH"))
    p.add_argument("--raw-dir", type=Path, default=Path("/data/weather/live/kalshi_l2"))
    p.add_argument("--latest-books", type=Path, default=Path("/data/weather/live/kalshi_l2_latest.json"))
    p.add_argument("--health-output", type=Path, default=Path("/data/weather/live/kalshi_l2_health.json"))
    p.add_argument("--paper-signals", type=Path, default=Path("/data/weather/live/weather_company_paper_signals.jsonl"))
    p.add_argument("--tournament-signals", type=Path, default=Path("/data/weather/live/weather_company_tournament_signals.jsonl"))
    p.add_argument("--diagnostic-signals", type=Path, default=Path("/data/weather/live/weather_company_diagnostic_signals.jsonl"))
    p.add_argument("--markouts-output", type=Path, default=Path("/data/weather/live/weather_company_latency_markouts.jsonl"))
    p.add_argument("--markout-horizons", default="0,5,15,30,60,120,300")
    p.add_argument("--refresh-seconds", type=int, default=300)
    p.add_argument("--state-interval", type=float, default=2.0)
    p.add_argument("--flush-every", type=int, default=50)
    p.add_argument("--max-backoff", type=float, default=30.0)
    p.add_argument("--replay-existing-signals", action="store_true")
    args = p.parse_args()

    if not args.key_id or not args.private_key_path:
        raise SystemExit("KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH are required; no order permissions are used")
    args.markout_horizons = [int(x) for x in str(args.markout_horizons).split(",") if x.strip()]
    print("mode=KALSHI_WEATHER_L2_RECORDER read_only=true live_order_submission=false", flush=True)
    print(f"markout_horizons={args.markout_horizons} raw_dir={args.raw_dir}", flush=True)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
