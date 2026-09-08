from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path

from weather_alpha.backtest.validation import EventReturn, rank_results, validate_strategy


def iter_trade_rows(root: Path):
    for path in sorted(root.rglob("trades.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)


def main() -> None:
    p = argparse.ArgumentParser(description="Aggregate multi-date pmxt blind-NO panel results")
    p.add_argument("--root", required=True, help="Directory containing per-date result artifacts")
    p.add_argument("--out", default="research/results/pmxt_blind_no_panel")
    p.add_argument("--min-events", type=int, default=30)
    p.add_argument("--min-dates", type=int, default=20)
    p.add_argument("--min-stations", type=int, default=3)
    args = p.parse_args()

    rows: list[EventReturn] = []
    raw_count = 0
    for item in iter_trade_rows(Path(args.root)):
        raw_count += 1
        rows.append(EventReturn(
            strategy=item["strategy"],
            event_id=item["event_id"],
            date=item["date"],
            station=item["station"],
            latency_seconds=int(item["latency_seconds"]),
            execution_case=item["execution_case"],
            pnl=float(item["pnl"]),
            capital=float(item["capital"]),
        ))

    keys = sorted({(r.strategy, r.latency_seconds, r.execution_case) for r in rows})
    results = []
    for strategy, latency, case in keys:
        subset = [
            r for r in rows
            if r.strategy == strategy
            and r.latency_seconds == latency
            and r.execution_case == case
        ]
        results.append(validate_strategy(
            subset,
            min_events=args.min_events,
            min_dates=args.min_dates,
            min_stations=args.min_stations,
        ))

    ranked = rank_results(results)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    fields = list(asdict(ranked[0]).keys()) if ranked else []
    with (out / "ranking.csv").open("w", newline="", encoding="utf-8") as handle:
        if fields:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for result in ranked:
                row = asdict(result)
                row["verdict"] = result.verdict.value
                writer.writerow(row)

    survivors = [r for r in ranked if r.verdict.value == "keep"]
    with (out / "survivors.json").open("w", encoding="utf-8") as handle:
        json.dump([asdict(r) | {"verdict": r.verdict.value} for r in survivors], handle, indent=2, sort_keys=True)

    print(f"raw_trades={raw_count} strategies={len(ranked)} keep={len(survivors)}")
    for r in ranked[:30]:
        print(
            f"{r.verdict.value:18s} {r.strategy:22s} latency={r.latency_seconds:4d}s "
            f"events={r.events:4d} dates={r.dates:3d} stations={r.stations:3d} "
            f"pnl={r.net_pnl:+.4f} roi={r.mean_event_roi:+.3%} "
            f"ci=[{r.roi_ci_low:+.3%},{r.roi_ci_high:+.3%}]"
        )


if __name__ == "__main__":
    main()
