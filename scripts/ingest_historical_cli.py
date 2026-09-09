from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.settlement.cli_archive import IemCliArchive


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest historical NWS CLI daily records required by resolved Kalshi weather events")
    ap.add_argument("catalog", type=Path, help="resolved Kalshi weather catalog parquet")
    ap.add_argument("--output", type=Path, default=Path("/data/weather/normalized/settlements/nws_cli_daily.parquet"))
    ap.add_argument("--raw-dir", type=Path, default=Path("/data/weather/raw/nws/cli"))
    ap.add_argument("--exact-only", action=argparse.BooleanOptionalAction, default=True)
    args = ap.parse_args()

    catalog = pq.read_table(args.catalog).to_pylist()
    targets: dict[str, set[int]] = {}
    target_dates: set[tuple[str, str]] = set()
    for row in catalog:
        if args.exact_only and row.get("status") != "EXACT":
            continue
        station = str(row.get("station") or "").upper()
        day = str(row.get("settlement_date") or "")[:10]
        if not station or len(day) != 10:
            continue
        year = int(day[:4])
        targets.setdefault(station, set()).add(year)
        target_dates.add((station, day))

    args.raw_dir.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    archive = IemCliArchive()
    all_rows: list[dict] = []
    counts: Counter[str] = Counter()
    for station in sorted(targets):
        for year in sorted(targets[station]):
            records = archive.fetch_year(station, year)
            raw_path = args.raw_dir / f"{station}_{year}.jsonl"
            with raw_path.open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record.to_dict(), sort_keys=True, default=str) + "\n")
                    all_rows.append(record.to_dict())
            counts[station] += len(records)
            print(f"station={station} year={year} cli_records={len(records)} raw={raw_path}")

    # Deduplicate because multiple contracts/buckets share one physical station-day.
    unique = {(r["station"], r["valid_date"]): r for r in all_rows}
    rows = [unique[k] for k in sorted(unique)]
    pq.write_table(pa.Table.from_pylist(rows) if rows else pa.table({"station": pa.array([], type=pa.string())}), args.output)
    observed = {(r["station"], r["valid_date"]) for r in rows}
    missing = sorted(target_dates - observed)
    print(f"stations={len(targets)} rows={len(rows)} target_station_dates={len(target_dates)} missing_target_dates={len(missing)}")
    print(f"station_counts={dict(counts)}")
    if missing:
        print(f"missing_sample={missing[:25]}")
    print(f"output={args.output}")
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
