from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.settlement.cli_archive import IemCliArchive


# Keep raw CLI JSONL lossless/auditable, but make the normalized Parquet schema
# deterministic. IEM/NWS uses markers such as M for missing and T for trace;
# mixed strings/numbers in nested raw payloads otherwise make Arrow infer an
# unstable numeric type and fail when a later row contains M.

def _clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.upper() in {"", "M", "MM", "NULL", "NONE", "NAN"}:
            return None
        return text
    return value


def _normalized_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "station": str(row.get("station") or "").upper(),
        "valid_date": str(row.get("valid_date") or "")[:10],
        "high_f": row.get("high_f"),
        "low_f": row.get("low_f"),
        "precip_in": row.get("precip_in"),
        "snow_in": row.get("snow_in"),
        "high_time_lst": _clean_scalar(row.get("high_time_lst")),
        "low_time_lst": _clean_scalar(row.get("low_time_lst")),
        "source": str(row.get("source") or "NWS_CLI_VIA_IEM"),
        # Preserve the source row as JSON rather than a nested inferred Arrow
        # struct. This retains M/T/product/link/WFO metadata without allowing
        # heterogeneous source types to corrupt the normalized table schema.
        "raw_json": json.dumps(row.get("raw") or {}, sort_keys=True, default=str),
    }


def _table(rows: list[dict[str, Any]]) -> pa.Table:
    schema = pa.schema([
        ("station", pa.string()),
        ("valid_date", pa.string()),
        ("high_f", pa.float64()),
        ("low_f", pa.float64()),
        ("precip_in", pa.float64()),
        ("snow_in", pa.float64()),
        ("high_time_lst", pa.string()),
        ("low_time_lst", pa.string()),
        ("source", pa.string()),
        ("raw_json", pa.string()),
    ])
    return pa.Table.from_pylist(rows, schema=schema)


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
    all_rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for station in sorted(targets):
        for year in sorted(targets[station]):
            records = archive.fetch_year(station, year)
            raw_path = args.raw_dir / f"{station}_{year}.jsonl"
            with raw_path.open("w", encoding="utf-8") as handle:
                for record in records:
                    raw = record.to_dict()
                    # Raw archive remains lossless for settlement audits.
                    handle.write(json.dumps(raw, sort_keys=True, default=str) + "\n")
                    all_rows.append(_normalized_row(raw))
            counts[station] += len(records)
            print(f"station={station} year={year} cli_records={len(records)} raw={raw_path}")

    # Deduplicate because multiple contracts/buckets share one physical station-day.
    unique = {(r["station"], r["valid_date"]): r for r in all_rows}
    rows = [unique[k] for k in sorted(unique)]
    pq.write_table(_table(rows), args.output)
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
