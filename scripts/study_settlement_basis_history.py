from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.providers.surface import IemAsosOneMinuteArchive
from weather_alpha.quality.asos_qc import assess_asos_temperature_quality
from weather_alpha.settlement.precision import as_decimal, difference
from weather_alpha.settlement.reconstruction import local_standard_settlement_window
from weather_alpha.settlement.stations import station_clock


def _cli_rows(path: Path) -> list[dict[str, Any]]:
    return pq.read_table(path).to_pylist()


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _next_month(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def _dec_text(v: Decimal | None) -> str | None:
    return None if v is None else format(v, "f")


def _safe_table(rows: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows) if rows else pa.table({"station": pa.array([], type=pa.string())})


async def run(args: argparse.Namespace) -> int:
    cli_rows = _cli_rows(args.cli)
    targets = [r for r in cli_rows if r.get("station") and r.get("valid_date")]
    by_station_month: dict[tuple[str, date], list[dict[str, Any]]] = defaultdict(list)
    for row in targets:
        station = str(row["station"]).upper()
        day = date.fromisoformat(str(row["valid_date"])[:10])
        by_station_month[(station, _month_start(day))].append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    archive = IemAsosOneMinuteArchive(timeout_s=args.timeout)

    out: list[dict[str, Any]] = []
    fetch_errors: list[str] = []

    for (station, month), month_cli in sorted(by_station_month.items()):
        clock = station_clock(station)
        first_window = local_standard_settlement_window(clock, month)
        end_month = _next_month(month)
        last_window = local_standard_settlement_window(clock, end_month)
        fetch_start = first_window.start_utc - timedelta(minutes=args.buffer_minutes)
        fetch_end = last_window.start_utc + timedelta(minutes=args.buffer_minutes)
        try:
            observations = await archive.fetch([station], fetch_start, fetch_end)
        except Exception as exc:
            msg = f"{station} {month.isoformat()}: {type(exc).__name__}: {exc}"
            fetch_errors.append(msg)
            print(f"fetch_error={msg}")
            continue

        raw_path = args.raw_dir / f"{station}_{month.year}_{month.month:02d}.jsonl"
        with raw_path.open("w", encoding="utf-8") as h:
            for o in observations:
                h.write(json.dumps({
                    "station": o.station,
                    "valid_time": o.valid_time.isoformat(),
                    "temperature_f": None if o.temperature_f is None else repr(o.temperature_f),
                    "dewpoint_f": None if o.dewpoint_f is None else repr(o.dewpoint_f),
                    "wind_direction_deg": o.wind_direction_deg,
                    "wind_speed_kt": o.wind_speed_kt,
                    "pressure_mb": o.pressure_mb,
                    "source": o.source,
                }, sort_keys=True) + "\n")

        for cli in sorted(month_cli, key=lambda r: str(r["valid_date"])):
            day_text = str(cli["valid_date"])[:10]
            day = date.fromisoformat(day_text)
            window = local_standard_settlement_window(clock, day)
            in_window = [o for o in observations if window.start_utc <= o.valid_time < window.end_utc]
            qc = assess_asos_temperature_quality(in_window, as_of=window.end_utc)
            temp_values = [as_decimal(repr(o.temperature_f)) for o in in_window if o.temperature_f is not None]
            high_1m = max(temp_values) if temp_values else None
            low_1m = min(temp_values) if temp_values else None
            official_high = as_decimal(cli.get("high_f"))
            official_low = as_decimal(cli.get("low_f"))
            high_basis = difference(official_high, high_1m)
            low_basis = difference(official_low, low_1m)
            high_times = [o.valid_time.isoformat() for o in in_window if high_1m is not None and as_decimal(repr(o.temperature_f)) == high_1m]
            low_times = [o.valid_time.isoformat() for o in in_window if low_1m is not None and as_decimal(repr(o.temperature_f)) == low_1m]

            out.append({
                "station": station,
                "settlement_date": day_text,
                "official_cli_high_f": _dec_text(official_high),
                "official_cli_low_f": _dec_text(official_low),
                "asos_1min_high_f": _dec_text(high_1m),
                "asos_1min_low_f": _dec_text(low_1m),
                "cli_minus_asos_high_f": _dec_text(high_basis),
                "cli_minus_asos_low_f": _dec_text(low_basis),
                "asos_high_times_utc": high_times,
                "asos_low_times_utc": low_times,
                "cli_high_time_lst": cli.get("high_time_lst"),
                "cli_low_time_lst": cli.get("low_time_lst"),
                "observations_used": len(in_window),
                "temperature_observations": len(temp_values),
                "qc_status": qc.status.value,
                "qc_reason_codes": list(qc.reason_codes),
                "window_start_utc": window.start_utc.isoformat(),
                "window_end_utc": window.end_utc.isoformat(),
                "window_start_civil": window.start_civil.isoformat(),
                "window_end_civil": window.end_civil.isoformat(),
                "standard_utc_offset_hours": window.standard_utc_offset_hours,
                "source_precision_note": "ASOS values preserved as received; no strategy-layer rounding applied",
                "raw_asos_month_path": str(raw_path),
            })

        print(f"station={station} month={month.isoformat()} days={len(month_cli)} asos_rows={len(observations)}")

    pq.write_table(_safe_table(out), args.output)

    high_basis = [Decimal(r["cli_minus_asos_high_f"]) for r in out if r.get("cli_minus_asos_high_f") is not None]
    qc_counts = Counter(str(r.get("qc_status")) for r in out)
    station_counts = Counter(str(r.get("station")) for r in out)
    print(f"rows={len(out)} stations={len(station_counts)} fetch_errors={len(fetch_errors)}")
    print(f"station_counts={dict(station_counts)}")
    print(f"qc_counts={dict(qc_counts)}")
    if high_basis:
        abs_basis = [abs(x) for x in high_basis]
        print(f"high_basis_nonzero={sum(x != 0 for x in high_basis)}")
        print(f"high_basis_abs_ge_0_1={sum(x >= Decimal('0.1') for x in abs_basis)}")
        print(f"high_basis_abs_ge_0_5={sum(x >= Decimal('0.5') for x in abs_basis)}")
        print(f"high_basis_abs_ge_1_0={sum(x >= Decimal('1.0') for x in abs_basis)}")
        print(f"high_basis_abs_ge_2_0={sum(x >= Decimal('2.0') for x in abs_basis)}")
        print(f"high_basis_min={min(high_basis)} high_basis_max={max(high_basis)}")
    if fetch_errors:
        print("fetch_errors_sample=" + repr(fetch_errors[:10]))
    print(f"result_status={'COMPLETE' if not fetch_errors else 'PARTIAL'}")
    print(f"output={args.output}")
    return 0 if not fetch_errors else 2


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Study CLI vs high-resolution ASOS settlement basis while preserving source precision")
    p.add_argument("--cli", type=Path, default=Path("/data/weather/normalized/settlements/nws_cli_daily.parquet"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/cli_asos_history.parquet"))
    p.add_argument("--raw-dir", type=Path, default=Path("/data/weather/raw/asos/history_months"))
    p.add_argument("--buffer-minutes", type=int, default=10)
    p.add_argument("--timeout", type=float, default=120.0)
    return p.parse_args()


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
