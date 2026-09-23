from __future__ import annotations

import argparse
import math
import random
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def D(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def safe_div(num: Decimal, den: Decimal) -> Decimal | None:
    return None if den == 0 else num / den


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1 - w) + xs[hi] * w


def contiguous_blocks(dates: list[str], n_blocks: int) -> list[list[str]]:
    if not dates:
        return []
    n_blocks = max(1, min(n_blocks, len(dates)))
    out: list[list[str]] = []
    for i in range(n_blocks):
        start = (len(dates) * i) // n_blocks
        end = (len(dates) * (i + 1)) // n_blocks
        if start < end:
            out.append(dates[start:end])
    return out


def aggregate_rows(rows: Iterable[dict[str, Any]]) -> tuple[Decimal, Decimal, int, int]:
    pnl = Decimal("0")
    capital = Decimal("0")
    wins = 0
    n = 0
    for row in rows:
        pnl += D(row.get("pnl"))
        capital += D(row.get("capital_at_risk"))
        wins += int(bool(row.get("won")))
        n += 1
    return pnl, capital, wins, n


def bootstrap_dates(
    date_rows: dict[str, tuple[Decimal, Decimal]],
    *,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    dates = sorted(date_rows)
    if not dates:
        return {
            "bootstrap_iterations": iterations,
            "bootstrap_pnl_ci_low": None,
            "bootstrap_pnl_ci_high": None,
            "bootstrap_roi_ci_low": None,
            "bootstrap_roi_ci_high": None,
            "bootstrap_prob_pnl_le_zero": None,
            "bootstrap_prob_roi_le_zero": None,
        }
    rng = random.Random(seed)
    pnl_samples: list[float] = []
    roi_samples: list[float] = []
    pnl_le_zero = 0
    roi_le_zero = 0
    for _ in range(iterations):
        pnl = Decimal("0")
        capital = Decimal("0")
        for _j in dates:
            sampled = rng.choice(dates)
            dpnl, dcap = date_rows[sampled]
            pnl += dpnl
            capital += dcap
        roi = safe_div(pnl, capital)
        pnl_f = float(pnl)
        pnl_samples.append(pnl_f)
        pnl_le_zero += int(pnl <= 0)
        if roi is not None:
            roi_f = float(roi)
            roi_samples.append(roi_f)
            roi_le_zero += int(roi <= 0)
    return {
        "bootstrap_iterations": iterations,
        "bootstrap_pnl_ci_low": percentile(pnl_samples, 0.025),
        "bootstrap_pnl_ci_high": percentile(pnl_samples, 0.975),
        "bootstrap_roi_ci_low": percentile(roi_samples, 0.025),
        "bootstrap_roi_ci_high": percentile(roi_samples, 0.975),
        "bootstrap_prob_pnl_le_zero": pnl_le_zero / iterations,
        "bootstrap_prob_roi_le_zero": None if not roi_samples else roi_le_zero / len(roi_samples),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Whole-date robustness validation for causal Kalshi weather trades")
    p.add_argument("--trades", type=Path, default=Path("/data/weather/results/settlement_reconstruction/causal_trade_backtest_bulk.parquet"))
    p.add_argument("--summary-output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/date_block_robustness_summary.parquet"))
    p.add_argument("--daily-output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/date_block_daily.parquet"))
    p.add_argument("--blocks-output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/date_block_chronological.parquet"))
    p.add_argument("--loo-output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/date_block_leave_one_out.parquet"))
    p.add_argument("--iterations", type=int, default=10000)
    p.add_argument("--seed", type=int, default=20260910)
    p.add_argument("--blocks", type=int, default=3)
    p.add_argument("--minimum-dates", type=int, default=20)
    args = p.parse_args()

    raw = pq.read_table(args.trades).to_pylist()
    trades = [r for r in raw if r.get("status") == "EXECUTED"]
    config_keys = sorted({
        (int(r["latency_seconds"]), str(r["depth_contracts"]), str(r["price_floor"]))
        for r in trades
    })

    daily_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    loo_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for config_index, (latency, depth, floor) in enumerate(config_keys):
        subset = [
            r for r in trades
            if int(r["latency_seconds"]) == latency
            and str(r["depth_contracts"]) == depth
            and str(r["price_floor"]) == floor
        ]
        by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in subset:
            by_date[str(row["settlement_date"])].append(row)
        dates = sorted(by_date)

        date_aggs: dict[str, tuple[Decimal, Decimal]] = {}
        positive_dates = 0
        for day in dates:
            pnl, cap, wins, n = aggregate_rows(by_date[day])
            date_aggs[day] = (pnl, cap)
            positive_dates += int(pnl > 0)
            daily_rows.append({
                "latency_seconds": latency,
                "depth_contracts": depth,
                "price_floor": floor,
                "settlement_date": day,
                "trades": n,
                "wins": wins,
                "losses": n - wins,
                "net_pnl": str(pnl),
                "capital_at_risk": str(cap),
                "roi": None if cap == 0 else str(pnl / cap),
            })

        total_pnl, total_cap, total_wins, total_n = aggregate_rows(subset)
        observed_roi = safe_div(total_pnl, total_cap)

        blocks = contiguous_blocks(dates, args.blocks)
        block_pnls: list[Decimal] = []
        for idx, block_dates in enumerate(blocks, 1):
            rows = [r for d in block_dates for r in by_date[d]]
            pnl, cap, wins, n = aggregate_rows(rows)
            block_pnls.append(pnl)
            block_rows.append({
                "latency_seconds": latency,
                "depth_contracts": depth,
                "price_floor": floor,
                "block_index": idx,
                "start_date": block_dates[0],
                "end_date": block_dates[-1],
                "settlement_dates": len(block_dates),
                "trades": n,
                "wins": wins,
                "losses": n - wins,
                "net_pnl": str(pnl),
                "capital_at_risk": str(cap),
                "roi": None if cap == 0 else str(pnl / cap),
            })

        loo_pnls: list[Decimal] = []
        loo_rois: list[Decimal] = []
        for omitted in dates:
            pnl = total_pnl - date_aggs[omitted][0]
            cap = total_cap - date_aggs[omitted][1]
            roi = safe_div(pnl, cap)
            loo_pnls.append(pnl)
            if roi is not None:
                loo_rois.append(roi)
            loo_rows.append({
                "latency_seconds": latency,
                "depth_contracts": depth,
                "price_floor": floor,
                "omitted_date": omitted,
                "net_pnl": str(pnl),
                "capital_at_risk": str(cap),
                "roi": None if roi is None else str(roi),
            })

        positive_profit = sorted((p for p, _ in date_aggs.values() if p > 0), reverse=True)
        total_positive_profit = sum(positive_profit, Decimal("0"))
        top1_concentration = None if total_positive_profit == 0 else positive_profit[0] / total_positive_profit
        top3_concentration = None if total_positive_profit == 0 else sum(positive_profit[:3], Decimal("0")) / total_positive_profit
        worst_day_pnl = min((p for p, _ in date_aggs.values()), default=Decimal("0"))
        best_day_pnl = max((p for p, _ in date_aggs.values()), default=Decimal("0"))

        boot = bootstrap_dates(
            date_aggs,
            iterations=args.iterations,
            seed=args.seed + config_index,
        )
        enough_dates = len(dates) >= args.minimum_dates
        all_blocks_positive = bool(block_pnls) and all(x > 0 for x in block_pnls)
        loo_all_positive = bool(loo_pnls) and min(loo_pnls) > 0
        bootstrap_positive = (
            boot["bootstrap_pnl_ci_low"] is not None
            and boot["bootstrap_pnl_ci_low"] > 0
            and boot["bootstrap_prob_pnl_le_zero"] is not None
            and boot["bootstrap_prob_pnl_le_zero"] <= 0.05
        )
        if not enough_dates:
            verdict = "INSUFFICIENT_DATES"
        elif bootstrap_positive and all_blocks_positive and loo_all_positive:
            verdict = "ROBUST_PASS"
        elif total_pnl > 0:
            verdict = "POSITIVE_BUT_FRAGILE"
        else:
            verdict = "FAIL"

        summary_rows.append({
            "latency_seconds": latency,
            "depth_contracts": depth,
            "price_floor": floor,
            "executed_trades": total_n,
            "wins": total_wins,
            "losses": total_n - total_wins,
            "win_rate": None if total_n == 0 else total_wins / total_n,
            "net_pnl": str(total_pnl),
            "capital_at_risk": str(total_cap),
            "roi": None if observed_roi is None else str(observed_roi),
            "settlement_dates": len(dates),
            "positive_dates": positive_dates,
            "positive_date_rate": None if not dates else positive_dates / len(dates),
            "worst_day_pnl": str(worst_day_pnl),
            "best_day_pnl": str(best_day_pnl),
            "top1_positive_profit_concentration": None if top1_concentration is None else str(top1_concentration),
            "top3_positive_profit_concentration": None if top3_concentration is None else str(top3_concentration),
            "chronological_blocks": len(blocks),
            "all_chronological_blocks_positive": all_blocks_positive,
            "leave_one_date_out_min_pnl": None if not loo_pnls else str(min(loo_pnls)),
            "leave_one_date_out_min_roi": None if not loo_rois else str(min(loo_rois)),
            "leave_one_date_out_all_positive": loo_all_positive,
            **boot,
            "minimum_dates_required": args.minimum_dates,
            "robustness_verdict": verdict,
            "method": "WHOLE_SETTLEMENT_DATE_RESAMPLING_NO_TRADE_LEVEL_BOOTSTRAP",
        })

    for path, rows in [
        (args.summary_output, summary_rows),
        (args.daily_output, daily_rows),
        (args.blocks_output, block_rows),
        (args.loo_output, loo_rows),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(rows) if rows else pa.table({"empty": pa.array([], type=pa.string())})
        pq.write_table(table, path, compression="zstd")

    print(f"executed_trades={len(trades)} configurations={len(summary_rows)}")
    print("bootstrap_unit=WHOLE_SETTLEMENT_DATE")
    print("chronological_blocks=CONTIGUOUS_DATES_NO_PARAMETER_SELECTION")
    for row in summary_rows:
        print("summary=" + repr(row))
    print(f"summary_output={args.summary_output}")
    print(f"daily_output={args.daily_output}")
    print(f"blocks_output={args.blocks_output}")
    print(f"loo_output={args.loo_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
