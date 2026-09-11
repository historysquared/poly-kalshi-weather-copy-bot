from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import scripts.run_experimental_weather_company_paper as paper
import scripts.run_weather_company_forward_tournament as tourney

TRACK = "D_DIAGNOSTIC_NO_LOCK"


def D(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"traded_events": {}, "pending": []}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def lock_components(row: dict[str, Any], *, min_minutes: Decimal, min_drop: Decimal, max_slope: Decimal) -> list[str]:
    reasons: list[str] = []
    mins = D(row.get("minutes_since_high"))
    drop = D(row.get("drop_from_high_f"))
    slope = D(row.get("slope_15m_f_per_min"))
    if mins is None:
        reasons.append("LOCK_FAIL_TIME_MISSING")
    elif mins < min_minutes:
        reasons.append("LOCK_FAIL_TIME")
    if drop is None:
        reasons.append("LOCK_FAIL_DROP_MISSING")
    elif drop < min_drop:
        reasons.append("LOCK_FAIL_DROP")
    if slope is None:
        reasons.append("LOCK_FAIL_SLOPE_MISSING")
    elif slope > max_slope:
        reasons.append("LOCK_FAIL_POSITIVE_SLOPE")
    return reasons


def evaluate(row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any] | None:
    yes_ask = D(row.get("yes_ask"))
    no_ask = D(row.get("no_ask"))
    if yes_ask is None and no_ask is None:
        return None

    control_failures = lock_components(
        row,
        min_minutes=args.control_min_minutes_since_high,
        min_drop=args.control_min_drop_from_high_f,
        max_slope=args.control_max_positive_slope,
    )
    in_bucket = bool(row.get("in_bucket_now"))
    drop = D(row.get("drop_from_high_f"))
    slope = D(row.get("slope_15m_f_per_min"))

    # Diagnostic-only: intentionally bypass the production lock gate so we can
    # exercise signal -> delayed fill -> settlement and measure the incremental
    # value of the lock. This is NEVER a production recommendation.
    p_yes = paper.provisional_probability(
        in_bucket=in_bucket,
        lock_pass=True,
        drop_f=drop,
        slope=slope,
    )
    p_no = Decimal("1") - p_yes
    choices: list[tuple[Decimal, Decimal, str, Decimal, Decimal, Decimal]] = []
    if yes_ask is not None:
        fee = paper.kalshi_taker_fee(yes_ask, 1)
        gross = p_yes - yes_ask
        choices.append((gross - fee, gross, "YES", yes_ask, p_yes, fee))
    if no_ask is not None:
        fee = paper.kalshi_taker_fee(no_ask, 1)
        gross = p_no - no_ask
        choices.append((gross - fee, gross, "NO", no_ask, p_no, fee))
    if not choices:
        return None

    net, gross, side, ask, p_side, fee = max(choices, key=lambda x: x[0])
    if ask < args.price_floor:
        decision = "PRICE_BELOW_FLOOR"
    elif gross < args.minimum_edge:
        decision = "EDGE_BELOW_MINIMUM"
    elif net <= 0:
        decision = "NEGATIVE_AFTER_FEE"
    else:
        decision = "DIAGNOSTIC_PAPER_ELIGIBLE"

    return {
        **row,
        "track": TRACK,
        "diagnostic_only": True,
        "production_eligible": False,
        "lock_gate_bypassed": True,
        "control_lock_failures": control_failures,
        "control_lock_would_pass": not control_failures,
        "tournament_side": side,
        "tournament_probability_side": str(p_side),
        "tournament_probability_yes": str(p_yes),
        "tournament_entry_ask": str(ask),
        "tournament_gross_edge": str(gross),
        "tournament_fee_per_contract": str(fee),
        "tournament_net_edge": str(net),
        "tournament_decision": decision,
        "model_status": "DIAGNOSTIC_ONLY_LOCK_BYPASS_UNCALIBRATED",
        "live_order_submission": False,
    }


