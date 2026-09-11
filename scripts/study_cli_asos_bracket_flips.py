from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.providers.surface import IemAsosOneMinuteArchive, SurfaceFetchError
from weather_alpha.quality.asos_qc import assess_asos_temperature_quality
from weather_alpha.settlement.bracket_study import evaluate_contract_flip
from weather_alpha.settlement.reconstruction import local_standard_settlement_window, reconstruct_daily_extreme
from weather_alpha.settlement.stations import station_clock


def _cli_lookup(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    rows = pq.read_table(path).to_pylist()
    return {(str(r.get("station") or "").upper(), str(r.get("valid_date") or "")[:10]): r for r in rows}


def _safe_table(rows: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows) if rows else pa.table({"station": pa.array([], type=pa.string())})


def _obs_to_json(obs: Any) -> dict[str, Any]:
    return {
        "station": obs.station,
        "valid_time": obs.valid_time.isoformat(),
        "temperature_f": obs.temperature_f,
        "dewpoint_f": obs.dewpoint_f,
        "wind_direction_deg": obs.wind_direction_deg,
        "wind_speed_kt": obs.wind_speed_kt,
        "pressure_mb": obs.pressure_mb,
        "source": obs.source,
        "received_time": obs.received_time.isoformat() if obs.received_time else None,
    }


async def run(args: argparse.Namespace) -> int:
    catalog = [r for r in pq.read_table(args.catalog).to_pylist() if r.get("status") == "EXACT"]
    cli = _cli_lookup(args.cli)
    events = sorted({(str(r["station"]).upper(), str(r["settlement_date"])[:10], str(r.get("measurement") or "")) for r in catalog})
    contracts_by_event: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in catalog:
        key = (str(row["station"]).upper(), str(row["settlement_date"])[:10], str(row.get("measurement") or ""))
        contracts_by_event.setdefault(key, []).append(row)

    args.raw_dir.mkdir(parents=True, exist_ok=True)
    args.station_output.parent.mkdir(parents=True, exist_ok=True)
    args.contract_output.parent.mkdir(parents=True, exist_ok=True)

    archive = IemAsosOneMinuteArchive(timeout_s=args.timeout)
    station_rows: list[dict[str, Any]] = []
    contract_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for station, day_text, measurement in events:
        if measurement not in {"DAILY_HIGH", "DAILY_LOW"}:
            continue
        day = date.fromisoformat(day_text)
        clock = station_clock(station)
        window = local_standard_settlement_window(clock, day)
        fetch_start = window.start_utc - timedelta(minutes=args.buffer_minutes)
        fetch_end = window.end_utc + timedelta(minutes=args.buffer_minutes)
        try:
            observations = await archive.fetch([station], fetch_start, fetch_end)
        except SurfaceFetchError as exc:
            errors.append(f"{station} {day_text}: {exc.status.value}: {exc}")
            print(f"fetch_error station={station} date={day_text} status={exc.status.value} http_status={exc.status_code} error={exc}")
            continue
        except Exception as exc:
            errors.append(f"{station} {day_text}: {type(exc).__name__}: {exc}")
            print(f"fetch_error station={station} date={day_text} status=UNEXPECTED error={exc}")
            continue

        raw_path = args.raw_dir / f"{station}_{day_text}.jsonl"
        with raw_path.open("w", encoding="utf-8") as handle:
            for obs in observations:
                handle.write(json.dumps(_obs_to_json(obs), sort_keys=True) + "\n")

        in_window = [o for o in observations if window.start_utc <= o.valid_time < window.end_utc]
        qc = assess_asos_temperature_quality(in_window, as_of=window.end_utc)
        cli_row = cli.get((station, day_text))
        official = None
        if cli_row:
            official = cli_row.get("high_f") if measurement == "DAILY_HIGH" else cli_row.get("low_f")
        kind = "high" if measurement == "DAILY_HIGH" else "low"
        recon = reconstruct_daily_extreme(observations, clock=clock, settlement_date=day, official_extreme_f=official, kind=kind)

        station_row = {
            "station": station,
            "settlement_date": day_text,
            "measurement": measurement,
            "official_cli_extreme_f": official,
            "asos_1min_extreme_f": recon.observed_extreme_f,
            "cli_minus_asos_1min_f": recon.residual_f,
            "exact_match": recon.exact_match,
            "within_one_f": recon.within_one_f,
            "observations_used": recon.observations_used,
            "qc_status": qc.status.value,
            "qc_reason_codes": list(qc.reason_codes),
            "window_start_utc": window.start_utc.isoformat(),
            "window_end_utc": window.end_utc.isoformat(),
            "window_start_civil": window.start_civil.isoformat(),
            "window_end_civil": window.end_civil.isoformat(),
            "standard_utc_offset_hours": window.standard_utc_offset_hours,
            "raw_asos_path": str(raw_path),
            "cli_product": cli_row.get("product") if cli_row else None,
            "cli_product_link": cli_row.get("product_link") if cli_row else None,
        }
        station_rows.append(station_row)

        for contract in contracts_by_event[(station, day_text, measurement)]:
            flip = evaluate_contract_flip(
                contract,
                official_f=official,
                public_f=recon.observed_extreme_f,
                reconstructed_f=recon.observed_extreme_f,
            )
            contract_rows.append({
                "station": station,
                "settlement_date": day_text,
                "measurement": measurement,
                "contract_id": contract.get("contract_id"),
                "shape": contract.get("shape"),
                "lower": contract.get("lower"),
                "upper": contract.get("upper"),
                "official_cli_extreme_f": official,
                "asos_1min_extreme_f": recon.observed_extreme_f,
                "basis_f": recon.residual_f,
                "official_winner": flip.official_winner,
                "asos_1min_winner": flip.public_winner,
                "boundary_flip": flip.public_flip,
                "official_boundary_distance_f": flip.official_boundary_distance_f,
                "asos_boundary_distance_f": flip.public_boundary_distance_f,
                "qc_status": qc.status.value,
            })

        print(
            f"station={station} date={day_text} measurement={measurement} "
            f"cli={official} asos1m={recon.observed_extreme_f} basis={recon.residual_f} "
            f"qc={qc.status.value} contracts={len(contracts_by_event[(station, day_text, measurement)])}"
        )

    # Fail closed: do not overwrite canonical research outputs when any required
    # station-day failed. Partial artifacts are explicitly labeled partial.
    station_path = args.station_output if not errors else args.station_output.with_name(args.station_output.stem + ".partial" + args.station_output.suffix)
    contract_path = args.contract_output if not errors else args.contract_output.with_name(args.contract_output.stem + ".partial" + args.contract_output.suffix)
    pq.write_table(_safe_table(station_rows), station_path)
    pq.write_table(_safe_table(contract_rows), contract_path)

    residuals = [float(r["cli_minus_asos_1min_f"]) for r in station_rows if r.get("cli_minus_asos_1min_f") is not None]
    flips = [r for r in contract_rows if r.get("boundary_flip") is True]
    qc_counts = Counter(str(r.get("qc_status")) for r in station_rows)
    print(f"station_days={len(station_rows)} contracts={len(contract_rows)} boundary_flips={len(flips)} errors={len(errors)}")
    print(f"qc_counts={dict(qc_counts)}")
    if residuals:
        print(f"basis_min={min(residuals):.3f} basis_max={max(residuals):.3f} basis_mean={sum(residuals)/len(residuals):.3f}")
        print(f"nonzero_basis_days={sum(abs(x) > 1e-9 for x in residuals)} within_1f_days={sum(abs(x) <= 1.0 + 1e-9 for x in residuals)}")
    if flips:
        print("flip_contracts=" + repr([r["contract_id"] for r in flips[:25]]))
    if errors:
        print("errors_sample=" + repr(errors[:10]))
        print("result_status=INCOMPLETE_DO_NOT_USE")
    else:
        print("result_status=COMPLETE")
    print(f"station_output={station_path}")
    print(f"contract_output={contract_path}")
    return 0 if not errors else 2


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare official NWS CLI extrema with causal IEM one-minute NCEI ASOS and test Kalshi bracket flips")
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_resolved.parquet"))
    p.add_argument("--cli", type=Path, default=Path("/data/weather/normalized/settlements/nws_cli_daily.parquet"))
    p.add_argument("--station-output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/cli_asos_station_days.parquet"))
    p.add_argument("--contract-output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/cli_asos_contract_flips.parquet"))
    p.add_argument("--raw-dir", type=Path, default=Path("/data/weather/raw/asos/settlement_study"))
    p.add_argument("--buffer-minutes", type=int, default=10)
    p.add_argument("--timeout", type=float, default=60.0)
    return p.parse_args()


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
