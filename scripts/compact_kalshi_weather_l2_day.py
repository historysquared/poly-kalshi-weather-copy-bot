#!/usr/bin/env python3
"""Compact one UTC day of raw Kalshi weather L2 into a replay-friendly Parquet layer.

The compact layer is deliberately not a replacement for exact queue-position replay.
It preserves time-bucketed spread/depth/VWAP state plus every trade so research can
simulate executable liquidity after raw L2 rolls off.

A prior end-of-day checkpoint is required unless --bootstrap is explicit. This is
important because a UTC day can start with order-book deltas from a WebSocket
connection whose initial snapshot was received on an earlier day.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.live.kalshi_ws import KalshiBook
from weather_alpha.markets.kalshi_weather_series import ALL_DAILY_TEMPERATURE_SERIES

SCHEMA_VERSION = 1
COMPACTOR_VERSION = "kalshi-weather-compact-replay-v1"
DAILY_SERIES = frozenset(ALL_DAILY_TEMPERATURE_SERIES)

SNAPSHOT_SCHEMA = pa.schema([
    ("bucket_time", pa.timestamp("us", tz="UTC")),
    ("observed_time", pa.timestamp("us", tz="UTC")),
    ("ticker", pa.string()),
    ("series", pa.string()),
    ("cadence_seconds", pa.int16()),
    ("sequence", pa.int64()),
    ("exchange_ts", pa.string()),
    ("yes_best_bid", pa.float64()),
    ("yes_best_ask", pa.float64()),
    ("no_best_bid", pa.float64()),
    ("no_best_ask", pa.float64()),
    ("yes_spread", pa.float64()),
    ("no_spread", pa.float64()),
    ("yes_bid_depth_top5", pa.float64()),
    ("yes_ask_depth_top5", pa.float64()),
    ("no_bid_depth_top5", pa.float64()),
    ("no_ask_depth_top5", pa.float64()),
    ("yes_buy_vwap_1", pa.float64()),
    ("yes_buy_filled_1", pa.float64()),
    ("yes_buy_vwap_5", pa.float64()),
    ("yes_buy_filled_5", pa.float64()),
    ("yes_buy_vwap_25", pa.float64()),
    ("yes_buy_filled_25", pa.float64()),
    ("yes_buy_vwap_100", pa.float64()),
    ("yes_buy_filled_100", pa.float64()),
    ("no_buy_vwap_1", pa.float64()),
    ("no_buy_filled_1", pa.float64()),
    ("no_buy_vwap_5", pa.float64()),
    ("no_buy_filled_5", pa.float64()),
    ("no_buy_vwap_25", pa.float64()),
    ("no_buy_filled_25", pa.float64()),
    ("no_buy_vwap_100", pa.float64()),
    ("no_buy_filled_100", pa.float64()),
    ("yes_bid_levels_top5_json", pa.string()),
    ("yes_ask_levels_top5_json", pa.string()),
    ("no_bid_levels_top5_json", pa.string()),
    ("no_ask_levels_top5_json", pa.string()),
])

TRADE_SCHEMA = pa.schema([
    ("receive_time", pa.timestamp("us", tz="UTC")),
    ("ticker", pa.string()),
    ("sequence", pa.int64()),
    ("trade_id", pa.string()),
    ("exchange_ts_ms", pa.int64()),
    ("yes_price", pa.float64()),
    ("no_price", pa.float64()),
    ("count", pa.float64()),
    ("taker_side", pa.string()),
    ("taker_book_side", pa.string()),
    ("is_block_trade", pa.bool_()),
])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc)


def series_ticker(ticker: str) -> str:
    return str(ticker).split("-", 1)[0].upper()


def cadence_for_ticker(ticker: str, *, daily_temperature_seconds: int, other_seconds: int) -> int:
    return daily_temperature_seconds if series_ticker(ticker) in DAILY_SERIES else other_seconds


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def levels_json(value: Any) -> str:
    return json.dumps(value or [], separators=(",", ":"), sort_keys=False)


def bucket_start(observed: datetime, cadence_seconds: int) -> datetime:
    epoch = int(observed.timestamp())
    return datetime.fromtimestamp((epoch // cadence_seconds) * cadence_seconds, tz=timezone.utc)


def book_row(book: KalshiBook, observed: datetime, cadence_seconds: int) -> dict[str, Any]:
    summary = book.summary(depth_levels=5)

    def extra_vwap(outcome: str, size: int) -> tuple[float | None, float]:
        vwap, filled = book.executable_buy_vwap(outcome, size)
        return as_float(vwap), float(filled)

    y25, yf25 = extra_vwap("YES", 25)
    y100, yf100 = extra_vwap("YES", 100)
    n25, nf25 = extra_vwap("NO", 25)
    n100, nf100 = extra_vwap("NO", 100)
    return {
        "bucket_time": bucket_start(observed, cadence_seconds),
        "observed_time": observed,
        "ticker": book.ticker,
        "series": series_ticker(book.ticker),
        "cadence_seconds": cadence_seconds,
        "sequence": as_int(summary.get("sequence")),
        "exchange_ts": summary.get("exchange_ts"),
        "yes_best_bid": as_float(summary.get("yes_best_bid")),
        "yes_best_ask": as_float(summary.get("yes_best_ask")),
        "no_best_bid": as_float(summary.get("no_best_bid")),
        "no_best_ask": as_float(summary.get("no_best_ask")),
        "yes_spread": as_float(summary.get("yes_spread")),
        "no_spread": as_float(summary.get("no_spread")),
        "yes_bid_depth_top5": as_float(summary.get("yes_bid_depth_top5")),
        "yes_ask_depth_top5": as_float(summary.get("yes_ask_depth_top5")),
        "no_bid_depth_top5": as_float(summary.get("no_bid_depth_top5")),
        "no_ask_depth_top5": as_float(summary.get("no_ask_depth_top5")),
        "yes_buy_vwap_1": as_float(summary.get("yes_buy_vwap_1")),
        "yes_buy_filled_1": as_float(summary.get("yes_buy_filled_1")),
        "yes_buy_vwap_5": as_float(summary.get("yes_buy_vwap_5")),
        "yes_buy_filled_5": as_float(summary.get("yes_buy_filled_5")),
        "yes_buy_vwap_25": y25,
        "yes_buy_filled_25": yf25,
        "yes_buy_vwap_100": y100,
        "yes_buy_filled_100": yf100,
        "no_buy_vwap_1": as_float(summary.get("no_buy_vwap_1")),
        "no_buy_filled_1": as_float(summary.get("no_buy_filled_1")),
        "no_buy_vwap_5": as_float(summary.get("no_buy_vwap_5")),
        "no_buy_filled_5": as_float(summary.get("no_buy_filled_5")),
        "no_buy_vwap_25": n25,
        "no_buy_filled_25": nf25,
        "no_buy_vwap_100": n100,
        "no_buy_filled_100": nf100,
        "yes_bid_levels_top5_json": levels_json(summary.get("yes_bid_levels_top5")),
        "yes_ask_levels_top5_json": levels_json(summary.get("yes_ask_levels_top5")),
        "no_bid_levels_top5_json": levels_json(summary.get("no_bid_levels_top5")),
        "no_ask_levels_top5_json": levels_json(summary.get("no_ask_levels_top5")),
    }


def trade_row(raw_row: dict[str, Any]) -> dict[str, Any] | None:
    data = raw_row.get("raw") if isinstance(raw_row.get("raw"), dict) else {}
    msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
    ticker = str(msg.get("market_ticker") or raw_row.get("ticker") or "")
    if not ticker:
        return None
    return {
        "receive_time": parse_dt(raw_row["local_receive_timestamp"]),
        "ticker": ticker,
        "sequence": as_int(raw_row.get("sequence") or data.get("seq")),
        "trade_id": str(msg.get("trade_id") or ""),
        "exchange_ts_ms": as_int(msg.get("ts_ms")),
        "yes_price": as_float(msg.get("yes_price_dollars")),
        "no_price": as_float(msg.get("no_price_dollars")),
        "count": as_float(msg.get("count_fp") or msg.get("count")),
        "taker_side": str(msg.get("taker_side") or msg.get("taker_outcome_side") or ""),
        "taker_book_side": str(msg.get("taker_book_side") or ""),
        "is_block_trade": bool(msg.get("is_block_trade", False)),
    }


class BatchParquetWriter:
    def __init__(self, path: Path, schema: pa.Schema, batch_size: int = 50_000) -> None:
        self.path = path
        self.schema = schema
        self.batch_size = max(1, int(batch_size))
        self.rows: list[dict[str, Any]] = []
        self.writer: pq.ParquetWriter | None = None
        self.count = 0

    def append(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        self.count += 1
        if len(self.rows) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(self.rows, schema=self.schema)
        if self.writer is None:
            self.writer = pq.ParquetWriter(
                self.path, self.schema, compression="zstd", compression_level=6,
                use_dictionary=True, write_statistics=True,
            )
        self.writer.write_table(table)
        self.rows.clear()

    def close(self) -> None:
        self.flush()
        if self.writer is not None:
            self.writer.close()
            self.writer = None
        elif not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist([], schema=self.schema), self.path, compression="zstd")


def checkpoint_payload(books: dict[str, KalshiBook], *, through_date: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ticker, book in sorted(books.items()):
        out[ticker] = {
            "yes_bids": [[str(p), str(q)] for p, q in sorted(book.yes_bids.items())],
            "no_bids": [[str(p), str(q)] for p, q in sorted(book.no_bids.items())],
            "sequence": book.sequence,
            "last_exchange_ts": book.last_exchange_ts,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "compactor_version": COMPACTOR_VERSION,
        "through_date": through_date,
        "generated_at": utcnow().isoformat(),
        "books": out,
    }


def save_checkpoint(path: Path, books: dict[str, KalshiBook], *, through_date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
        json.dump(checkpoint_payload(books, through_date=through_date), fh, separators=(",", ":"), sort_keys=True)
    tmp.replace(path)


def load_checkpoint(path: Path) -> dict[str, KalshiBook]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    books: dict[str, KalshiBook] = {}
    for ticker, row in (payload.get("books") or {}).items():
        if not isinstance(row, dict):
            continue
        book = KalshiBook(str(ticker))
        book.yes_bids = {Decimal(str(p)): Decimal(str(q)) for p, q in row.get("yes_bids") or []}
        book.no_bids = {Decimal(str(p)): Decimal(str(q)) for p, q in row.get("no_bids") or []}
        book.sequence = as_int(row.get("sequence"))
        book.last_exchange_ts = row.get("last_exchange_ts")
        books[book.ticker] = book
    return books


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def source_files_for_day(raw_root: Path, day: date) -> list[Path]:
    root = raw_root / day.strftime("%Y/%m/%d")
    return sorted(root.glob(f"kalshi_weather_l2_{day.isoformat()}T*.jsonl.gz"))


def expected_hours(files: Iterable[Path]) -> set[int]:
    hours: set[int] = set()
    for path in files:
        try:
            hours.add(int(path.name.rsplit("T", 1)[1].split(".", 1)[0]))
        except (IndexError, ValueError):
            continue
    return hours


@dataclass
class CompactStats:
    raw_rows: int = 0
    orderbook_snapshots: int = 0
    orderbook_deltas: int = 0
    trades: int = 0
    ticker_messages: int = 0
    delta_without_state: int = 0
    malformed_rows: int = 0
    crossed_states: int = 0


def crossed(row: dict[str, Any]) -> bool:
    for side in ("yes", "no"):
        bid = row.get(f"{side}_best_bid")
        ask = row.get(f"{side}_best_ask")
        if bid is not None and ask is not None and float(bid) > float(ask) + 1e-8:
            return True
    return False


def compact_files(
    raw_files: list[Path],
    *,
    snapshots_path: Path,
    trades_path: Path,
    checkpoint_path: Path,
    checkpoint_in: Path | None,
    bootstrap: bool,
    through_date: str,
    daily_temperature_seconds: int = 10,
    other_seconds: int = 60,
    batch_size: int = 50_000,
) -> tuple[CompactStats, int, int, int]:
    if checkpoint_in is not None:
        books = load_checkpoint(checkpoint_in)
    elif bootstrap:
        books = {}
    else:
        raise ValueError("checkpoint_in is required unless bootstrap=True")

    snapshots = BatchParquetWriter(snapshots_path, SNAPSHOT_SCHEMA, batch_size=batch_size)
    trades = BatchParquetWriter(trades_path, TRADE_SCHEMA, batch_size=batch_size)
    # ticker -> (bucket epoch, observed time of last update in that bucket)
    pending: dict[str, tuple[int, datetime]] = {}
    stats = CompactStats()

    def emit_pending(ticker: str, observed: datetime, cadence: int) -> None:
        book = books.get(ticker)
        if book is None:
            return
        compact = book_row(book, observed, cadence)
        if crossed(compact):
            stats.crossed_states += 1
        snapshots.append(compact)

    try:
        for raw_path in raw_files:
            with gzip.open(raw_path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    stats.raw_rows += 1
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        stats.malformed_rows += 1
                        continue
                    msg_type = str(row.get("message_type") or "")
                    ticker = str(row.get("ticker") or "")
                    data = row.get("raw") if isinstance(row.get("raw"), dict) else {}
                    msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}

                    if msg_type == "trade":
                        trade = trade_row(row)
                        if trade is not None:
                            trades.append(trade)
                            stats.trades += 1
                        continue
                    if msg_type == "ticker":
                        stats.ticker_messages += 1
                        continue
                    if msg_type not in {"orderbook_snapshot", "orderbook_delta"} or not ticker:
                        continue

                    observed = parse_dt(row["local_receive_timestamp"])
                    cadence = cadence_for_ticker(
                        ticker,
                        daily_temperature_seconds=daily_temperature_seconds,
                        other_seconds=other_seconds,
                    )
                    current_bucket = int(bucket_start(observed, cadence).timestamp())
                    previous = pending.get(ticker)
                    # Flush the prior bucket BEFORE applying the first mutation from
                    # the new bucket, so the emitted state is truly end-of-bucket.
                    if previous is not None and previous[0] != current_bucket:
                        emit_pending(ticker, previous[1], cadence)

                    if msg_type == "orderbook_snapshot":
                        book = books.setdefault(ticker, KalshiBook(ticker))
                        book.apply_snapshot(msg, row.get("sequence"))
                        stats.orderbook_snapshots += 1
                    else:
                        book = books.get(ticker)
                        if book is None:
                            stats.delta_without_state += 1
                            continue
                        book.apply_delta(msg, row.get("sequence"))
                        stats.orderbook_deltas += 1
                    pending[ticker] = (current_bucket, observed)

        for ticker in sorted(pending):
            cadence = cadence_for_ticker(
                ticker,
                daily_temperature_seconds=daily_temperature_seconds,
                other_seconds=other_seconds,
            )
            emit_pending(ticker, pending[ticker][1], cadence)
    finally:
        snapshots.close()
        trades.close()

    save_checkpoint(checkpoint_path, books, through_date=through_date)
    return stats, snapshots.count, trades.count, len(books)


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    p = argparse.ArgumentParser(description="Compact a UTC day of Kalshi all-weather L2")
    p.add_argument("--date", required=True, help="UTC date YYYY-MM-DD")
    p.add_argument("--raw-root", type=Path, default=Path("/data/weather/raw/kalshi_l2"))
    p.add_argument("--output-root", type=Path, default=Path("/data/weather/compact/kalshi_l2"))
    p.add_argument("--checkpoint-in", type=Path)
    p.add_argument("--bootstrap", action="store_true", help="Start with no prior book state; intended only for the first archive day")
    p.add_argument("--allow-partial-day", action="store_true", help="Testing only; partial output cannot become retention-eligible")
    p.add_argument("--max-files", type=int, help="Testing only; implies a partial day unless all 24 files are selected")
    p.add_argument("--daily-temperature-seconds", type=int, default=10)
    p.add_argument("--other-seconds", type=int, default=60)
    p.add_argument("--batch-size", type=int, default=50_000)
    args = p.parse_args()

    day = date.fromisoformat(args.date)
    files = source_files_for_day(args.raw_root, day)
    if args.max_files:
        files = files[: max(0, args.max_files)]
    hours = expected_hours(files)
    complete_day = hours == set(range(24))
    if not files:
        raise SystemExit(f"no raw L2 files found for {day}")
    if not complete_day and not args.allow_partial_day:
        raise SystemExit(f"refusing incomplete day: hours={sorted(hours)}")
    if args.bootstrap and args.checkpoint_in:
        raise SystemExit("choose either --bootstrap or --checkpoint-in, not both")
    if not args.bootstrap and not args.checkpoint_in:
        raise SystemExit("--checkpoint-in is required unless --bootstrap is explicit")
    if args.checkpoint_in and not args.checkpoint_in.exists():
        raise SystemExit(f"checkpoint not found: {args.checkpoint_in}")

    out = args.output_root / day.strftime("%Y/%m/%d")
    out.mkdir(parents=True, exist_ok=True)
    snapshots_path = out / "book_replay.parquet"
    trades_path = out / "trades.parquet"
    checkpoint_path = out / "end_checkpoint.json.gz"
    manifest_path = out / "manifest.json"
    for target in (snapshots_path, trades_path, checkpoint_path):
        if target.exists():
            raise SystemExit(f"refusing overwrite: {target}")

    source_meta = [
        {"path": str(x), "bytes": x.stat().st_size, "sha256": sha256_file(x)}
        for x in files
    ]
    checkpoint_in_meta = None
    if args.checkpoint_in:
        checkpoint_in_meta = {
            "path": str(args.checkpoint_in),
            "bytes": args.checkpoint_in.stat().st_size,
            "sha256": sha256_file(args.checkpoint_in),
        }

    stats, snapshot_rows, trade_rows, book_count = compact_files(
        files,
        snapshots_path=snapshots_path,
        trades_path=trades_path,
        checkpoint_path=checkpoint_path,
        checkpoint_in=args.checkpoint_in,
        bootstrap=args.bootstrap,
        through_date=day.isoformat(),
        daily_temperature_seconds=args.daily_temperature_seconds,
        other_seconds=args.other_seconds,
        batch_size=args.batch_size,
    )
    outputs = {}
    for key, target in {
        "book_replay": snapshots_path,
        "trades": trades_path,
        "end_checkpoint": checkpoint_path,
    }.items():
        outputs[key] = {"path": str(target), "bytes": target.stat().st_size, "sha256": sha256_file(target)}

    status = "COMPACTED_UNVALIDATED" if complete_day else "PARTIAL_TEST_ONLY"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "compactor_version": COMPACTOR_VERSION,
        "date": day.isoformat(),
        "generated_at": utcnow().isoformat(),
        "status": status,
        "complete_day": complete_day,
        "source_hours": sorted(hours),
        "source_files": source_meta,
        "checkpoint_in": checkpoint_in_meta,
        "bootstrap": bool(args.bootstrap),
        "daily_temperature_seconds": args.daily_temperature_seconds,
        "other_seconds": args.other_seconds,
        "raw_rows": stats.raw_rows,
        "orderbook_snapshots": stats.orderbook_snapshots,
        "orderbook_deltas": stats.orderbook_deltas,
        "trade_rows": trade_rows,
        "ticker_messages": stats.ticker_messages,
        "delta_without_state": stats.delta_without_state,
        "malformed_rows": stats.malformed_rows,
        "crossed_states": stats.crossed_states,
        "snapshot_rows": snapshot_rows,
        "end_book_count": book_count,
        "outputs": outputs,
        "retention_eligible": False,
    }
    write_manifest(manifest_path, manifest)
    print(json.dumps({
        "date": day.isoformat(), "status": status, "raw_rows": stats.raw_rows,
        "snapshot_rows": snapshot_rows, "trade_rows": trade_rows, "books": book_count,
        "delta_without_state": stats.delta_without_state, "crossed_states": stats.crossed_states,
        "output_bytes": sum(x["bytes"] for x in outputs.values()),
        "manifest": str(manifest_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
