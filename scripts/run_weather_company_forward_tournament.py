from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import scripts.run_experimental_weather_company_paper as paper


@dataclass(frozen=True)
class Track:
    name: str
    min_minutes_since_high: Decimal
    min_drop_from_high_f: Decimal
    max_positive_slope: Decimal
    price_floor: Decimal
    minimum_edge: Decimal


TRACKS = (
    Track("A_CONTROL", Decimal("60"), Decimal("1.0"), Decimal("0.02"), Decimal("0.15"), Decimal("0.08")),
    Track("B_MODERATE", Decimal("45"), Decimal("0.75"), Decimal("0.03"), Decimal("0.15"), Decimal("0.06")),
    Track("C_EXPLORATORY", Decimal("30"), Decimal("0.50"), Decimal("0.05"), Decimal("0.10"), Decimal("0.04")),
)


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
        return {t.name: {"traded_events": {}, "pending": []} for t in TRACKS}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def latest_contract_snapshot(path: Path, max_bytes: int = 8_000_000) -> tuple[str | None, list[dict[str, Any]]]:
    if not path.exists() or path.stat().st_size == 0:
        return None, []
    size = path.stat().st_size
    with path.open("rb") as fh:
        start = max(0, size - max_bytes)
        fh.seek(start)
        data = fh.read()
    if start:
        first_nl = data.find(b"\n")
        if first_nl >= 0:
            data = data[first_nl + 1 :]
    rows: list[dict[str, Any]] = []
    for raw in data.splitlines():
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("snapshot_time"):
            rows.append(obj)
    if not rows:
        return None, []
    latest = max(str(r["snapshot_time"]) for r in rows)
    return latest, [r for r in rows if str(r.get("snapshot_time")) == latest]


def lock_for_track(row: dict[str, Any], track: Track) -> tuple[bool, list[str]]:
    mins = D(row.get("minutes_since_high"))
    drop = D(row.get("drop_from_high_f"))
    slope = D(row.get("slope_15m_f_per_min"))
    reasons: list[str] = []
    if mins is None or mins < track.min_minutes_since_high:
        reasons.append("MINUTES_SINCE_HIGH")
    if drop is None or drop < track.min_drop_from_high_f:
        reasons.append("DROP_FROM_HIGH")
    if slope is None:
        reasons.append("NO_15M_SLOPE")
    elif slope > track.max_positive_slope:
        reasons.append("POSITIVE_SLOPE")
    return not reasons, reasons


def evaluate_contract(row: dict[str, Any], track: Track) -> dict[str, Any] | None:
    yes_ask = D(row.get("yes_ask"))
    no_ask = D(row.get("no_ask"))
    if yes_ask is None and no_ask is None:
        return None
    lock_pass, lock_reasons = lock_for_track(row, track)
    in_bucket = bool(row.get("in_bucket_now"))
    drop = D(row.get("drop_from_high_f"))
    slope = D(row.get("slope_15m_f_per_min"))
    p_yes = paper.provisional_probability(in_bucket=in_bucket, lock_pass=lock_pass, drop_f=drop, slope=slope)
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
    net, gross, side, ask, p_side, fee = max(choices, key=lambda x: x[0])
    decision = "PAPER_TRADE_ELIGIBLE"
    if not lock_pass:
        decision = "LOCK_GATE_FAIL"
    elif ask < track.price_floor:
        decision = "PRICE_BELOW_FLOOR"
    elif gross < track.minimum_edge:
        decision = "EDGE_BELOW_MINIMUM"
    elif net <= 0:
        decision = "NEGATIVE_AFTER_FEE"
    return {
        **row,
        "track": track.name,
        "track_lock_pass": lock_pass,
        "track_lock_reasons": lock_reasons,
        "track_min_minutes_since_high": str(track.min_minutes_since_high),
        "track_min_drop_from_high_f": str(track.min_drop_from_high_f),
        "track_max_positive_slope": str(track.max_positive_slope),
        "track_price_floor": str(track.price_floor),
        "track_minimum_edge": str(track.minimum_edge),
        "tournament_side": side,
        "tournament_probability_side": str(p_side),
        "tournament_probability_yes": str(p_yes),
        "tournament_entry_ask": str(ask),
        "tournament_gross_edge": str(gross),
        "tournament_fee_per_contract": str(fee),
        "tournament_net_edge": str(net),
        "tournament_decision": decision,
        "live_order_submission": False,
    }


def settle_pending(args: argparse.Namespace, state: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    for track in TRACKS:
        ts = state.setdefault(track.name, {"traded_events": {}, "pending": []})
        remain = []
        for item in ts.get("pending", []):
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
                "fill_model": f"SYNCHRONIZED_LIVE_QUOTE_AFTER_{args.latency_seconds}S",
                "side": side,
            })
            append_jsonl(args.fills_output, item)
            print(f"TOURNAMENT_FILL track={track.name} ticker={item['ticker']} side={side} price={ask}", flush=True)
        ts["pending"] = remain


