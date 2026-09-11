from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


def D(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def derive_contract_decision(row: dict[str, Any], *, min_edge: Decimal, price_floor: Decimal,
                             traded: bool, pending: bool) -> str:
    if traded:
        return "ALREADY_TRADED_EVENT"
    if pending:
        return "PENDING_DELAYED_FILL"
    if not row.get("lock_gate_pass", False):
        return "LOCK_GATE_FAIL"
    ask = D(row.get("entry_ask"))
    gross = D(row.get("gross_edge"))
    net = D(row.get("net_edge_after_fee"))
    if ask is None:
        return "NO_EXECUTABLE_QUOTE"
    if ask < price_floor:
        return "PRICE_BELOW_FLOOR"
    if gross is None or gross < min_edge:
        return "EDGE_BELOW_MINIMUM"
    if net is None or net <= 0:
        return "NEGATIVE_AFTER_FEE"
    return "PAPER_TRADE_ELIGIBLE"


def age_minutes(ts: str | None) -> Decimal | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return Decimal(str((datetime.now(timezone.utc) - dt).total_seconds() / 60.0))


def main() -> int:
    p = argparse.ArgumentParser(description="Compact health/status report for Weather Company forward paper trading")
    p.add_argument("--dashboard", type=Path, default=Path("/data/weather/live/weather_company_dashboard.json"))
    p.add_argument("--state", type=Path, default=Path("/data/weather/live/weather_company_paper_state.json"))
    p.add_argument("--signals", type=Path, default=Path("/data/weather/live/weather_company_paper_signals.jsonl"))
    p.add_argument("--fills", type=Path, default=Path("/data/weather/live/weather_company_paper_fills.jsonl"))
    p.add_argument("--settled", type=Path, default=Path("/data/weather/live/weather_company_paper_settled.jsonl"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/live/weather_company_forward_status.txt"))
    p.add_argument("--price-floor", type=Decimal, default=Decimal("0.15"))
    p.add_argument("--minimum-edge", type=Decimal, default=Decimal("0.08"))
    args = p.parse_args()

    snap = load_json(args.dashboard)
    state = load_json(args.state)
    event_rows = snap.get("rows") if isinstance(snap.get("rows"), list) else []
    contract_rows = snap.get("contract_rows") if isinstance(snap.get("contract_rows"), list) else []
    traded_events = state.get("traded_events") if isinstance(state.get("traded_events"), dict) else {}
    pending_rows = state.get("pending") if isinstance(state.get("pending"), list) else []
    pending_events = {str(r.get("event_id")) for r in pending_rows if isinstance(r, dict)}

    derived: list[dict[str, Any]] = []
    for r0 in contract_rows:
        if not isinstance(r0, dict):
            continue
        r = dict(r0)
        eid = str(r.get("event_id") or "")
        r["decision"] = derive_contract_decision(
            r,
            min_edge=args.minimum_edge,
            price_floor=args.price_floor,
            traded=eid in traded_events,
            pending=eid in pending_events,
        )
        derived.append(r)

    reasons = Counter(r["decision"] for r in derived)
    event_reasons = Counter(str(r.get("decision") or "UNKNOWN") for r in event_rows if isinstance(r, dict))
    eligible = [r for r in derived if r["decision"] == "PAPER_TRADE_ELIGIBLE"]

    # Near-misses are useful diagnostics only; they do not alter the frozen paper rules.
    near = [r for r in derived if r["decision"] != "PAPER_TRADE_ELIGIBLE"]
    near.sort(key=lambda r: D(r.get("net_edge_after_fee")) or Decimal("-999"), reverse=True)

    signals = read_jsonl(args.signals)
    fills = [r for r in read_jsonl(args.fills) if r.get("fill_status") == "PAPER_FILLED"]
    settled = [r for r in read_jsonl(args.settled) if r.get("score_status") == "SETTLED_SCORED"]
    wins = sum(1 for r in settled if r.get("won") is True)
    pnl = sum((D(r.get("net_pnl")) or Decimal("0") for r in settled), Decimal("0"))

    generated = str(snap.get("generated_at") or "")
    snap_age = age_minutes(generated)
    counts = snap.get("counts") if isinstance(snap.get("counts"), dict) else {}

    lines = [
        "WEATHER COMPANY FORWARD PAPER STATUS",
        f"dashboard_generated={generated or '-'} age_minutes={snap_age if snap_age is not None else '-'} live_order_submission=false",
        f"contracts_fetched={counts.get('contracts','-')} events={counts.get('events','-')} contract_quotes_evaluated={len(derived)}",
        f"signals_total={len(signals)} fills_total={len(fills)} settled_total={len(settled)} wins={wins} net_pnl={pnl}",
        f"pending_events={len(pending_events)} traded_events={len(traded_events)} eligible_now={len(eligible)}",
        "",
        "EVENT-LEVEL DECISIONS",
    ]
    for k, v in sorted(event_reasons.items()):
        lines.append(f"{k}: {v}")
    if not event_reasons:
        lines.append("none")

    lines.extend(["", "CONTRACT-LEVEL GATE COUNTS"])
    for k, v in sorted(reasons.items()):
        lines.append(f"{k}: {v}")
    if not reasons:
        lines.append("none")

    lines.extend(["", "ELIGIBLE NOW"])
    if not eligible:
        lines.append("none")
    for r in sorted(eligible, key=lambda x: D(x.get("net_edge_after_fee")) or Decimal("-999"), reverse=True)[:15]:
        lines.append(
            f"{r.get('station')} {r.get('ticker')} bucket={r.get('bucket')} side={r.get('side')} "
            f"p_yes={r.get('model_probability_yes')} yes={r.get('yes_bid')}/{r.get('yes_ask')} "
            f"no={r.get('no_bid')}/{r.get('no_ask')} ask={r.get('entry_ask')} "
            f"gross={r.get('gross_edge')} net={r.get('net_edge_after_fee')}"
        )

    lines.extend(["", "TOP NEAR-MISSES / WHY NO TRADE"])
    if not near:
        lines.append("none")
    for r in near[:20]:
        lines.append(
            f"{r.get('station')} {r.get('ticker')} bucket={r.get('bucket')} decision={r.get('decision')} "
            f"temp={r.get('latest_temp_f')} high={r.get('high_so_far_f')} mins_since_high={r.get('minutes_since_high')} "
            f"drop={r.get('drop_from_high_f')} slope={r.get('slope_15m_f_per_min')} side={r.get('side')} "
            f"p_yes={r.get('model_probability_yes')} ask={r.get('entry_ask')} gross={r.get('gross_edge')} net={r.get('net_edge_after_fee')}"
        )

    text = "\n".join(lines) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
