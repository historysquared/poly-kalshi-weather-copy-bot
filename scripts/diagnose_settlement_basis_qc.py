from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.settlement.bracket_study import evaluate_contract_flip


def D(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Decimal(text)


def pct(n: int, d: int) -> float | None:
    return None if d == 0 else 100.0 * n / d


def mean(values: list[Decimal]) -> Decimal | None:
    return None if not values else sum(values, Decimal(0)) / Decimal(len(values))


def quantile(values: list[Decimal], q: float) -> Decimal | None:
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    pos = q * (len(vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    frac = Decimal(str(pos - lo))
    return vals[lo] * (Decimal(1) - frac) + vals[hi] * frac


def _table(rows: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows) if rows else pa.table({"station": pa.array([], type=pa.string())})


def main() -> int:
    p = argparse.ArgumentParser(description="QC-filter and diagnose CLI-vs-ASOS settlement basis without upstream rounding")
    p.add_argument("--history", type=Path, default=Path("/data/weather/results/settlement_reconstruction/cli_asos_history.parquet"))
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_resolved.parquet"))
    p.add_argument("--station-output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/qc_station_summary.parquet"))
    p.add_argument("--contract-output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/qc_actual_contract_flips.parquet"))
    p.add_argument("--plausibility-limit-f", type=Decimal, default=Decimal("5.0"), help="diagnostic flag only; does not mutate source values")
    args = p.parse_args()

    history = pq.read_table(args.history).to_pylist()
    catalog = [r for r in pq.read_table(args.catalog).to_pylist() if r.get("status") == "EXACT"]

    reason_counts: Counter[str] = Counter()
    for row in history:
        for reason in row.get("qc_reason_codes") or []:
            reason_counts[str(reason)] += 1

    station_rows: list[dict[str, Any]] = []
    by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        by_station[str(row.get("station") or "")].append(row)

    for station, rows in sorted(by_station.items()):
        pass_rows = [r for r in rows if r.get("qc_status") == "PASS"]
        pass_basis = [D(r.get("cli_minus_asos_high_f")) for r in pass_rows]
        pass_basis = [x for x in pass_basis if x is not None]
        abs_basis = [abs(x) for x in pass_basis]
        plausible = [x for x in pass_basis if abs(x) <= args.plausibility_limit_f]
        abs_plausible = [abs(x) for x in plausible]
        station_rows.append({
            "station": station,
            "all_days": len(rows),
            "pass_days": len(pass_rows),
            "pass_rate_pct": pct(len(pass_rows), len(rows)),
            "pass_basis_days": len(pass_basis),
            "pass_median_basis_f": None if not pass_basis else str(median(pass_basis)),
            "pass_mean_basis_f": None if mean(pass_basis) is None else str(mean(pass_basis)),
            "pass_mean_abs_basis_f": None if mean(abs_basis) is None else str(mean(abs_basis)),
            "pass_p50_abs_basis_f": None if quantile(abs_basis, .50) is None else str(quantile(abs_basis, .50)),
            "pass_p90_abs_basis_f": None if quantile(abs_basis, .90) is None else str(quantile(abs_basis, .90)),
            "pass_p95_abs_basis_f": None if quantile(abs_basis, .95) is None else str(quantile(abs_basis, .95)),
            "pass_abs_ge_0_1": sum(x >= Decimal("0.1") for x in abs_basis),
            "pass_abs_ge_0_5": sum(x >= Decimal("0.5") for x in abs_basis),
            "pass_abs_ge_1_0": sum(x >= Decimal("1.0") for x in abs_basis),
            "pass_abs_ge_2_0": sum(x >= Decimal("2.0") for x in abs_basis),
            "pass_abs_ge_0_1_pct": pct(sum(x >= Decimal("0.1") for x in abs_basis), len(abs_basis)),
            "pass_abs_ge_0_5_pct": pct(sum(x >= Decimal("0.5") for x in abs_basis), len(abs_basis)),
            "pass_abs_ge_1_0_pct": pct(sum(x >= Decimal("1.0") for x in abs_basis), len(abs_basis)),
            "pass_abs_ge_2_0_pct": pct(sum(x >= Decimal("2.0") for x in abs_basis), len(abs_basis)),
            "pass_implausible_gt_limit": sum(abs(x) > args.plausibility_limit_f for x in pass_basis),
            "plausibility_limit_f": str(args.plausibility_limit_f),
            "pass_plausible_days": len(plausible),
            "pass_plausible_mean_abs_basis_f": None if mean(abs_plausible) is None else str(mean(abs_plausible)),
        })

    history_lookup = {(str(r.get("station") or ""), str(r.get("settlement_date") or "")[:10]): r for r in history}
    contract_rows: list[dict[str, Any]] = []
    for c in catalog:
        key = (str(c.get("station") or ""), str(c.get("settlement_date") or "")[:10])
        h = history_lookup.get(key)
        if not h:
            continue
        official = D(h.get("official_cli_high_f"))
        observed = D(h.get("asos_1min_high_f"))
        if official is None or observed is None:
            continue
        flip = evaluate_contract_flip(c, official_f=float(official), public_f=float(observed), reconstructed_f=float(observed))
        contract_rows.append({
            "station": key[0],
            "settlement_date": key[1],
            "contract_id": c.get("contract_id"),
            "shape": c.get("shape"),
            "lower": c.get("lower"),
            "upper": c.get("upper"),
            "qc_status": h.get("qc_status"),
            "qc_reason_codes": h.get("qc_reason_codes"),
            "official_cli_high_f": str(official),
            "asos_1min_high_f": str(observed),
            "basis_f": str(official - observed),
            "official_winner": flip.official_winner,
            "asos_winner": flip.public_winner,
            "boundary_flip": flip.public_flip,
            "pass_only_eligible": h.get("qc_status") == "PASS",
        })

    args.station_output.parent.mkdir(parents=True, exist_ok=True)
    args.contract_output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(_table(station_rows), args.station_output)
    pq.write_table(_table(contract_rows), args.contract_output)

    pass_all = [r for r in history if r.get("qc_status") == "PASS"]
    pass_basis_all = [D(r.get("cli_minus_asos_high_f")) for r in pass_all]
    pass_basis_all = [x for x in pass_basis_all if x is not None]
    abs_all = [abs(x) for x in pass_basis_all]
    pass_contracts = [r for r in contract_rows if r["pass_only_eligible"]]
    pass_flips = [r for r in pass_contracts if r["boundary_flip"] is True]

    print(f"history_rows={len(history)} pass_days={len(pass_all)} pass_basis_days={len(pass_basis_all)}")
    print(f"qc_reason_counts={dict(reason_counts)}")
    if abs_all:
        print(f"pass_mean_abs_basis_f={mean(abs_all)}")
        print(f"pass_median_basis_f={median(pass_basis_all)}")
        print(f"pass_abs_ge_0_1={sum(x >= Decimal('0.1') for x in abs_all)} ({pct(sum(x >= Decimal('0.1') for x in abs_all), len(abs_all)):.2f}%)")
        print(f"pass_abs_ge_0_5={sum(x >= Decimal('0.5') for x in abs_all)} ({pct(sum(x >= Decimal('0.5') for x in abs_all), len(abs_all)):.2f}%)")
        print(f"pass_abs_ge_1_0={sum(x >= Decimal('1.0') for x in abs_all)} ({pct(sum(x >= Decimal('1.0') for x in abs_all), len(abs_all)):.2f}%)")
        print(f"pass_abs_ge_2_0={sum(x >= Decimal('2.0') for x in abs_all)} ({pct(sum(x >= Decimal('2.0') for x in abs_all), len(abs_all)):.2f}%)")
    print(f"actual_exact_contracts_joined={len(contract_rows)} pass_contracts={len(pass_contracts)} pass_boundary_flips={len(pass_flips)}")
    if pass_flips:
        print("pass_flip_contracts=" + repr([r["contract_id"] for r in pass_flips]))
    for row in station_rows:
        print("station_summary=" + repr(row))
    print(f"station_output={args.station_output}")
    print(f"contract_output={args.contract_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