def run_cycle(args: argparse.Namespace, state: dict[str, Any]) -> None:
    settle_pending(args, state)
    snap_time, rows = latest_contract_snapshot(args.contract_history)
    now = datetime.now(timezone.utc)
    if not rows or snap_time is None:
        print("tournament_cycle no_dashboard_snapshot", flush=True)
        return
    try:
        snap_dt = datetime.fromisoformat(snap_time.replace("Z", "+00:00"))
        age_s = (now - snap_dt).total_seconds()
    except Exception:
        age_s = 999999
    if age_s > args.max_snapshot_age_seconds:
        print(f"tournament_cycle stale_dashboard_snapshot age_s={age_s:.1f}", flush=True)
        return

    by_event: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        event = str(row.get("event_id") or "")
        if event:
            by_event.setdefault(event, []).append(row)

    decision_counts: dict[str, dict[str, int]] = {}
    for track in TRACKS:
        ts = state.setdefault(track.name, {"traded_events": {}, "pending": []})
        counts: dict[str, int] = {}
        for event_id, group in sorted(by_event.items()):
            evaluated = [e for r in group if (e := evaluate_contract(r, track)) is not None]
            if not evaluated:
                continue
            # Critical invariant: eligibility is decided only on the SAME event-level candidate
            # that would actually be signaled, ranked by after-fee edge.
            best = max(evaluated, key=lambda r: D(r.get("tournament_net_edge")) or Decimal("-999"))
            if event_id in ts.get("traded_events", {}):
                best["tournament_decision"] = "ALREADY_TRADED_EVENT"
            elif any(str(p.get("event_id")) == event_id for p in ts.get("pending", [])):
                best["tournament_decision"] = "PENDING_DELAYED_FILL"
            decision = str(best["tournament_decision"])
            counts[decision] = counts.get(decision, 0) + 1
            append_jsonl(args.decisions_output, best)
            if decision != "PAPER_TRADE_ELIGIBLE":
                continue
            due = now.timestamp() + args.latency_seconds
            best["signal_time"] = now.isoformat()
            best["fill_due"] = datetime.fromtimestamp(due, tz=timezone.utc).isoformat()
            best["paper_status"] = "PENDING_DELAYED_FILL"
            best["side"] = best["tournament_side"]
            append_jsonl(args.signals_output, best)
            ts.setdefault("pending", []).append(dict(best))
            ts.setdefault("traded_events", {})[event_id] = {
                "ticker": best.get("ticker"), "signal_time": best["signal_time"]
            }
            print(
                f"TOURNAMENT_SIGNAL track={track.name} event={event_id} ticker={best.get('ticker')} "
                f"side={best['tournament_side']} ask={best['tournament_entry_ask']} "
                f"gross={best['tournament_gross_edge']} net={best['tournament_net_edge']}",
                flush=True,
            )
        decision_counts[track.name] = counts
    save_state(args.state, state)
    print(f"tournament_cycle snapshot={snap_time} events={len(by_event)} counts={decision_counts}", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description="Synchronized three-track Weather Company forward paper tournament")
    p.add_argument("--contract-history", type=Path, default=Path("/data/weather/live/weather_company_contract_history.jsonl"))
    p.add_argument("--state", type=Path, default=Path("/data/weather/live/weather_company_tournament_state.json"))
    p.add_argument("--decisions-output", type=Path, default=Path("/data/weather/live/weather_company_tournament_decisions.jsonl"))
    p.add_argument("--signals-output", type=Path, default=Path("/data/weather/live/weather_company_tournament_signals.jsonl"))
    p.add_argument("--fills-output", type=Path, default=Path("/data/weather/live/weather_company_tournament_fills.jsonl"))
    p.add_argument("--loop-seconds", type=int, default=60)
    p.add_argument("--latency-seconds", type=int, default=300)
    p.add_argument("--max-snapshot-age-seconds", type=int, default=180)
    p.add_argument("--contracts", type=int, default=1)
    args = p.parse_args()
    print("mode=WEATHER_COMPANY_FORWARD_TOURNAMENT live_order_submission=false", flush=True)
    print("tracks=A_CONTROL,B_MODERATE,C_EXPLORATORY synchronized_dashboard_snapshot=true", flush=True)
    state = load_state(args.state)
    while True:
        try:
            run_cycle(args, state)
        except KeyboardInterrupt:
            save_state(args.state, state)
            raise
        except Exception as exc:
            print(f"tournament_error={type(exc).__name__}:{exc}", flush=True)
            save_state(args.state, state)
        time.sleep(max(10, args.loop_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
