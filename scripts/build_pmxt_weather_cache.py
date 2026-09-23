from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow.dataset as ds
import pyarrow.parquet as pq

from weather_alpha.backtest.pmxt import download_hour, hourly_filename
from weather_alpha.backtest.polymarket_manifest import load_manifest, search_weather_markets, write_manifest


def parse_utc_hour(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def hours(start: datetime, end: datetime):
    cur = start
    while cur < end:
        yield cur
        cur += timedelta(hours=1)


def compact_weather_hour(raw_path: Path, output_path: Path, condition_ids: set[str]) -> int:
    """Write only selected weather condition IDs from one pmxt hour.

    This uses predicate filtering on pmxt's fixed-size-binary `market` column and
    streams record batches into a new Parquet file, so the full hour does not need
    to fit in memory.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dataset = ds.dataset(str(raw_path), format="parquet")
    encoded = [x.encode("ascii") for x in sorted(condition_ids)]
    scanner = dataset.scanner(filter=ds.field("market").isin(encoded), batch_size=250_000)

    writer = None
    rows = 0
    try:
        for batch in scanner.to_batches():
            if batch.num_rows == 0:
                continue
            if writer is None:
                writer = pq.ParquetWriter(output_path, batch.schema, compression="zstd")
            writer.write_batch(batch)
            rows += batch.num_rows
    finally:
        if writer is not None:
            writer.close()

    if rows == 0 and output_path.exists():
        output_path.unlink()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a weather-only pmxt v2 cache")
    parser.add_argument("--start", required=True, help="UTC start hour, e.g. 2026-04-13T19:00Z")
    parser.add_argument("--end", required=True, help="UTC exclusive end hour")
    parser.add_argument("--root", default="data/weather/pmxt_v2")
    parser.add_argument("--manifest", default="data/weather/metadata/polymarket_weather_markets.jsonl")
    parser.add_argument("--refresh-manifest", action="store_true")
    parser.add_argument("--keep-raw", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    manifest_path = Path(args.manifest)
    if args.refresh_manifest or not manifest_path.exists():
        count = write_manifest(manifest_path, search_weather_markets())
        print(f"weather manifest: {count} markets -> {manifest_path}")

    markets = load_manifest(manifest_path)
    condition_ids = {m.condition_id for m in markets}
    print(f"condition ids: {len(condition_ids)}")

    start = parse_utc_hour(args.start)
    end = parse_utc_hour(args.end)
    raw_dir = root / "raw"
    compact_dir = root / "weather_only"

    total_rows = 0
    missing_hours = 0
    for hour in hours(start, end):
        compact_path = compact_dir / hourly_filename(hour)
        if compact_path.exists() and not args.overwrite:
            print(f"skip compact existing {hour.isoformat()}")
            continue
        try:
            raw_path = download_hour(hour, raw_dir, overwrite=args.overwrite)
        except FileNotFoundError:
            missing_hours += 1
            print(f"missing pmxt object {hour.isoformat()}")
            continue

        rows = compact_weather_hour(raw_path, compact_path, condition_ids)
        total_rows += rows
        print(f"{hour.isoformat()} weather rows={rows:,}")
        if not args.keep_raw:
            raw_path.unlink(missing_ok=True)

    print(f"done weather_rows={total_rows:,} missing_hours={missing_hours}")


if __name__ == "__main__":
    main()
