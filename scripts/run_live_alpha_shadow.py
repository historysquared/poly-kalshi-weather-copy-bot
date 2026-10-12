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
        event_members: dict[str, list[str]] = {}
        for market in series.get("markets") or []:
            if isinstance(market, dict):
                event_members.setdefault(str(market.get("event_ticker") or ""), []).append(str(market.get("ticker") or ""))
        for market in series.get("markets") or []:
            if not isinstance(market, dict):
                continue
            strike = str(market.get("strike_type") or "").lower()
            shape = {"between": "bucket", "less": "below", "greater": "above"}.get(strike, strike)
            rows.append({
                "snapshot_time": generated,
                "event_id": market.get("event_ticker"),
                "ticker": market.get("ticker"),
                "series": series.get("ticker"),
                "shape": shape,
                "lower": market.get("floor_strike"),
                "upper": market.get("cap_strike"),
                "yes_ask": None,
                "no_ask": None,
                "estimated_yes_taker_fee_per_contract": None,
                "estimated_no_taker_fee_per_contract": None,
                "source_family": source_family,
                "settlement_date": None,
                "event_contract_ids": event_members.get(str(market.get("event_ticker") or ""), []),
                # Only an independently verified catalog record may supply this.
                # Never infer precision/rounding/inclusivity from a ticker/title.
                "settlement_semantics": market.get("settlement_semantics"),
            })
    return generated, rows


def load_live_bbo(path: Path) -> tuple[str | None, dict[str, dict[str, Any]]]:
    if not path.exists():
        return None, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, {}
    generated = str(payload.get("generated_at") or "") or None
    raw_quotes = payload.get("quotes") if isinstance(payload, dict) else None
    quotes = {
        str(ticker): row
        for ticker, row in (raw_quotes or {}).items()
        if ticker and isinstance(row, dict)
    }
    return generated, quotes


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


def input_snapshot_key(catalog_snapshot: str, bbo_snapshot: str,
                       enriched_snapshot: str | None) -> str:
    return f"{catalog_snapshot}|{bbo_snapshot}|{enriched_snapshot or ''}"


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