def settle_pending(args: argparse.Namespace, state: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    remain = []
    for item in state.get("pending", []):
        due = datetime.fromisoformat(str(item["fill_due"]).replace("Z", "+00:00"))
        if due > now:
            remain.append(item)
            continue
        market = paper.current_quote(str(item["ticker"]))
        if market is None:
            item["fill_status"] = "NO_MARKET_QUOTE"
            append_jsonl(args.fills_output, item)
            continue
        side = str(item["tournament_side"])
        ask = paper.market_price(market, "yes_ask" if side == "YES" else "no_ask")
        if ask is None:
            other = paper.market_price(market, "no_bid" if side == "YES" else "yes_bid")
            ask = None if other is None else Decimal("1") - other
        if ask is None:
            item["fill_status"] = "NO_EXECUTABLE_ASK"
            append_jsonl(args.fills_output, item)
            continue
        fee = paper.kalshi_taker_fee(ask, args.contracts)
        item.update({
            "fill_status": "PAPER_FILLED",
            "fill_time": now.isoformat(),
            "fill_price": str(ask),
            "contracts": args.contracts,
            "estimated_taker_fee": str(fee),
            "capital_at_risk": str(ask * Decimal(args.contracts) + fee),
            "fill_model": f"DIAGNOSTIC_LIVE_QUOTE_AFTER_{args.latency_seconds}S",
            "side": side,
            "live_order_submission": False,
        })
        append_jsonl(args.fills_output, item)
        print(f"DIAGNOSTIC_FILL ticker={item['ticker']} side={side} price={ask}", flush=True)
    state["pending"] = remain


def run_cycle(args: argparse.Namespace, state: dict[str, Any]) -> None:
    settle_pending(args, state)
    snap_time, rows = tourney.latest_contract_snapshot(args.contract_history)
    now = datetime.now(timezone.utc)
    if not rows or snap_time is None:
        print("diagnostic_cycle no_dashboard_snapshot", flush=True)
        return
    try:
        snap_dt = datetime.fromisoformat(snap_time.replace("Z", "+00:00"))
        age_s = (now - snap_dt).total_seconds()
    except Exception:
        age_s = 999999
    if age_s > args.max_snapshot_age_seconds:
        print(f"diagnostic_cycle stale_dashboard_snapshot age_s={age_s:.1f}", flush=True)
        return

    by_event: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        event_id = str(row.get("event_id") or "")
        if event_id:
            by_event.setdefault(event_id, []).append(row)

    counts: Counter[str] = Counter()
    lock_counts: Counter[str] = Counter()
    traded = state.setdefault("traded_events", {})
    pending = state.setdefault("pending", [])

    for event_id, group in sorted(by_event.items()):
        evaluated = [e for r in group if (e := evaluate(r, args)) is not None]
        if not evaluated:
            counts["NO_EXECUTABLE_QUOTE"] += 1
            continue
        best = max(evaluated, key=lambda r: D(r.get("tournament_net_edge")) or Decimal("-999"))
        for reason in best.get("control_lock_failures", []):
            lock_counts[str(reason)] += 1
        if event_id in traded:
            decision = "ALREADY_TRADED_EVENT"
        elif any(str(p.get("event_id")) == event_id for p in pending):
            decision = "PENDING_DELAYED_FILL"
        else:
            decision = str(best["tournament_decision"])
        best["tournament_decision"] = decision
        counts[decision] += 1
        append_jsonl(args.decisions_output, best)
        if decision != "DIAGNOSTIC_PAPER_ELIGIBLE":
            continue

        best["signal_time"] = now.isoformat()
        best["fill_due"] = datetime.fromtimestamp(now.timestamp() + args.latency_seconds, tz=timezone.utc).isoformat()
        best["paper_status"] = "PENDING_DELAYED_FILL"
        best["side"] = best["tournament_side"]
        append_jsonl(args.signals_output, best)
        pending.append(dict(best))
        traded[event_id] = {"ticker": best.get("ticker"), "signal_time": best["signal_time"]}
        print(
            f"DIAGNOSTIC_SIGNAL event={event_id} ticker={best.get('ticker')} side={best['tournament_side']} "
            f"ask={best['tournament_entry_ask']} net={best['tournament_net_edge']} "
            f"control_lock_failures={','.join(best.get('control_lock_failures', [])) or 'NONE'}",
            flush=True,
        )

    save_state(args.state, state)
    print(
        f"diagnostic_cycle snapshot={snap_time} events={len(by_event)} decisions={dict(counts)} "
        f"control_lock_components={dict(lock_counts)} pending={len(pending)} traded={len(traded)}",
        flush=True,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Diagnostic no-lock Weather Company paper track")
    p.add_argument("--contract-history", type=Path, default=Path("/data/weather/live/weather_company_contract_history.jsonl"))
    p.add_argument("--state", type=Path, default=Path("/data/weather/live/weather_company_diagnostic_state.json"))
    p.add_argument("--decisions-output", type=Path, default=Path("/data/weather/live/weather_company_diagnostic_decisions.jsonl"))
    p.add_argument("--signals-output", type=Path, default=Path("/data/weather/live/weather_company_diagnostic_signals.jsonl"))
    p.add_argument("--fills-output", type=Path, default=Path("/data/weather/live/weather_company_diagnostic_fills.jsonl"))
    p.add_argument("--loop-seconds", type=int, default=60)
    p.add_argument("--latency-seconds", type=int, default=300)
    p.add_argument("--max-snapshot-age-seconds", type=int, default=180)
    p.add_argument("--contracts", type=int, default=1)
    p.add_argument("--price-floor", type=Decimal, default=Decimal("0.10"))
    p.add_argument("--minimum-edge", type=Decimal, default=Decimal("0.04"))
    p.add_argument("--control-min-minutes-since-high", type=Decimal, default=Decimal("60"))
    p.add_argument("--control-min-drop-from-high-f", type=Decimal, default=Decimal("1.0"))
    p.add_argument("--control-max-positive-slope", type=Decimal, default=Decimal("0.02"))
    args = p.parse_args()

    print("mode=WEATHER_COMPANY_DIAGNOSTIC_NO_LOCK live_order_submission=false", flush=True)
    print("diagnostic_only=true production_eligible=false lock_gate_bypassed=true", flush=True)
    state = load_state(args.state)
    while True:
        try:
            run_cycle(args, state)
        except KeyboardInterrupt:
            save_state(args.state, state)
            raise
        except Exception as exc:
            print(f"diagnostic_error={type(exc).__name__}:{exc}", flush=True)
            save_state(args.state, state)
        time.sleep(max(10, args.loop_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
