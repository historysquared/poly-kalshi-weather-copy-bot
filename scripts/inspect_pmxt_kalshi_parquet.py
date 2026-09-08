#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyarrow.parquet as pq


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a real PMXT Kalshi Parquet partition before implementing raw replay")
    parser.add_argument("path", type=Path)
    parser.add_argument("--rows", type=int, default=5)
    args = parser.parse_args()

    pf = pq.ParquetFile(args.path)
    print("schema:")
    print(pf.schema_arrow)
    print(f"row_groups={pf.num_row_groups}")
    print(f"rows={pf.metadata.num_rows}")

    if pf.num_row_groups == 0 or args.rows <= 0:
        return
    batch = next(pf.iter_batches(batch_size=args.rows))
    for i, row in enumerate(batch.to_pylist()):
        print(json.dumps({"row": i, "data": row}, default=str, sort_keys=True))


if __name__ == "__main__":
    main()