def ladder_validation_reason(group: list[dict[str, Any]]) -> str:
    if len(group) < 3:
        return "TOO_FEW_LEGS"
    tickers = [row.get("ticker") for row in group]
    if any(not isinstance(t, str) or not t for t in tickers) or len(set(tickers)) != len(tickers):
        return "DUPLICATE_OR_MISSING_CONTRACT"
    expected = group[0].get("event_contract_ids")
    if (not isinstance(expected, list) or any(not isinstance(t, str) or not t for t in expected)
            or len(set(expected)) != len(expected) or set(expected) != set(tickers)):
        return "CANONICAL_MEMBERSHIP_INCOMPLETE"
    event = group[0].get("event_id")
    if not event or any(row.get("event_id") != event or row.get("event_contract_ids") != expected for row in group):
        return "MIXED_EVENT_OR_MEMBERSHIP"
    semantics = [row.get("settlement_semantics") for row in group]
    basis_fields = ("source", "station", "date", "measurement", "window", "unit", "rounding", "value_domain")
    for meta in semantics:
        if (not isinstance(meta, dict) or meta.get("mapping_status") != "EXACT"
                or not isinstance(meta.get("evidence"), str) or not meta["evidence"].strip()
                or any(not isinstance(meta.get(k), str) or not meta[k].strip() for k in basis_fields)):
            return "SETTLEMENT_SEMANTICS_UNVERIFIED"
        if type(meta.get("lower_inclusive")) is not bool or type(meta.get("upper_inclusive")) is not bool:
            return "INCLUSIVITY_UNVERIFIED"
    basis = tuple(semantics[0][k] for k in basis_fields)
    if any(tuple(meta[k] for k in basis_fields) != basis for meta in semantics):
        return "MIXED_SETTLEMENT_BASIS"
    domain = semantics[0]["value_domain"]
    if (semantics[0]["unit"] != "F" or
            (domain, semantics[0]["rounding"]) not in {("INTEGER", "WHOLE_DEGREE"), ("CONTINUOUS", "NONE")}):
        return "UNSUPPORTED_PRECISION"
    intervals = []
    shapes = []
    for row, meta in zip(group, semantics):
        shape = row.get("shape")
        shapes.append(shape)
        try:
            lo = None if row.get("lower") is None else Decimal(str(row["lower"]))
            hi = None if row.get("upper") is None else Decimal(str(row["upper"]))
        except Exception:
            return "INVALID_BOUND"
        if any(x is not None and not x.is_finite() for x in (lo, hi)):
            return "INVALID_BOUND"
        if ((shape == "below" and (lo is not None or hi is None))
                or (shape == "above" and (lo is None or hi is not None))
                or (shape == "bucket" and (lo is None or hi is None))
                or shape not in {"below", "bucket", "above"}):
            return "INVALID_SHAPE_BOUNDS"
        li, ui = meta["lower_inclusive"], meta["upper_inclusive"]
        if domain == "INTEGER":
            if any(x is not None and x != x.to_integral_value() for x in (lo, hi)):
                return "NONINTEGER_BOUND"
            if lo is not None and not li: lo += 1
            if hi is not None and not ui: hi -= 1
            li = ui = True
        if lo is not None and hi is not None and (lo > hi or (lo == hi and not (li and ui))):
            return "EMPTY_OR_REVERSED_INTERVAL"
        intervals.append((lo, hi, li, ui))
    if shapes.count("below") != 1 or shapes.count("above") != 1 or shapes.count("bucket") < 1:
        return "MISSING_OR_DUPLICATE_TAIL"
    intervals.sort(key=lambda x: (x[0] is not None, x[0] or Decimal(0)))
    if intervals[0][0] is not None or intervals[-1][1] is not None:
        return "UNBOUNDED_TAILS_MISSING"
    for left, right in zip(intervals, intervals[1:]):
        if left[1] is None or right[0] is None:
            return "OVERLAPPING_TAILS"
        if domain == "INTEGER":
            if left[1] + 1 != right[0]:
                return "GAP_OR_OVERLAP"
        elif left[1] != right[0] or left[3] == right[2]:
            return "GAP_OR_OVERLAP"
    return "EXACT_PARTITION"


def complete_ladder(group: list[dict[str, Any]]) -> bool:
    return ladder_validation_reason(group) == "EXACT_PARTITION"


