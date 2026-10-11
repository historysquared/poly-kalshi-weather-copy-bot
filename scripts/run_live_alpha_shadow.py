#!/usr/bin/env python3
"""Read-only live alpha monitor for structural weather-market opportunities.

Signals are shadow alerts only. No order submission is imported or enabled.
"""
from __future__ import annotations

import argparse
import json
import time
from decimal import Decimal, ROUND_CEILING
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weather_alpha.ops.health import StrategyHealth
from weather_alpha.markets.kalshi_weather_series import ALL_DAILY_TEMPERATURE_SERIES


def D(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def latest_snapshot(path: Path, max_bytes: int = 8_000_000) -> tuple[str | None, list[dict[str, Any]]]:
    if not path.exists() or path.stat().st_size == 0:
        return None, []
    size = path.stat().st_size
    with path.open("rb") as fh:
        start = max(0, size - max_bytes)
        fh.seek(start)
        raw = fh.read()
    if start:
        cut = raw.find(b"\n")
        raw = raw[cut + 1:] if cut >= 0 else b""
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("snapshot_time"):
            rows.append(row)
    if not rows:
        return None, []
    latest = max(str(row["snapshot_time"]) for row in rows)
    return latest, [row for row in rows if str(row.get("snapshot_time")) == latest]


def _fee_for_price(value: Any) -> float:
    p = D(value)
    if p is None:
        return 0.0
    raw = Decimal("0.07") * Decimal(str(p)) * (Decimal("1") - Decimal(str(p)))
    return float((raw * Decimal("100")).to_integral_value(rounding=ROUND_CEILING) / Decimal("100"))


def catalog_snapshot(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    if not path.exists():
        return None, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, []
    generated = str(payload.get("generated_at") or "") or None
    wanted = set(ALL_DAILY_TEMPERATURE_SERIES)
    rows: list[dict[str, Any]] = []
    for series in payload.get("active_series") or []:
        if not isinstance(series, dict) or str(series.get("ticker") or "") not in wanted:
            continue
        source_names = [str(x.get("name") or "") for x in (series.get("settlement_sources") or []) if isinstance(x, dict)]
        source_family = "WEATHER_COMPANY" if any("Weather Company" in x for x in source_names) else "UNKNOWN"
        for market in series.get("markets") or []:
            if not isinstance(market, dict):
                continue
            strike = str(market.get("strike_type") or "").lower()
            shape = {"between": "bucket", "less": "below", "greater": "above"}.get(strike, strike)
            yes_ask = D(market.get("yes_ask_dollars"))
            no_ask = D(market.get("no_ask_dollars"))
            rows.append({
                "snapshot_time": generated,
                "event_id": market.get("event_ticker"),
                "ticker": market.get("ticker"),
                "series": series.get("ticker"),
                "shape": shape,
                "lower": market.get("floor_strike"),
                "upper": market.get("cap_strike"),
                "yes_ask": yes_ask,
                "no_ask": no_ask,
                "estimated_yes_taker_fee_per_contract": _fee_for_price(yes_ask),
                "estimated_no_taker_fee_per_contract": _fee_for_price(no_ask),
                "source_family": source_family,
                "settlement_date": None,
            })
    return generated, rows


def load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def input_snapshot_key(catalog_snapshot: str, enriched_snapshot: str | None) -> str:
    return f"{catalog_snapshot}|{enriched_snapshot or ''}"


def fee(row: dict[str, Any], side: str | None = None) -> float:
    generic = D(row.get("estimated_taker_fee_per_contract"))
    if side == "YES":
        explicit = D(row.get("estimated_yes_taker_fee_per_contract"))
        if explicit is not None:
            return explicit
        if generic is not None:
            return generic
        return _fee_for_price(row.get("yes_ask"))
    if side == "NO":
        explicit = D(row.get("estimated_no_taker_fee_per_contract"))
        if explicit is not None:
            return explicit
        if generic is not None:
            return generic
        return _fee_for_price(row.get("no_ask"))
    return generic or 0.0


def signal_key(row: dict[str, Any]) -> str:
    return "|".join(str(row.get(k) or "") for k in ("strategy", "event_id", "ticker", "side"))


def complete_ladder(group: list[dict[str, Any]]) -> bool:
    shapes = {str(row.get("shape") or "") for row in group}
    return len(group) >= 3 and "below" in shapes and "above" in shapes and shapes <= {"below", "bucket", "above"}


def ladder_signals(group: list[dict[str, Any]], snapshot: str, min_locked_edge: float) -> list[dict[str, Any]]:
    if not complete_ladder(group):
        return []
    event_id = str(group[0].get("event_id") or "")
    station = str(group[0].get("station") or "")
    settlement_date = group[0].get("settlement_date")
    out: list[dict[str, Any]] = []
    yes_costs = [(D(row.get("yes_ask")), fee(row, "YES")) for row in group]
    if all(ask is not None for ask, _ in yes_costs):
        total = sum(float(ask) + f for ask, f in yes_costs if ask is not None)
        edge = 1.0 - total
        if edge >= min_locked_edge:
            out.append({
                "snapshot_time": snapshot, "strategy": "LADDER_YES_UNDERROUND_V1",
                "event_id": event_id, "ticker": event_id + "::ALL_YES", "side": "BASKET_YES",
                "entry_ask": total, "gross_edge": edge, "net_edge_after_fee": edge,
                "station": station, "settlement_date": settlement_date,
                "reasoning": f"Complete exhaustive ladder costs {total:.3f} after quoted fees for fixed 1.000 payout.",
                "legs": len(group), "live_order_submission": False,
            })
    no_costs = [(D(row.get("no_ask")), fee(row, "NO")) for row in group]
    if all(ask is not None for ask, _ in no_costs):
        total = sum(float(ask) + f for ask, f in no_costs if ask is not None)
        payout = float(len(group) - 1)
        edge = payout - total
        if edge >= min_locked_edge:
            out.append({
                "snapshot_time": snapshot, "strategy": "LADDER_NO_UNDERROUND_V1",
                "event_id": event_id, "ticker": event_id + "::ALL_NO", "side": "BASKET_NO",
                "entry_ask": total, "gross_edge": edge, "net_edge_after_fee": edge,
                "station": station, "settlement_date": settlement_date,
                "reasoning": f"Complete exhaustive NO basket costs {total:.3f} after quoted fees for fixed {payout:.3f} payout.",
                "legs": len(group), "live_order_submission": False,
            })
    return out


def monotonic_signals(group: list[dict[str, Any]], snapshot: str, basis_margin_f: float,
                      max_price: float, min_price_gap: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in group:
        high = D(row.get("high_so_far_f"))
        lower = D(row.get("lower"))
        upper = D(row.get("upper"))
        if high is None:
            continue
        shape = str(row.get("shape") or "")
        side: str | None = None
        reason: str | None = None
        if shape in {"bucket", "below"} and upper is not None and high >= upper + basis_margin_f:
            side = "NO"
            reason = f"Observed high {high:.1f}F is >= upper {upper:.1f}F + {basis_margin_f:.1f}F source-basis buffer."
        elif shape == "above" and lower is not None and high >= lower + basis_margin_f:
            side = "YES"
            reason = f"Observed high {high:.1f}F is >= threshold {lower:.1f}F + {basis_margin_f:.1f}F source-basis buffer."
        if side is None:
            continue
        ask = D(row.get("yes_ask" if side == "YES" else "no_ask"))
        if ask is None or ask > max_price:
            continue
        gap = 1.0 - ask - fee(row, side)
        if gap < min_price_gap:
            continue
        out.append({
            "snapshot_time": snapshot, "strategy": "MONOTONIC_HIGH_LOCK_V1",
            "event_id": row.get("event_id"), "ticker": row.get("ticker"), "side": side,
            "entry_ask": ask, "gross_edge": 1.0 - ask, "net_edge_after_fee": gap,
            "station": row.get("station"), "settlement_date": row.get("settlement_date"),
            "latest_temp_f": D(row.get("latest_temp_f")), "high_so_far_f": high,
            "minutes_since_high": D(row.get("minutes_since_high")),
            "drop_from_high_f": D(row.get("drop_from_high_f")),
            "slope_15m_f_per_min": D(row.get("slope_15m_f_per_min")),
            "source_family": row.get("source_family"), "basis_margin_f": basis_margin_f,
            "reasoning": reason + " Daily high is monotonic; signal remains shadow because observation and settlement sources may differ.",
            "live_order_submission": False,
        })
    return out


def run_cycle(args: argparse.Namespace, state: dict[str, Any], health: StrategyHealth) -> int:
    snapshot, rows = catalog_snapshot(args.catalog)
    enriched_snapshot, enriched_rows = latest_snapshot(args.contract_history)
    enriched_by_ticker = {str(r.get("ticker") or ""): r for r in enriched_rows}
    for row in rows:
        extra = enriched_by_ticker.get(str(row.get("ticker") or ""))
        if extra:
            for key in ("station", "settlement_date", "latest_temp_f", "high_so_far_f", "minutes_since_high", "drop_from_high_f", "slope_15m_f_per_min"):
                if extra.get(key) is not None:
                    row[key] = extra.get(key)
    if not snapshot or not rows:
        print("alpha_shadow no_snapshot", flush=True)
        health.report("ERROR", "NO_CATALOG_SNAPSHOT")
        return 0
    snap_dt = datetime.fromisoformat(snapshot.replace("Z", "+00:00")).astimezone(timezone.utc)
    age = (datetime.now(timezone.utc) - snap_dt).total_seconds()
    if age > args.max_snapshot_age_seconds:
        print(f"alpha_shadow stale_snapshot age_s={age:.1f}", flush=True)
        health.report("ERROR", "STALE_CONTRACT_SNAPSHOT", age_seconds=age)
        return 0
    current_input_key = input_snapshot_key(snapshot, enriched_snapshot)
    if state.get("last_input_snapshot") == current_input_key:
        print(f"alpha_shadow duplicate_snapshot key={current_input_key}", flush=True)
        return 0
    by_event: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        event_id = str(row.get("event_id") or "")
        if event_id:
            by_event.setdefault(event_id, []).append(row)
    if len(by_event) < args.minimum_event_coverage:
        health.report(
            "DEGRADED", "UNIVERSE_COVERAGE_INCOMPLETE", snapshot_time=snapshot,
            events=len(by_event), required_events=args.minimum_event_coverage,
            input=str(args.catalog),
        )
        print(
            f"alpha_shadow coverage_fail events={len(by_event)} required={args.minimum_event_coverage}",
            flush=True,
        )
        return 0
    candidates: list[dict[str, Any]] = []
    for group in by_event.values():
        candidates.extend(ladder_signals(group, snapshot, args.min_locked_edge))
        candidates.extend(monotonic_signals(group, snapshot, args.basis_margin_f, args.max_lock_price, args.min_lock_price_gap))
    emitted = 0
    seen = state.setdefault("seen", {})
    for candidate in candidates:
        key = signal_key(candidate)
        if key in seen:
            continue
        append_jsonl(args.signals_output, candidate)
        seen[key] = {"signal_time": snapshot, "entry_ask": candidate.get("entry_ask")}
        emitted += 1
        print("LIVE_ALPHA_SIGNAL " + json.dumps(candidate, sort_keys=True), flush=True)
    state["last_snapshot"] = snapshot
    state["last_input_snapshot"] = current_input_key
    health.report(
        "OK", "CYCLE_OK", snapshot_time=snapshot, events=len(by_event),
        candidates=len(candidates), emitted=emitted, total_seen=len(seen),
    )
    save_state(args.state, state)
    print(f"alpha_shadow snapshot={snapshot} events={len(by_event)} candidates={len(candidates)} emitted={emitted} total_seen={len(seen)}", flush=True)
    return emitted


def main() -> int:
    p = argparse.ArgumentParser(description="Read-only structural weather alpha shadow monitor")
    p.add_argument("--contract-history", type=Path, default=Path("/data/weather/live/weather_company_contract_history.jsonl"))
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/status/kalshi_all_weather_live_catalog.json"))
    p.add_argument("--signals-output", type=Path, default=Path("/data/weather/live/live_alpha_signals.jsonl"))
    p.add_argument("--state", type=Path, default=Path("/data/weather/live/live_alpha_state.json"))
    p.add_argument("--loop-seconds", type=int, default=30)
    p.add_argument("--max-snapshot-age-seconds", type=int, default=900)
    p.add_argument("--min-locked-edge", type=float, default=0.02)
    p.add_argument("--basis-margin-f", type=float, default=2.0)
    p.add_argument("--max-lock-price", type=float, default=0.95)
    p.add_argument("--min-lock-price-gap", type=float, default=0.03)
    p.add_argument("--minimum-event-coverage", type=int, default=40)
    p.add_argument("--once", action="store_true")
    args = p.parse_args()
    state = load_state(args.state)
    health = StrategyHealth("live_structural_alpha_shadow")
    print("mode=LIVE_ALPHA_SHADOW live_order_submission=false strategies=LADDER_UNDERROUND,MONOTONIC_HIGH_LOCK", flush=True)
    while True:
        try:
            run_cycle(args, state, health)
        except KeyboardInterrupt:
            save_state(args.state, state)
            raise
        except Exception as exc:
            print(f"alpha_shadow_error={type(exc).__name__}:{exc}", flush=True)
            health.report("ERROR", f"{type(exc).__name__}:{exc}")
        if args.once:
            break
        time.sleep(max(10, args.loop_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
