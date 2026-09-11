from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as pq


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit Kalshi weather catalog classification reasons")
    ap.add_argument("catalog", type=Path)
    ap.add_argument("--samples", type=int, default=5)
    args = ap.parse_args()

    rows = pq.read_table(args.catalog).to_pylist()
    print(f"rows={len(rows)}")
    print(f"status_counts={dict(Counter(r.get('status') for r in rows))}")
    print(f"measurement_counts={dict(Counter(r.get('measurement') for r in rows))}")

    reasons = Counter()
    examples: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        for reason in row.get("reasons") or []:
            reasons[reason] += 1
            if len(examples[reason]) < args.samples:
                examples[reason].append(row)

    print("reason_counts=")
    for reason, count in reasons.most_common():
        print(f"  {count:4d}  {reason}")

    for reason, count in reasons.most_common():
        print(f"\n=== {reason} ({count}) ===")
        for row in examples[reason]:
            print({
                "contract_id": row.get("contract_id"),
                "event_id": row.get("event_id"),
                "status": row.get("status"),
                "measurement": row.get("measurement"),
                "station": row.get("station"),
                "settlement_date": row.get("settlement_date"),
                "shape": row.get("shape"),
                "lower": row.get("lower"),
                "upper": row.get("upper"),
                "title": row.get("title"),
                "subtitle": row.get("subtitle"),
                "settlement_source": row.get("settlement_source"),
            })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
