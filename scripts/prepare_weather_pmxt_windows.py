from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.backtest.kalshi_pmxt import download_kalshi_hour, iter_market_snapshots, read_kalshi_parquet
from weather_alpha.backtest.weather_pmxt_windows import derive_weather_pmxt_hours, unique_hours


def _levels(levels):
    return [{"price": str(p), "size": str(q)} for p, q in levels]


def main() -> int:
    p = argparse.ArgumentParser(description="Download PMXT hours covering exact weather settlement windows and build a compact weather-only replay")
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_resolved.parquet"))
    p.add_argument("--raw-dir", type=Path, default=Path("/data/weather/raw/kalshi/pmxt_orderbooks"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/normalized/orderbooks/kalshi_weather_pmxt_final6h.parquet"))
    p.add_argument("--final-hours", type=int, default=6)
    p.add_argument("--overwrite-downloads", action="store_true")
    args = p.parse_args()

    catalog = [r for r in pq.read_table(args.catalog).to_pylist() if r.get("status") == "EXACT"]
    tickers = {str(r.get("contract_id") or "") for r in catalog if r.get("contract_id")}
    windows = derive_weather_pmxt_hours(catalog, final_hours=args.final_hours)
    hours = unique_hours(windows)

    print(f"exact_contracts={len(tickers)} event_window_rows={len(windows)} unique_hours={len(hours)} final_hours={args.final_hours}")
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []
    missing = 0
    for idx, hour in enumerate(hours, 1):
        try:
            path = download_kalshi_hour(hour, args.raw_dir, overwrite=args.overwrite_downloads)
            downloaded.append(path)
            print(f"download={idx}/{len(hours)} hour={hour.isoformat()} path={path.name}")
        except FileNotFoundError:
            missing += 1
            print(f"archive_missing hour={hour.isoformat()}")

    rows = []
    relevant_raw_events = 0
    by_ticker = Counter()
    for path in downloaded:
        events = [e for e in read_kalshi_parquet(path) if e.market_ticker in tickers]
        relevant_raw_events += len(events)
        if not events:
            continue
        for book in iter_market_snapshots(events):
            by_ticker[book.market_ticker] += 1
            rows.append({
                "contract_id": book.market_ticker,
                "market_id": book.market_id,
                "book_time": book.effective_timestamp.isoformat(),
                "received_time": book.received_timestamp.isoformat(),
                "exchange_time": book.exchange_timestamp.isoformat() if book.exchange_timestamp else None,
                "clock_source": "exchange" if book.exchange_timestamp else "received_fallback",
                "yes_bids": _levels(book.yes_bids),
                "no_bids": _levels(book.no_bids),
            })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        pq.write_table(pa.Table.from_pylist(rows), args.output, compression="zstd")
    else:
        pq.write_table(pa.table({"contract_id": pa.array([], type=pa.string())}), args.output)

    print(f"downloaded_hours={len(downloaded)} missing_hours={missing}")
    print(f"relevant_raw_events={relevant_raw_events} compact_snapshots={len(rows)} covered_contracts={len(by_ticker)}")
    if by_ticker:
        print(f"top_snapshot_counts={by_ticker.most_common(10)}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
