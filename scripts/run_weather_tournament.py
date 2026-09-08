from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from weather_alpha.backtest.tournament import (
    ContractSnapshot,
    adjacent_yes_basket,
    best_yes,
    blind_no,
    late_elimination_no,
    model_no,
    summarize,
)


def f(row: dict[str, str], key: str):
    value = row.get(key, "")
    return None if value in ("", "NA", "None", None) else float(value)


def b(row: dict[str, str], key: str):
    value = row.get(key, "")
    if value in ("", "NA", "None", None):
        return None
    return str(value).lower() in {"1", "true", "yes", "y"}


def load(path: Path) -> list[ContractSnapshot]:
    out = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            out.append(ContractSnapshot(
                venue=row["venue"], event_id=row["event_id"], market_id=row["market_id"],
                timestamp_ms=int(row["timestamp_ms"]), lower_f=f(row, "lower_f"), upper_f=f(row, "upper_f"),
                yes_ask=f(row, "yes_ask"), no_ask=f(row, "no_ask"), fair_yes=f(row, "fair_yes"),
                resolved_yes=b(row, "resolved_yes"), observed_high_f=f(row, "observed_high_f"),
                minutes_to_close=f(row, "minutes_to_close"), fees_per_share=f(row, "fees_per_share") or 0.0,
                slippage_per_share=f(row, "slippage_per_share") or 0.0,
            ))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshots", type=Path, help="Normalized historical snapshot CSV")
    ap.add_argument("--out", type=Path, default=Path("research/weather_backtest_summary.csv"))
    args = ap.parse_args()

    rows = load(args.snapshots)
    trades = []
    for s in rows:
        for fn in (blind_no, model_no, best_yes, late_elimination_no):
            t = fn(s)
            if t is not None:
                trades.append(t)

    grouped = defaultdict(list)
    for s in rows:
        grouped[(s.venue, s.event_id, s.timestamp_ms)].append(s)
    for snapshots in grouped.values():
        result = adjacent_yes_basket(snapshots)
        if result is not None:
            basket_trades, _ = result
            trades.extend(basket_trades)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["strategy", "trades", "wins", "win_rate", "pnl_per_share", "average_roi"])
        for s in summarize(trades):
            writer.writerow([s.strategy, s.trades, s.wins, f"{s.win_rate:.6f}", f"{s.pnl_per_share:.6f}", f"{s.average_roi:.6f}"])
    print(f"wrote {args.out} from {len(rows):,} normalized snapshots and {len(trades):,} simulated legs")


if __name__ == "__main__":
    main()
