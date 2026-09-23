from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.backtest.causal_settlement import (
    TimedTemperature,
    empirical_settlement_posterior,
    lock_gate,
    surface_state,
)
from weather_alpha.settlement.stations import station_clock
from weather_alpha.settlement.reconstruction import local_standard_settlement_window


def D(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Decimal(text)


def _parse_dt(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _table(rows: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows) if rows else pa.table({"contract_id": pa.array([], type=pa.string())})


def _load_asos_month(raw_dir: Path, station: str, year: int, month: int) -> list[TimedTemperature]:
    path = raw_dir / f"{station}_{year}_{month:02d}.jsonl"
    if not path.exists():
        return []
    out: list[TimedTemperature] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("station") or "").upper() != station.upper():
            continue
        temp = D(row.get("temperature_f"))
        ts = row.get("valid_time")
        if temp is None or not ts:
            continue
        out.append(TimedTemperature(_parse_dt(ts), temp))
    return sorted(out, key=lambda x: x.valid_time)


def main() -> int:
    p = argparse.ArgumentParser(description="Build a no-lookahead settlement-mapping posterior on historical PMXT snapshots")
    p.add_argument("--overlay", type=Path, default=Path("/data/weather/results/settlement_reconstruction/qc_pmxt_money_overlay.parquet"))
    p.add_argument("--history", type=Path, default=Path("/data/weather/results/settlement_reconstruction/cli_asos_history.parquet"))
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_resolved.parquet"))
    p.add_argument("--asos-raw-dir", type=Path, default=Path("/data/weather/raw/asos/history_months"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/causal_settlement_pmxt.parquet"))
    p.add_argument("--minimum-prior-samples", type=int, default=20)
    p.add_argument("--min-minutes-since-high", type=Decimal, default=Decimal("60"))
    p.add_argument("--min-drop-from-high-f", type=Decimal, default=Decimal("1.0"))
    p.add_argument("--max-positive-slope", type=Decimal, default=Decimal("0.02"))
    p.add_argument("--minimum-edge", type=Decimal, default=Decimal("0.05"), help="gross probability edge; no fee deduction unless separately modeled")
    args = p.parse_args()

    overlay = pq.read_table(args.overlay).to_pylist()
    history = pq.read_table(args.history).to_pylist()
    catalog_rows = [r for r in pq.read_table(args.catalog).to_pylist() if r.get("status") == "EXACT"]
    catalog = {str(r.get("contract_id") or ""): r for r in catalog_rows}

    history_by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        if row.get("qc_status") != "PASS":
            continue
        basis = D(row.get("cli_minus_asos_high_f"))
        if basis is None:
            continue
        history_by_station[str(row.get("station") or "").upper()].append(row)

    asos_cache: dict[tuple[str, int, int], list[TimedTemperature]] = {}
    rows: list[dict[str, Any]] = []
    status = Counter()

    for snap in overlay:
        ticker = str(snap.get("contract_id") or "")
        contract = catalog.get(ticker)
        if contract is None:
            status["MISSING_CONTRACT"] += 1
            continue
        station = str(contract.get("station") or "").upper()
        day_text = str(contract.get("settlement_date") or "")[:10]
        if not station or len(day_text) != 10:
            status["BAD_CONTRACT_TRUTH"] += 1
            continue
        day = date.fromisoformat(day_text)
        book_time = _parse_dt(snap.get("book_time"))

        clock = station_clock(station)
        window = local_standard_settlement_window(clock, day)
        if not (window.start_utc <= book_time < window.end_utc):
            status["OUTSIDE_SETTLEMENT_WINDOW"] += 1
            continue

        key = (station, book_time.year, book_time.month)
        if key not in asos_cache:
            asos_cache[key] = _load_asos_month(args.asos_raw_dir, *key)
        month_obs = asos_cache[key]
        event_obs = [o for o in month_obs if window.start_utc <= o.valid_time <= book_time]
        state = surface_state(event_obs, as_of=book_time)

        locked, lock_reasons = lock_gate(
            state,
            min_minutes_since_high=args.min_minutes_since_high,
            min_drop_from_high_f=args.min_drop_from_high_f,
            max_positive_slope_f_per_min=args.max_positive_slope,
        )

        prior_rows = [
            r for r in history_by_station.get(station, [])
            if str(r.get("settlement_date") or "")[:10] < day_text
        ]
        prior_basis = [D(r.get("cli_minus_asos_high_f")) for r in prior_rows]
        prior_basis = [x for x in prior_basis if x is not None]

        lower = D(contract.get("lower"))
        upper = D(contract.get("upper"))
        posterior = None
        if state.high_so_far_f is not None:
            posterior = empirical_settlement_posterior(
                high_so_far_f=state.high_so_far_f,
                prior_basis_samples_f=prior_basis,
                shape=str(contract.get("shape") or ""),
                lower=lower,
                upper=upper,
                minimum_samples=args.minimum_prior_samples,
            )

        p_yes = None if posterior is None else posterior.probability_yes
        yes_ask = D(snap.get("yes_best_ask"))
        no_ask = D(snap.get("no_best_ask"))
        yes_edge = None if p_yes is None or yes_ask is None else p_yes - yes_ask
        no_prob = None if p_yes is None else Decimal("1") - p_yes
        no_edge = None if no_prob is None or no_ask is None else no_prob - no_ask

        chosen_side = None
        chosen_edge = None
        chosen_price = None
        if locked and p_yes is not None:
            candidates = []
            if yes_edge is not None:
                candidates.append((yes_edge, "YES", yes_ask))
            if no_edge is not None:
                candidates.append((no_edge, "NO", no_ask))
            if candidates:
                best = max(candidates, key=lambda x: x[0])
                if best[0] >= args.minimum_edge:
                    chosen_edge, chosen_side, chosen_price = best

        if p_yes is None:
            status["INSUFFICIENT_PRIOR"] += 1
        elif not locked:
            status["LOCK_GATE_FAIL"] += 1
        elif chosen_side is None:
            status["NO_EDGE"] += 1
        else:
            status["CANDIDATE"] += 1

        rows.append({
            "contract_id": ticker,
            "station": station,
            "settlement_date": day_text,
            "book_time": book_time.isoformat(),
            "shape": contract.get("shape"),
            "lower": contract.get("lower"),
            "upper": contract.get("upper"),
            "high_so_far_f": None if state.high_so_far_f is None else str(state.high_so_far_f),
            "latest_temp_f": None if state.latest_temp_f is None else str(state.latest_temp_f),
            "minutes_since_high": None if state.minutes_since_high is None else str(state.minutes_since_high),
            "drop_from_high_f": None if state.drop_from_high_f is None else str(state.drop_from_high_f),
            "slope_15m_f_per_min": None if state.slope_15m_f_per_min is None else str(state.slope_15m_f_per_min),
            "surface_observations_used": state.observations_used,
            "lock_gate_pass": locked,
            "lock_gate_reasons": list(lock_reasons),
            "prior_basis_samples": len(prior_basis),
            "posterior_yes_probability": None if p_yes is None else str(p_yes),
            "yes_best_ask": None if yes_ask is None else str(yes_ask),
            "no_best_ask": None if no_ask is None else str(no_ask),
            "gross_yes_edge": None if yes_edge is None else str(yes_edge),
            "gross_no_edge": None if no_edge is None else str(no_edge),
            "candidate_side": chosen_side,
            "candidate_gross_edge": None if chosen_edge is None else str(chosen_edge),
            "candidate_price": None if chosen_price is None else str(chosen_price),
            "official_yes_winner_evaluation_only": snap.get("official_yes_winner"),
            "causal_label": "CAUSAL_INPUTS_ONLY_POSTERIOR_USES_STRICTLY_PRIOR_PASS_DAYS",
            "model_warning": "POSTERIOR_MODELS_SETTLEMENT_BASIS_ONLY; FUTURE_INTRADAY_HEATING_NOT_MODELED; LOCK_GATE_REQUIRED",
            "fee_warning": "GROSS_EDGE_ONLY_FEES_NOT_DEDUCTED",
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(_table(rows), args.output)
    print(f"snapshot_rows={len(rows)} status_counts={dict(status)}")
    print(f"candidates={sum(1 for r in rows if r.get('candidate_side'))}")
    print("causality=STRICTLY_PRIOR_SETTLEMENT_BASIS_PLUS_ASOS_AT_OR_BEFORE_BOOK_TIME")
    print("warning=FUTURE_INTRADAY_HEATING_NOT_MODELED_LOCK_GATE_IS_REQUIRED")
    print("fee_status=NOT_DEDUCTED_GROSS_ONLY")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
