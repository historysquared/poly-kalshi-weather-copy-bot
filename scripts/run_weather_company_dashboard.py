from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from weather_alpha.backtest.causal_settlement import TimedTemperature, lock_gate, surface_state
from weather_alpha.providers.live_surface import fetch_live_temperature_series
from weather_alpha.settlement.reconstruction import local_standard_settlement_window
from weather_alpha.settlement.stations import station_clock
import scripts.run_experimental_weather_company_paper as paper


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_json(path: Path, obj) -> None:
    atomic_text(path, json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n")


def append_jsonl(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, sort_keys=True, default=str) + "\n")


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"traded_events": {}, "pending": []}


def load_l2_books(path: Path) -> dict[str, dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    books = payload.get("books") if isinstance(payload, dict) else None
    return books if isinstance(books, dict) else {}


def decision_reason(*, traded: bool, pending: bool, lock_pass: bool, edge: Decimal, net_edge: Decimal,
                    ask: Decimal, min_edge: Decimal, price_floor: Decimal,
                    obs_age_min: Decimal | None) -> str:
    if traded:
        return "ALREADY_TRADED_EVENT"
    if pending:
        return "PENDING_DELAYED_FILL"
    if obs_age_min is None:
        return "NO_LIVE_OBSERVATION"
    if obs_age_min > Decimal("100"):
        return "STALE_OBSERVATION"
    if not lock_pass:
        return "LOCK_GATE_FAIL"
    if ask < price_floor:
        return "PRICE_BELOW_FLOOR"
    if edge < min_edge:
        return "EDGE_BELOW_MINIMUM"
    if net_edge <= Decimal("0"):
        return "NEGATIVE_AFTER_FEE"
    return "PAPER_TRADE_ELIGIBLE"


async def live_surface(station: str, start, end):
    rows = await fetch_live_temperature_series(station, start, end, max_age_minutes=100)
    timed = [TimedTemperature(r.valid_time, Decimal(str(r.temperature_f))) for r in rows]
    return rows, surface_state(timed, as_of=end)


def fee_per_contract(price: Decimal) -> Decimal:
    return paper.kalshi_taker_fee(price, 1)


def bucket_label(shape: str, lower, upper) -> str:
    if shape == "bucket":
        return f"{lower}-{upper}"
    if shape == "above":
        return f">={lower}"
    if shape == "below":
        return f"<={upper}"
    return shape


def build_snapshot(args) -> dict:
    now = datetime.now(timezone.utc)
    contracts = paper.fetch_contracts([x.strip().upper() for x in args.series.split(",") if x.strip()])
    by_event: dict[str, list] = {}
    for c in contracts:
        by_event.setdefault(c.event_id, []).append(c)

    state = load_state(args.state)
    l2_books = load_l2_books(args.l2_latest)
    traded_events = state.get("traded_events", {}) or {}
    pending_events = {x.get("event_id") for x in state.get("pending", []) if isinstance(x, dict)}
    rows = []
    contract_rows = []

    for event_id, group in sorted(by_event.items()):
        c0 = group[0]
        window = local_standard_settlement_window(station_clock(c0.station), c0.day)
        seconds_remaining = max(0.0, (window.end_utc - now).total_seconds())
        row = {
            "snapshot_time": now.isoformat(),
            "event_id": event_id,
            "series": c0.series,
            "station": c0.station,
            "settlement_date": c0.day.isoformat(),
            "source_family": c0.source_family,
            "settlement_window_start_utc": window.start_utc.isoformat(),
            "settlement_window_end_utc": window.end_utc.isoformat(),
            "minutes_to_settlement_end": str(Decimal(str(seconds_remaining / 60.0))),
            "in_settlement_window": bool(window.start_utc <= now < window.end_utc),
        }
        if not row["in_settlement_window"]:
            row.update({"decision": "OUTSIDE_SETTLEMENT_WINDOW"})
            rows.append(row)
            continue

        try:
            obs_rows, surf = asyncio.run(live_surface(c0.station, window.start_utc, now))
            latest_obs = obs_rows[-1]
            row.update({
                "obs_source": latest_obs.source,
                "latest_obs_time": latest_obs.valid_time.isoformat(),
                "observation_count": len(obs_rows),
                "latest_temp_f": str(surf.latest_temp_f) if surf.latest_temp_f is not None else None,
                "high_so_far_f": str(surf.high_so_far_f) if surf.high_so_far_f is not None else None,
                "minutes_since_high": str(surf.minutes_since_high) if surf.minutes_since_high is not None else None,
                "drop_from_high_f": str(surf.drop_from_high_f) if surf.drop_from_high_f is not None else None,
                "slope_15m_f_per_min": str(surf.slope_15m_f_per_min) if surf.slope_15m_f_per_min is not None else None,
            })
            obs_age = Decimal(str((now - latest_obs.valid_time).total_seconds() / 60.0))
            row["obs_age_minutes"] = str(obs_age)
        except Exception as exc:
            row.update({"decision": "OBSERVATION_ERROR", "error": f"{type(exc).__name__}:{exc}"})
            rows.append(row)
            continue

        lock_pass, lock_reasons = lock_gate(
            surf,
            min_minutes_since_high=args.min_minutes_since_high,
            min_drop_from_high_f=args.min_drop_from_high_f,
            max_positive_slope_f_per_min=args.max_positive_slope,
        )
        row["lock_gate_pass"] = lock_pass
        row["lock_gate_reasons"] = list(lock_reasons)

        best = None
        for c in group:
            if surf.high_so_far_f is None:
                continue
            in_bucket = paper.contains(c.shape, c.lower, c.upper, surf.high_so_far_f)
            p_yes = paper.provisional_probability(
                in_bucket=in_bucket,
                lock_pass=lock_pass,
                drop_f=surf.drop_from_high_f,
                slope=surf.slope_15m_f_per_min,
            )
            p_no = Decimal("1") - p_yes
            choices = []
            if c.yes_ask is not None:
                yes_fee = fee_per_contract(c.yes_ask)
                choices.append((p_yes - c.yes_ask, p_yes - c.yes_ask - yes_fee, "YES", c.yes_ask, p_yes, yes_fee))
            if c.no_ask is not None:
                no_fee = fee_per_contract(c.no_ask)
                choices.append((p_no - c.no_ask, p_no - c.no_ask - no_fee, "NO", c.no_ask, p_no, no_fee))
            if not choices:
                continue
            edge, net_edge, side, ask, p_side, fee = max(choices, key=lambda x: x[1])
            l2 = l2_books.get(c.ticker, {})
            l2_prefix = "yes" if side == "YES" else "no"
            item = {
                "snapshot_time": now.isoformat(),
                "event_id": event_id,
                "series": c.series,
                "station": c.station,
                "settlement_date": c.day.isoformat(),
                "source_family": c.source_family,
                "ticker": c.ticker,
                "shape": c.shape,
                "lower": None if c.lower is None else str(c.lower),
                "upper": None if c.upper is None else str(c.upper),
                "bucket": bucket_label(c.shape, c.lower, c.upper),
                "high_so_far_f": row.get("high_so_far_f"),
                "latest_temp_f": row.get("latest_temp_f"),
                "latest_obs_time": row.get("latest_obs_time"),
                "obs_source": row.get("obs_source"),
                "obs_age_minutes": row.get("obs_age_minutes"),
                "minutes_to_settlement_end": row.get("minutes_to_settlement_end"),
                "minutes_since_high": row.get("minutes_since_high"),
                "drop_from_high_f": row.get("drop_from_high_f"),
                "slope_15m_f_per_min": row.get("slope_15m_f_per_min"),
                "lock_gate_pass": lock_pass,
                "lock_gate_reasons": list(lock_reasons),
                "side": side,
                "model_probability_side": str(p_side),
                "model_probability_yes": str(p_yes),
                "gross_edge": str(edge),
                "estimated_taker_fee_per_contract": str(fee),
                "net_edge_after_fee": str(net_edge),
                "entry_ask": str(ask),
                "yes_bid": None if c.yes_bid is None else str(c.yes_bid),
                "yes_ask": None if c.yes_ask is None else str(c.yes_ask),
                "no_bid": None if c.no_bid is None else str(c.no_bid),
                "no_ask": None if c.no_ask is None else str(c.no_ask),
                "in_bucket_now": in_bucket,
                "l2_available": bool(l2),
                "l2_best_bid": l2.get(f"{l2_prefix}_best_bid"),
                "l2_best_ask": l2.get(f"{l2_prefix}_best_ask"),
                "l2_spread": l2.get(f"{l2_prefix}_spread"),
                "l2_bid_depth_top5": l2.get(f"{l2_prefix}_bid_depth_top5"),
                "l2_ask_depth_top5": l2.get(f"{l2_prefix}_ask_depth_top5"),
                "l2_buy_vwap_1": l2.get(f"{l2_prefix}_buy_vwap_1"),
                "l2_buy_vwap_5": l2.get(f"{l2_prefix}_buy_vwap_5"),
                "l2_sequence": l2.get("sequence"),
                "model_status": "PROVISIONAL_UNCALIBRATED_WEATHER_COMPANY_FORWARD_PAPER",
                "live_order_submission": False,
            }
            contract_rows.append(item)
            if best is None or net_edge > Decimal(best["net_edge_after_fee"]):
                best = item

        if best is None:
            row["decision"] = "NO_EXECUTABLE_QUOTE"
            rows.append(row)
            continue

        row.update(best)
        row["event_traded"] = event_id in traded_events
        row["event_pending"] = event_id in pending_events
        row["decision"] = decision_reason(
            traded=row["event_traded"],
            pending=row["event_pending"],
            lock_pass=lock_pass,
            edge=Decimal(best["gross_edge"]),
            net_edge=Decimal(best["net_edge_after_fee"]),
            ask=Decimal(best["entry_ask"]),
            min_edge=args.minimum_edge,
            price_floor=args.price_floor,
            obs_age_min=Decimal(row["obs_age_minutes"]),
        )
        rows.append(row)

    eligible = sum(r.get("decision") == "PAPER_TRADE_ELIGIBLE" for r in rows)
    return {
        "generated_at": now.isoformat(),
        "mode": "EXPERIMENTAL_WEATHER_COMPANY_PAPER_DASHBOARD",
        "live_order_submission": False,
        "model_status": "PROVISIONAL_UNCALIBRATED_WEATHER_COMPANY_FORWARD_PAPER",
        "thresholds": {
            "price_floor": str(args.price_floor),
            "minimum_edge": str(args.minimum_edge),
            "min_minutes_since_high": str(args.min_minutes_since_high),
            "min_drop_from_high_f": str(args.min_drop_from_high_f),
            "max_positive_slope": str(args.max_positive_slope),
        },
        "counts": {
            "contracts": len(contracts),
            "events": len(by_event),
            "eligible": eligible,
            "traded_events": len(traded_events),
            "pending": len(state.get("pending", [])),
            "audited_contract_quotes": len(contract_rows),
        },
        "rows": rows,
        "contract_rows": contract_rows,
    }


def write_outputs(args, snap: dict) -> None:
    atomic_json(args.json_output, snap)
    append_jsonl(args.snapshot_history, snap)

    fields = [
        "snapshot_time", "event_id", "series", "station", "settlement_date", "source_family",
        "latest_obs_time", "obs_source", "obs_age_minutes", "observation_count", "latest_temp_f", "high_so_far_f",
        "minutes_to_settlement_end", "minutes_since_high", "drop_from_high_f", "slope_15m_f_per_min", "lock_gate_pass",
        "ticker", "bucket", "shape", "lower", "upper", "side", "model_probability_side",
        "model_probability_yes", "yes_bid", "yes_ask", "no_bid", "no_ask", "entry_ask",
        "gross_edge", "estimated_taker_fee_per_contract", "net_edge_after_fee",
        "event_pending", "event_traded", "decision"
    ]
    args.csv_output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.csv_output.with_suffix(args.csv_output.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(snap["rows"])
    tmp.replace(args.csv_output)

    contract_fields = [
        "snapshot_time", "event_id", "series", "station", "settlement_date", "source_family", "ticker", "bucket",
        "shape", "lower", "upper", "latest_temp_f", "high_so_far_f", "latest_obs_time", "obs_source", "obs_age_minutes",
        "minutes_to_settlement_end", "minutes_since_high", "drop_from_high_f", "slope_15m_f_per_min", "lock_gate_pass",
        "side", "model_probability_side", "model_probability_yes", "yes_bid", "yes_ask", "no_bid", "no_ask", "entry_ask",
        "gross_edge", "estimated_taker_fee_per_contract", "net_edge_after_fee", "in_bucket_now",
        "l2_available", "l2_best_bid", "l2_best_ask", "l2_spread", "l2_bid_depth_top5",
        "l2_ask_depth_top5", "l2_buy_vwap_1", "l2_buy_vwap_5", "l2_sequence",
    ]
    args.contract_csv_output.parent.mkdir(parents=True, exist_ok=True)
    ctmp = args.contract_csv_output.with_suffix(args.contract_csv_output.suffix + ".tmp")
    with ctmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=contract_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(snap["contract_rows"])
    ctmp.replace(args.contract_csv_output)

    for item in snap["contract_rows"]:
        append_jsonl(args.contract_snapshot_history, item)

    lines = []
    c = snap["counts"]
    lines.append(
        f"generated={snap['generated_at']} contracts={c['contracts']} events={c['events']} "
        f"eligible={c['eligible']} pending={c['pending']} traded={c['traded_events']} quotes={c['audited_contract_quotes']}"
    )
    lines.append("station event          temp high age  mins_left bucket      side p_yes ask  fee  netedge decision")
    for r in snap["rows"]:
        lines.append(
            f"{str(r.get('station','-')):7} {str(r.get('event_id','-')):14.14} "
            f"{str(r.get('latest_temp_f','-')):>5} {str(r.get('high_so_far_f','-')):>5} "
            f"{str(r.get('obs_age_minutes','-'))[:5]:>5} {str(r.get('minutes_to_settlement_end','-'))[:8]:>8} "
            f"{str(r.get('bucket','-'))[:10]:>10} {str(r.get('side','-')):>4} "
            f"{str(r.get('model_probability_yes','-'))[:5]:>5} {str(r.get('entry_ask','-'))[:5]:>5} "
            f"{str(r.get('estimated_taker_fee_per_contract','-'))[:5]:>5} "
            f"{str(r.get('net_edge_after_fee','-'))[:7]:>7} {r.get('decision','-')}"
        )
    atomic_text(args.text_output, "\n".join(lines) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description="Live read-only dashboard and snapshot recorder for Weather Company paper trading")
    p.add_argument("--series", default=",".join(paper.SERIES_STATION))
    p.add_argument("--state", type=Path, default=Path("/data/weather/live/weather_company_paper_state.json"))
    p.add_argument("--l2-latest", type=Path, default=Path("/data/weather/live/kalshi_l2_latest.json"))
    p.add_argument("--json-output", type=Path, default=Path("/data/weather/live/weather_company_dashboard.json"))
    p.add_argument("--csv-output", type=Path, default=Path("/data/weather/live/weather_company_dashboard.csv"))
    p.add_argument("--contract-csv-output", type=Path, default=Path("/data/weather/live/weather_company_contract_dashboard.csv"))
    p.add_argument("--text-output", type=Path, default=Path("/data/weather/live/weather_company_dashboard.txt"))
    p.add_argument("--snapshot-history", type=Path, default=Path("/data/weather/live/weather_company_dashboard_history.jsonl"))
    p.add_argument("--contract-snapshot-history", type=Path, default=Path("/data/weather/live/weather_company_contract_history.jsonl"))
    p.add_argument("--loop-seconds", type=int, default=60)
    p.add_argument("--price-floor", type=Decimal, default=Decimal("0.15"))
    p.add_argument("--minimum-edge", type=Decimal, default=Decimal("0.08"))
    p.add_argument("--min-minutes-since-high", type=Decimal, default=Decimal("60"))
    p.add_argument("--min-drop-from-high-f", type=Decimal, default=Decimal("1.0"))
    p.add_argument("--max-positive-slope", type=Decimal, default=Decimal("0.02"))
    args = p.parse_args()
    if args.loop_seconds < 10:
        raise SystemExit("--loop-seconds must be >=10")
    print("mode=WEATHER_COMPANY_PAPER_DASHBOARD live_order_submission=false snapshot_history=true", flush=True)
    while True:
        try:
            snap = build_snapshot(args)
            write_outputs(args, snap)
            c = snap["counts"]
            print(
                f"dashboard generated={snap['generated_at']} events={c['events']} eligible={c['eligible']} "
                f"pending={c['pending']} traded={c['traded_events']} quotes={c['audited_contract_quotes']}",
                flush=True,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"dashboard_error={type(exc).__name__}:{exc}", flush=True)
        time.sleep(args.loop_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
