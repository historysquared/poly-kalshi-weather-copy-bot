from __future__ import annotations

import argparse
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


def _catalog_tickers(catalog: Path, allowed_statuses: set[str]) -> set[str]:
    table = pq.read_table(catalog, columns=["contract_id", "status"])
    rows = table.to_pylist()
    return {
        str(r["contract_id"])
        for r in rows
        if r.get("contract_id") and str(r.get("status")) in allowed_statuses
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract compact weather-only rows from a PMXT Kalshi hourly archive")
    ap.add_argument("pmxt_path", type=Path)
    ap.add_argument("catalog", type=Path)
    ap.add_argument("--output", type=Path, default=Path("/data/weather/normalized/orderbooks/kalshi_weather_pmxt.parquet"))
    ap.add_argument("--statuses", default="EXACT", help="comma-separated catalog statuses; default EXACT only")
    args = ap.parse_args()

    allowed = {x.strip().upper() for x in args.statuses.split(",") if x.strip()}
    tickers = _catalog_tickers(args.catalog, allowed)
    print(f"allowed_statuses={sorted(allowed)}")
    print(f"catalog_tickers={len(tickers)}")
    if not tickers:
        print("no eligible catalog tickers; refusing to create a misleading research dataset")
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    pf = pq.ParquetFile(args.pmxt_path)
    writer: pq.ParquetWriter | None = None
    scanned = 0
    kept = 0
    try:
        for batch in pf.iter_batches(batch_size=32768):
            scanned += batch.num_rows
            ticker_col = batch.column(batch.schema.get_field_index("market_ticker"))
            mask = pc.is_in(ticker_col, value_set=pa.array(sorted(tickers), type=pa.string()))
            selected = pa.Table.from_batches([batch]).filter(mask)
            if selected.num_rows == 0:
                continue
            kept += selected.num_rows
            if writer is None:
                writer = pq.ParquetWriter(args.output, selected.schema, compression="zstd")
            writer.write_table(selected)
    finally:
        if writer is not None:
            writer.close()

    print(f"scanned_rows={scanned}")
    print(f"kept_rows={kept}")
    print(f"output={args.output}")
    if kept == 0:
        print("eligible catalog contracts had no rows in this PMXT archive")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
