from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.backtest.causal_trade_backtest import D, executable_buy, kalshi_fee, parse_dt, snapshot_at_or_after, terminal_pnl


def contract_yes(contract: dict[str, Any], value_f: Decimal) -> bool:
    shape = str(contract.get("shape") or "").lower()
    lo = D(contract.get("lower"))
    hi = D(contract.get("upper"))
    if shape == "above":
        return lo is not None and value_f >= lo
    if shape == "below":
        return hi is not None and value_f <= hi
    if shape == "bucket":
        if lo is None or hi is None:
            return False
        return lo <= value_f <= hi
    raise ValueError(f"unsupported contract shape {shape!r}")


def first_event_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_event: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("status") != "CANDIDATE" or not row.get("candidate_side"):
            continue
        by_event[(str(row.get("station")), str(row.get("settlement_date")))].append(row)
    selected = []
    for _, group in sorted(by_event.items()):
        first_time = min(parse_dt(r["book_time"]) for r in group)
        same_time = [r for r in group if parse_dt(r["book_time"]) == first_time]
        selected.append(max(same_time, key=lambda r: D(r.get("candidate_gross_edge")) or Decimal("-999")))
    return selected


def safe_table(rows: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows) if rows else pa.table({"contract_id": pa.array([], type=pa.string())})


