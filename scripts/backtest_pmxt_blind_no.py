from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from weather_alpha.backtest.pmxt import BookState, iter_events
from weather_alpha.backtest.polymarket_manifest import load_manifest
from weather_alpha.backtest.validation import EventReturn, rank_results, validate_strategy
from weather_alpha.backtest.weather_scope import is_us_icao, settlement_station


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Replay resolved weather NO markets from compact pmxt data")
    p.add_argument("--manifest", default="data/weather/metadata/polymarket_weather_markets.jsonl")
    p.add_argument("--cache", default="data/weather/pmxt_v2/weather_only")
    p.add_argument("--out", default="research/results/pmxt_blind_no")
    p.add_argument("--min-no-cents", default="80,85,90,92,94,95,96,97,98")
    p.add_argument("--latencies", default="0,30,60,120,300,600,900")
    p.add_argument("--max-entry", type=float, default=0.995)
    p.add_argument("--region", choices=["us", "all"], default="us")
    p.add_argument("--min-events", type=int, default=30, help="Production gate; smoke runs may override to 1")
    p.add_argument("--min-dates", type=int, default=20, help="Production settlement-date gate; smoke runs may override to 1")
    p.add_argument("--min-stations", type=int, default=3, help="Production station gate; smoke runs may override to 1")
    return p


def main() -> None:
    args = build_parser().parse_args()

    manifest = load_manifest(Path(args.manifest))
    resolved = [m for m in manifest if m.resolved_yes is not None]
    market_by_token = {}
    for m in resolved:
        station = settlement_station(m)
        if args.region == "us" and not is_us_icao(station):
            continue
        token = m.token_by_outcome.get("No") or m.token_by_outcome.get("NO")
        if token:
            market_by_token[token] = m

    paths = sorted(Path(args.cache).glob("*.parquet"))
    if not paths:
        raise SystemExit(f"no compact pmxt parquet under {args.cache}")
    if not market_by_token:
        raise SystemExit("manifest has no matching unambiguously resolved binary weather markets")

    thresholds = [int(x) / 100 for x in args.min_no_cents.split(",") if x.strip()]
    latencies = [int(x) for x in args.latencies.split(",") if x.strip()]
    returns: list[EventReturn] = []
    raw_trades: list[dict] = []

    # pmxt is physically sorted by (market, asset_id, timestamp_received), not
    # globally by timestamp. Process each NO token's contiguous chronological run
    # independently; this is both time-correct and linear in the selected rows.
    current_token = None
    state = BookState.empty()
    trigger_at: dict[float, object] = {}
    filled: set[tuple[float, int]] = set()
    latest_fee_bps = 0

    events = iter_events(paths, asset_ids=set(market_by_token), event_types={"book", "price_change", "last_trade_price"})
    for ev in events:
        if ev.asset_id != current_token:
            current_token = ev.asset_id
            state = BookState.empty()
            trigger_at = {}
            filled = set()
            latest_fee_bps = 0

        if ev.fee_rate_bps is not None:
            latest_fee_bps = max(0, ev.fee_rate_bps)
        if ev.event_type in {"book", "price_change"}:
            state.apply(ev)
        now = ev.timestamp_received
        ask = state.best_ask
        if ask is None or ask <= 0:
            continue

        if ask <= args.max_entry:
            for threshold in thresholds:
                if threshold not in trigger_at and ask >= threshold:
                    trigger_at[threshold] = now

        m = market_by_token[current_token]
        for threshold, signal_time in trigger_at.items():
            for latency in latencies:
                fill_key = (threshold, latency)
                if fill_key in filled or now < signal_time + timedelta(seconds=latency):
                    continue
                current = state.best_ask
                if current is None or current <= 0 or current > args.max_entry:
                    continue
                fee = current * latest_fee_bps / 10_000.0
                all_in = current + fee
                won = not bool(m.resolved_yes)
                pnl = (1.0 if won else 0.0) - all_in
                strategy = f"blind_no_min_{int(round(threshold * 100)):02d}c"
                row = EventReturn(
                    strategy=strategy,
                    event_id=m.event_id or m.event_slug,
                    date=(m.end_date or str(now.date()))[:10],
                    station=settlement_station(m),
                    latency_seconds=latency,
                    execution_case=f"pmxt_global_best_ask_1share_{args.region}",
                    pnl=pnl,
                    capital=all_in,
                )
                returns.append(row)
                raw_trades.append({
                    **asdict(row),
                    "condition_id": m.condition_id,
                    "market_slug": m.market_slug,
                    "question": m.question,
                    "signal_time": signal_time.isoformat(),
                    "fill_time": now.isoformat(),
                    "entry_price": current,
                    "fee_reserved": fee,
                    "resolved_yes": m.resolved_yes,
                    "source_venue": "polymarket_global_pmxt_v2",
                })
                filled.add(fill_key)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "trades.jsonl").open("w", encoding="utf-8") as f:
        for row in raw_trades:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    results = []
    keys = sorted({(r.strategy, r.latency_seconds, r.execution_case) for r in returns})
    for strategy, latency, case in keys:
        subset = [r for r in returns if r.strategy == strategy and r.latency_seconds == latency and r.execution_case == case]
        results.append(
            validate_strategy(
                subset,
                min_events=args.min_events,
                min_dates=args.min_dates,
                min_stations=args.min_stations,
            )
        )
    ranked = rank_results(results)

    fields = list(asdict(ranked[0]).keys()) if ranked else []
    with (out / "ranking.csv").open("w", newline="", encoding="utf-8") as f:
        if fields:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in ranked:
                d = asdict(r)
                d["verdict"] = r.verdict.value
                w.writerow(d)

    print(f"resolved_markets={len(resolved)} selected_no_tokens={len(market_by_token)} region={args.region}")
    print(f"pmxt_files={len(paths)} fills={len(returns)}")
    for r in ranked[:20]:
        print(
            f"{r.verdict.value:18s} {r.strategy:22s} latency={r.latency_seconds:4d}s "
            f"events={r.events:3d} dates={r.dates:3d} pnl={r.net_pnl:+.4f} mean_roi={r.mean_event_roi:+.3%} "
            f"ci=[{r.roi_ci_low:+.3%},{r.roi_ci_high:+.3%}]"
        )


if __name__ == "__main__":
    main()
