#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyarrow.parquet as pq

from weather_alpha.backtest.validation import EventReturn
from weather_alpha.research.scorecard import score_event_returns


def main() -> int:
    p = argparse.ArgumentParser(description="Build the canonical economic scorecard for the frozen causal settlement strategy")
    p.add_argument("--trades", type=Path, default=Path("/data/weather/results/settlement_reconstruction/causal_trade_backtest_bulk.parquet"))
    p.add_argument("--latency-seconds", type=int, default=300)
    p.add_argument("--depth-contracts", default="5")
    p.add_argument("--price-floor", default="0")
    p.add_argument("--output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/settlement_basis_scorecard.json"))
    args = p.parse_args()

    rows = pq.read_table(args.trades).to_pylist()
    selected = [
        row for row in rows
        if row.get("status") == "EXECUTED"
        and int(row.get("latency_seconds") or -1) == args.latency_seconds
        and str(row.get("depth_contracts")) == str(args.depth_contracts)
        and str(row.get("price_floor")) == str(args.price_floor)
    ]
    returns = [
        EventReturn(
            strategy="settlement_basis_causal_v1",
            event_id=f"{row.get('station')}|{row.get('settlement_date')}|{row.get('contract_id')}",
            date=str(row.get("settlement_date") or "UNKNOWN"),
            station=str(row.get("station") or "UNKNOWN"),
            latency_seconds=args.latency_seconds,
            execution_case=f"TAKER_DEPTH_{args.depth_contracts}_FLOOR_{args.price_floor}",
            pnl=float(row.get("pnl") or 0.0),
            capital=float(row.get("capital_at_risk") or 0.0),
        )
        for row in selected
    ]
    card = score_event_returns("settlement_basis_causal_v1", returns)
    payload = card.to_dict()
    payload["source"] = str(args.trades)
    payload["latency_seconds"] = args.latency_seconds
    payload["depth_contracts"] = str(args.depth_contracts)
    payload["price_floor"] = str(args.price_floor)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