def main() -> int:
    p = argparse.ArgumentParser(description="Economic backtest of causal settlement candidates")
    p.add_argument("--signals", type=Path, default=Path("/data/weather/results/settlement_reconstruction/causal_settlement_pmxt_full.parquet"))
    p.add_argument("--replay", type=Path, default=Path("/data/weather/normalized/orderbooks/kalshi_weather_pmxt_final6h.parquet"))
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_resolved.parquet"))
    p.add_argument("--cli", type=Path, default=Path("/data/weather/normalized/settlements/nws_cli_daily.parquet"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/causal_trade_backtest.parquet"))
    p.add_argument("--summary-output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/causal_trade_backtest_summary.parquet"))
    p.add_argument("--latencies", default="0,30,60,120,300")
    p.add_argument("--depths", default="1,5,25")
    p.add_argument("--price-floors", default="0,0.15")
    p.add_argument("--taker-fee-coefficient", type=Decimal, default=Decimal("0.07"), help="Kalshi general event-contract taker coefficient; override if series-specific schedule differs")
    args = p.parse_args()

    signals = pq.read_table(args.signals).to_pylist()
    replay = pq.read_table(args.replay).to_pylist()
    catalog_rows = [r for r in pq.read_table(args.catalog).to_pylist() if r.get("status") == "EXACT"]
    catalog = {str(r.get("contract_id") or ""): r for r in catalog_rows}
    cli_rows = pq.read_table(args.cli).to_pylist()
    cli = {(str(r.get("station") or ""), str(r.get("valid_date") or "")[:10]): r for r in cli_rows}

    replay_by_contract: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in replay:
        replay_by_contract[str(row.get("contract_id") or "")].append(row)
    for rows in replay_by_contract.values():
        rows.sort(key=lambda r: parse_dt(r["book_time"]))

    selected = first_event_candidates(signals)
    latencies = [int(x) for x in args.latencies.split(",") if x.strip()]
    depths = [Decimal(x.strip()) for x in args.depths.split(",") if x.strip()]
    floors = [Decimal(x.strip()) for x in args.price_floors.split(",") if x.strip()]

    trades: list[dict[str, Any]] = []
    for sig in selected:
        ticker = str(sig.get("contract_id") or "")
        contract = catalog.get(ticker)
        if not contract:
            continue
        station = str(sig.get("station") or "")
        day = str(sig.get("settlement_date") or "")[:10]
        cli_row = cli.get((station, day))
        official_high = D(cli_row.get("high_f")) if cli_row else None
        if official_high is None:
            continue
        official_yes = contract_yes(contract, official_high)
        side = str(sig.get("candidate_side") or "").upper()
        signal_time = parse_dt(sig["book_time"])
        contract_books = replay_by_contract.get(ticker, [])

        for latency in latencies:
            exec_snap = snapshot_at_or_after(contract_books, signal_time, latency)
            if exec_snap is None:
                for depth in depths:
                    for floor in floors:
                        trades.append({
                            "contract_id": ticker, "station": station, "settlement_date": day,
                            "signal_time": sig["book_time"], "side": side,
                            "latency_seconds": latency, "depth_contracts": str(depth), "price_floor": str(floor),
                            "status": "NO_POST_LATENCY_BOOK", "official_cli_high_f": str(official_high),
                            "official_yes_winner": official_yes,
                        })
                continue
            for depth in depths:
                fill = executable_buy(exec_snap, side, depth)
                for floor in floors:
                    row = {
                        "contract_id": ticker,
                        "station": station,
                        "settlement_date": day,
                        "signal_time": sig["book_time"],
                        "execution_time": exec_snap["book_time"],
                        "side": side,
                        "signal_gross_edge": sig.get("candidate_gross_edge"),
                        "signal_price": sig.get("candidate_price"),
                        "latency_seconds": latency,
                        "depth_contracts": str(depth),
                        "price_floor": str(floor),
                        "official_cli_high_f": str(official_high),
                        "official_yes_winner": official_yes,
                        "fee_coefficient": str(args.taker_fee_coefficient),
                        "fee_formula": "ceil_cent(coeff*C*P*(1-P))",
                        "dedup_rule": "FIRST_EVENT_SIGNAL_THEN_MAX_EDGE_AT_SAME_TIMESTAMP",
                    }
                    if not fill.complete or fill.vwap is None:
                        row["status"] = "INSUFFICIENT_DEPTH"
                    elif fill.vwap < floor:
                        row["status"] = "PRICE_FLOOR_REJECT"
                        row["execution_vwap"] = str(fill.vwap)
                    else:
                        fee = kalshi_fee(coefficient=args.taker_fee_coefficient, contracts=depth, price=fill.vwap)
                        pnl = terminal_pnl(side=side, price=fill.vwap, contracts=depth, official_yes_winner=official_yes, fee=fee)
                        cost = depth * fill.vwap + fee
                        row.update({
                            "status": "EXECUTED",
                            "execution_vwap": str(fill.vwap),
                            "filled_contracts": str(fill.filled),
                            "fee": str(fee),
                            "pnl": str(pnl),
                            "capital_at_risk": str(cost),
                            "roi": None if cost == 0 else str(pnl / cost),
                            "won": (official_yes if side == "YES" else not official_yes),
                        })
                    trades.append(row)

    summary: list[dict[str, Any]] = []
    for latency in latencies:
        for depth in depths:
            for floor in floors:
                subset = [r for r in trades if r["latency_seconds"] == latency and r["depth_contracts"] == str(depth) and r["price_floor"] == str(floor)]
                executed = [r for r in subset if r.get("status") == "EXECUTED"]
                pnl = sum((D(r.get("pnl")) or Decimal("0") for r in executed), Decimal("0"))
                capital = sum((D(r.get("capital_at_risk")) or Decimal("0") for r in executed), Decimal("0"))
                dates = sorted({r["settlement_date"] for r in executed})
                summary.append({
                    "latency_seconds": latency,
                    "depth_contracts": str(depth),
                    "price_floor": str(floor),
                    "selected_events": len(selected),
                    "executed_trades": len(executed),
                    "wins": sum(bool(r.get("won")) for r in executed),
                    "losses": sum(not bool(r.get("won")) for r in executed),
                    "win_rate": None if not executed else sum(bool(r.get("won")) for r in executed) / len(executed),
                    "net_pnl": str(pnl),
                    "capital_at_risk": str(capital),
                    "roi": None if capital == 0 else str(pnl / capital),
                    "settlement_dates": len(dates),
                    "bootstrap_status": "INSUFFICIENT_DATES" if len(dates) < 20 else "READY_FOR_DATE_BLOCK_BOOTSTRAP",
                    "fee_coefficient": str(args.taker_fee_coefficient),
                })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(safe_table(trades), args.output, compression="zstd")
    pq.write_table(safe_table(summary), args.summary_output, compression="zstd")

    print(f"candidate_snapshots={sum(1 for r in signals if r.get('status') == 'CANDIDATE')} selected_physical_events={len(selected)}")
    print("dedup_rule=FIRST_EVENT_SIGNAL_THEN_MAX_EDGE_AT_SAME_TIMESTAMP")
    print(f"taker_fee_coefficient={args.taker_fee_coefficient} fee_rounding=CEIL_TO_NEXT_CENT")
    for r in summary:
        print("summary=" + repr(r))
    print(f"trade_output={args.output}")
    print(f"summary_output={args.summary_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