def ladder_signals(group: list[dict[str, Any]], snapshot: str, min_locked_edge: float) -> list[dict[str, Any]]:
    if not complete_ladder(group):
        return []
    event_id = str(group[0].get("event_id") or "")
    station = str(group[0].get("station") or "")
    settlement_date = group[0].get("settlement_date")
    out: list[dict[str, Any]] = []
    yes_costs = [(D(row.get("yes_ask")), fee(row, "YES")) for row in group]
    if all(ask is not None and 0 <= ask <= 1 and 0 <= f < float("inf") for ask, f in yes_costs):
        total = sum(float(ask) + f for ask, f in yes_costs if ask is not None)
        edge = 1.0 - total
        if edge >= min_locked_edge:
            out.append({
                "snapshot_time": snapshot, "strategy": "LADDER_YES_UNDERROUND_V1",
                "event_id": event_id, "ticker": event_id + "::ALL_YES", "side": "BASKET_YES",
                "entry_ask": total, "gross_edge": edge, "net_edge_after_fee": edge,
                "station": station, "settlement_date": settlement_date,
                "reasoning": f"Verified exhaustive ladder (normal binary settlement) costs {total:.3f} after quoted fees for fixed 1.000 payout.",
                "legs": len(group), "live_order_submission": False,
            })
    no_costs = [(D(row.get("no_ask")), fee(row, "NO")) for row in group]
    if all(ask is not None and 0 <= ask <= 1 and 0 <= f < float("inf") for ask, f in no_costs):
        total = sum(float(ask) + f for ask, f in no_costs if ask is not None)
        payout = float(len(group) - 1)
        edge = payout - total
        if edge >= min_locked_edge:
            out.append({
                "snapshot_time": snapshot, "strategy": "LADDER_NO_UNDERROUND_V1",
                "event_id": event_id, "ticker": event_id + "::ALL_NO", "side": "BASKET_NO",
                "entry_ask": total, "gross_edge": edge, "net_edge_after_fee": edge,
                "station": station, "settlement_date": settlement_date,
                "reasoning": f"Verified exhaustive NO basket (normal binary settlement) costs {total:.3f} after quoted fees for fixed {payout:.3f} payout.",
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


def timestamp_age_seconds(value: Any, now: datetime) -> float | None:
    """Unknown, timezone-less and future timestamps are not fresh data."""
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return None
        age = (now - stamp.astimezone(timezone.utc)).total_seconds()
        return age if age >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def quote_is_fresh(quote: dict[str, Any], now: datetime, max_age_seconds: float) -> bool:
    age = timestamp_age_seconds(quote.get("local_receive_timestamp"), now)
    return age is not None and age <= max_age_seconds


def run_cycle(args: argparse.Namespace, state: dict[str, Any], health: StrategyHealth) -> int:
    catalog_time, rows = catalog_snapshot(args.catalog)
    bbo_time, bbo_quotes = load_live_bbo(args.bbo)
    if not catalog_time or not rows:
        print("alpha_shadow no_catalog_snapshot", flush=True)
        health.report("ERROR", "NO_CATALOG_SNAPSHOT")
        return 0
    if not bbo_time or not bbo_quotes:
        print("alpha_shadow no_bbo_snapshot", flush=True)
        health.report("ERROR", "NO_LIVE_BBO_SNAPSHOT")
        return 0

    now = datetime.now(timezone.utc)
    catalog_age = timestamp_age_seconds(catalog_time, now)
    bbo_age = timestamp_age_seconds(bbo_time, now)
    if catalog_age is None or bbo_age is None:
        health.report("ERROR", "INVALID_INPUT_TIMESTAMP")
        return 0
    if catalog_age > args.max_catalog_age_seconds:
        print(f"alpha_shadow stale_catalog age_s={catalog_age:.1f}", flush=True)
        health.report("ERROR", "STALE_MARKET_CATALOG", age_seconds=catalog_age)
        return 0
    if bbo_age > args.max_snapshot_age_seconds:
        print(f"alpha_shadow stale_bbo age_s={bbo_age:.1f}", flush=True)
        health.report("ERROR", "STALE_LIVE_BBO", age_seconds=bbo_age)
        return 0

    enriched_snapshot, enriched_rows = latest_snapshot(args.contract_history)
    surface_fresh = False
    if enriched_snapshot:
        surface_age = timestamp_age_seconds(enriched_snapshot, now)
        surface_fresh = surface_age is not None and surface_age <= args.max_surface_age_seconds
    enriched_by_ticker = {
        str(r.get("ticker") or ""): r for r in enriched_rows
    } if surface_fresh else {}

    # Drop the whole event if any leg is missing/stale, rather than treating
    # a reduced set of available prices as a complete basket.
    invalid_events = {
        str(row.get("event_id") or "") for row in rows
        if not quote_is_fresh(bbo_quotes.get(str(row.get("ticker") or "")) or {},
                              now, args.max_snapshot_age_seconds)
    }
    priced_rows: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("event_id") or "") in invalid_events:
            continue
        ticker = str(row.get("ticker") or "")
        quote = bbo_quotes.get(ticker)
        if not quote:
            continue
        yes_ask = D(quote.get("yes_ask"))
        no_ask = D(quote.get("no_ask"))
        if yes_ask is None and no_ask is None:
            continue
        row["snapshot_time"] = bbo_time
        row["yes_ask"] = yes_ask
        row["no_ask"] = no_ask
        row["estimated_yes_taker_fee_per_contract"] = _fee_for_price(yes_ask)
        row["estimated_no_taker_fee_per_contract"] = _fee_for_price(no_ask)
        row["quote_exchange_time"] = quote.get("exchange_time")
        row["quote_receive_time"] = quote.get("local_receive_timestamp")
        extra = enriched_by_ticker.get(ticker)
        if extra:
            for key in ("station", "settlement_date", "latest_temp_f", "high_so_far_f",
                        "minutes_since_high", "drop_from_high_f", "slope_15m_f_per_min"):
                if extra.get(key) is not None:
                    row[key] = extra.get(key)
        priced_rows.append(row)

    current_input_key = input_snapshot_key(catalog_time, bbo_time, enriched_snapshot if surface_fresh else None)
    if state.get("last_input_snapshot") == current_input_key:
        print(f"alpha_shadow duplicate_snapshot key={current_input_key}", flush=True)
        return 0

    by_event: dict[str, list[dict[str, Any]]] = {}
    for row in priced_rows:
        event_id = str(row.get("event_id") or "")
        if event_id:
            by_event.setdefault(event_id, []).append(row)
    if len(by_event) < args.minimum_event_coverage:
        health.report(
            "DEGRADED", "UNIVERSE_COVERAGE_INCOMPLETE", snapshot_time=bbo_time,
            events=len(by_event), required_events=args.minimum_event_coverage,
            bbo_quotes=len(bbo_quotes), invalid_quote_events=len(invalid_events), input=str(args.bbo),
        )
        print(
            f"alpha_shadow coverage_fail events={len(by_event)} required={args.minimum_event_coverage}",
            flush=True,
        )
        return 0

    candidates: list[dict[str, Any]] = []
    ladder_failures: dict[str, int] = {}
    for group in by_event.values():
        ladder_reason = ladder_validation_reason(group)
        if ladder_reason != "EXACT_PARTITION":
            ladder_failures[ladder_reason] = ladder_failures.get(ladder_reason, 0) + 1
        candidates.extend(ladder_signals(group, bbo_time, args.min_locked_edge))
        candidates.extend(monotonic_signals(group, bbo_time, args.basis_margin_f, args.max_lock_price, args.min_lock_price_gap))
    emitted = 0
    seen = state.setdefault("seen", {})
    for candidate in candidates:
        key = signal_key(candidate)
        if key in seen:
            continue
        append_jsonl(args.signals_output, candidate)
        seen[key] = {"signal_time": bbo_time, "entry_ask": candidate.get("entry_ask")}
        emitted += 1
        print("LIVE_ALPHA_SIGNAL " + json.dumps(candidate, sort_keys=True), flush=True)

    state["last_snapshot"] = bbo_time
    state["last_input_snapshot"] = current_input_key
    status = "OK" if surface_fresh and not invalid_events and not ladder_failures else "DEGRADED"
    reason = ("INVALID_OR_STALE_QUOTE_EVENTS" if invalid_events else
              "LADDER_VALIDATION_INCOMPLETE" if ladder_failures else
              "CYCLE_OK" if surface_fresh else "SURFACE_ENRICHMENT_STALE_OR_MISSING")
    health.report(
        status, reason, snapshot_time=bbo_time, catalog_time=catalog_time,
        surface_snapshot=enriched_snapshot, surface_fresh=surface_fresh,
        events=len(by_event), candidates=len(candidates), emitted=emitted,
        total_seen=len(seen), bbo_quotes=len(bbo_quotes), invalid_quote_events=len(invalid_events),
        ladder_validation_failures=ladder_failures,
    )
    save_state(args.state, state)
    print(
        f"alpha_shadow snapshot={bbo_time} events={len(by_event)} candidates={len(candidates)} "
        f"emitted={emitted} total_seen={len(seen)} surface_fresh={surface_fresh}",
        flush=True,
    )
    return emitted


def main() -> int:
    p = argparse.ArgumentParser(description="Read-only structural weather alpha shadow monitor")
    p.add_argument("--contract-history", type=Path, default=Path("/data/weather/live/weather_company_contract_history.jsonl"))
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/status/kalshi_all_weather_live_catalog.json"))
    p.add_argument("--bbo", type=Path, default=Path("/data/weather/status/kalshi_all_weather_bbo.json"))
    p.add_argument("--signals-output", type=Path, default=Path("/data/weather/live/live_alpha_signals.jsonl"))
    p.add_argument("--state", type=Path, default=Path("/data/weather/live/live_alpha_state.json"))
    p.add_argument("--loop-seconds", type=int, default=30)
    p.add_argument("--max-snapshot-age-seconds", type=int, default=30)
    p.add_argument("--max-catalog-age-seconds", type=int, default=25200)
    p.add_argument("--max-surface-age-seconds", type=int, default=180)
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
