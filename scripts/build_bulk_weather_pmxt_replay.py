from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.backtest.kalshi_pmxt import iter_market_snapshots, read_kalshi_parquet


def _levels(levels: tuple[tuple[Any, Any], ...]) -> list[dict[str, str]]:
    return [{"price": str(price), "size": str(size)} for price, size in levels]


def main() -> int:
    p = argparse.ArgumentParser(description="Filter bulk PMXT hourly archives to a compact EXACT-weather replay parquet")
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_bulk_resolved.parquet"))
    p.add_argument("--pmxt-dir", type=Path, default=Path("/data/weather/raw/kalshi/pmxt_orderbooks_bulk"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/normalized/orderbooks/kalshi_weather_pmxt_bulk.parquet"))
    args = p.parse_args()

    catalog = pq.read_table(args.catalog).to_pylist()
    tickers = {str(r.get("contract_id") or "") for r in catalog if r.get("status") == "EXACT" and r.get("contract_id")}
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    files = sorted(args.pmxt_dir.glob("kalshi_orderbook_*.parquet"))
    for i, path in enumerate(files, 1):
        events = [event for event in read_kalshi_parquet(path) if event.market_ticker in tickers]
        counts["raw_relevant_events"] += len(events)
        if not events:
            continue
        for book in iter_market_snapshots(events):
            rows.append({
                "contract_id": book.market_ticker,
                "market_id": book.market_id,
                "book_time": book.effective_timestamp.isoformat(),
                "received_time": book.received_timestamp.isoformat(),
                "exchange_time": book.exchange_timestamp.isoformat() if book.exchange_timestamp else None,
                "clock_source": "exchange" if book.exchange_timestamp else "received_fallback",
                "yes_bids": _levels(book.yes_bids),
                "no_bids": _levels(book.no_bids),
                "source_file": path.name,
            })
            counts["snapshots"] += 1
        if i % 25 == 0:
            print(f"files={i}/{len(files)} raw_relevant={counts['raw_relevant_events']} snapshots={counts['snapshots']}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows) if rows else pa.table({"contract_id": pa.array([], type=pa.string())})
    pq.write_table(table, args.output, compression="zstd")
    covered = {r["contract_id"] for r in rows}
    print(f"files_scanned={len(files)} exact_contracts={len(tickers)} covered_contracts={len(covered)} relevant_raw_events={counts['raw_relevant_events']} compact_snapshots={counts['snapshots']}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
