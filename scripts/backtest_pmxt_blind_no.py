from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from weather_alpha.backtest.pmxt import BookState, iter_events
from weather_alpha.backtest.polymarket_manifest import load_manifest
from weather_alpha.backtest.validation import EventReturn, rank_results, validate_strategy


SITE_RE = re.compile(r"(?:site=|station[^A-Za-z0-9]+)([a-zA-Z]{3,5})", re.I)


def station_from_market(m) -> str:
    text = " ".join(x for x in (m.resolution_source, m.description, m.question) if x)
    found = SITE_RE.search(text)
    return found.group(1).upper() if found else "UNKNOWN"


def main() -> None:
    p = argparse.ArgumentParser(description="Replay resolved weather NO markets from compact pmxt data")
    p.add_argument("--manifest", default="data/weather/metadata/polymarket_weather_markets.jsonl")
    p.add_argument("--cache", default="data/weather/pmxt_v2/weather_only")
    p.add_argument("--out", default="research/results/pmxt_blind_no")
    p.add_argument("--min-no-cents", default="80,85,90,92,94,95,96,97,98")
    p.add_argument("--latencies", default="0,30,60,120,300,600,900")
    p.add_argument("--max-entry", type=float, default=0.995)
    p.add_argument("--min-events", type=int, default=1, help="Use 30+ for promotion; smoke runs may use 1")
    p.add_argument("--min-stations", type=int, default=1, help="Use 2+ for promotion; smoke runs may use 1")
    args = p.parse_args()

    manifest = load_manifest(Path(args.manifest))
    resolved = [m for m in manifest if m.resolved_yes is not None]
    no_token = {}
    market_by_token = {}
    for m in resolved:
        token = m.token_by_outcome.get("No") or m.token_by_outcome.get("NO")
        if token:
            no_token[token] = m
            market_by_token[token] = m

    paths = sorted(Path(args.cache).glob("*.parquet"))
    if not paths:
        raise SystemExit(f"no compact pmxt parquet under {args.cache}")
    if not no_token:
        raise SystemExit("manifest has no unambiguously resolved binary weather markets")

    thresholds = [int(x) / 100 for x in args.min_no_cents.split(",") if x.strip()]
    latencies = [int(x) for x in args.latencies.split(",") if x.strip()]
    states = {token: BookState.empty() for token in no_token}

    # Trigger once per token/threshold. Each latency variant is filled at the first
    # globally observed archive event at/after trigger+delay using the then-current book.
    trigger_at = {}
    filled = set()
    returns: list[EventReturn] = []
    raw_trades: list[dict] = []

    events = iter_events(paths, asset_ids=set(no_token), event_types={"book", "price_change"})
    for ev in events:
        state = states[ev.asset_id]
        state.apply(ev)
        now = ev.timestamp_received
        ask = state.best_ask

        if ask is not None and ask <= args.max_entry:
            for threshold in thresholds:
                key = (ev.asset_id, threshold)
                if key not in trigger_at and ask >= threshold:
                    trigger_at[key] = now

        # Process all pending fills against books as-of this archive receipt time.
        for (token, threshold), signal_time in list(trigger_at.items()):
            m = market_by_token[token]
            for latency in latencies:
                fill_key = (token, threshold, latency)
                if fill_key in filled or now < signal_time + timedelta(seconds=latency):
                    continue
                current = states[token].best_ask
                if current is None or current <= 0 or current > args.max_entry:
                    continue
                # pmxt exposes the market fee rate on events. Use the current event's
                # rate when this is the same token; otherwise reserve zero here and
                # label the execution case so fee-model sensitivity can be run later.
                bps = ev.fee_rate_bps if ev.asset_id == token and ev.fee_rate_bps is not None else 0
                fee = current * max(0, bps) / 10_000.0
                all_in = current + fee
                won = not bool(m.resolved_yes)
                pnl = (1.0 if won else 0.0) - all_in
                strategy = f"blind_no_min_{int(round(threshold*100)):02d}c"
                row = EventReturn(
                    strategy=strategy,
                    event_id=m.event_id or m.event_slug,
                    date=(m.end_date or str(now.date()))[:10],
                    station=station_from_market(m),
                    latency_seconds=latency,
                    execution_case="pmxt_best_ask_1share",
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
        results.append(validate_strategy(subset, min_events=args.min_events, min_stations=args.min_stations))
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

    print(f"resolved_markets={len(resolved)} resolved_no_tokens={len(no_token)}")
    print(f"pmxt_files={len(paths)} fills={len(returns)}")
    for r in ranked[:20]:
        print(
            f"{r.verdict.value:18s} {r.strategy:22s} latency={r.latency_seconds:4d}s "
            f"events={r.events:3d} pnl={r.net_pnl:+.4f} mean_roi={r.mean_event_roi:+.3%} "
            f"ci=[{r.roi_ci_low:+.3%},{r.roi_ci_high:+.3%}]"
        )


if __name__ == "__main__":
    main()
