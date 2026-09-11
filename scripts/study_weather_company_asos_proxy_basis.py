from __future__ import annotations

import argparse
import asyncio
import math
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from statistics import mean, median
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.providers.surface import IemAsosOneMinuteArchive
from weather_alpha.settlement.reconstruction import local_standard_settlement_window
from weather_alpha.settlement.stations import station_clock

NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def parse_numeric(value: Any) -> float | None:
    if value in (None, ""):
        return None
    m = NUM.search(str(value))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


async def asos_high(station: str, day: date) -> tuple[float | None, int, str | None]:
    window = local_standard_settlement_window(station_clock(station), day)
    provider = IemAsosOneMinuteArchive()
    try:
        rows = await provider.fetch([station], window.start_utc, window.end_utc)
    except Exception as exc:
        return None, 0, f"{type(exc).__name__}:{exc}"
    temps = [float(r.temperature_f) for r in rows if r.temperature_f is not None]
    return (max(temps) if temps else None), len(temps), None


def main() -> int:
    p = argparse.ArgumentParser(description="Compare Weather Company Kalshi expiration values with ASOS proxy daily highs")
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/weather_company_regime_history.parquet"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/weather_company_asos_proxy_basis.parquet"))
    p.add_argument("--summary-output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/weather_company_asos_proxy_basis_summary.parquet"))
    p.add_argument("--newest-first", action=argparse.BooleanOptionalAction, default=True)
    args = p.parse_args()

    rows = pq.read_table(args.catalog).to_pylist()
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("source_family") != "WEATHER_COMPANY":
            continue
        station = str(r.get("asos_proxy_station") or "")
        day = str(r.get("settlement_date") or "")[:10]
        series = str(r.get("series_ticker") or "")
        if station and len(day) == 10:
            grouped[(series, station, day)].append(r)

    keys = sorted(grouped, key=lambda x: x[2], reverse=args.newest_first)
    out: list[dict[str, Any]] = []
    cache: dict[tuple[str, str], tuple[float | None, int, str | None]] = {}

    for i, (series, station, day_text) in enumerate(keys, 1):
        day = date.fromisoformat(day_text)
        values = [parse_numeric(r.get("expiration_value")) for r in grouped[(series, station, day_text)]]
        values = [v for v in values if v is not None]
        counts = Counter(values)
        settlement_temp = counts.most_common(1)[0][0] if counts else None
        disagreement = len(counts) > 1

        cache_key = (station, day_text)
        if cache_key not in cache:
            cache[cache_key] = asyncio.run(asos_high(station, day))
        a_high, nobs, error = cache[cache_key]
        basis = None if settlement_temp is None or a_high is None else settlement_temp - a_high

        row = {
            "series_ticker": series,
            "settlement_date": day_text,
            "source_family": "WEATHER_COMPANY",
            "asos_proxy_station": station,
            "proxy_role": "PREDICTIVE_ASOS_PROXY_NOT_SETTLEMENT_SOURCE",
            "weather_company_expiration_value_f": settlement_temp,
            "expiration_value_unique_count": len(counts),
            "expiration_value_disagreement": disagreement,
            "asos_proxy_high_f": a_high,
            "asos_observations": nobs,
            "twc_minus_asos_proxy_high_f": basis,
            "fetch_error": error,
            "contract_rows": len(grouped[(series, station, day_text)]),
        }
        out.append(row)
        print(f"{i}/{len(keys)} {day_text} {series} proxy={station} twc={settlement_temp} asos={a_high} basis={basis} err={error}", flush=True)

    summary: list[dict[str, Any]] = []
    by_series: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in out:
        by_series[r["series_ticker"]].append(r)
    for series, subset in sorted(by_series.items()):
        vals = [float(r["twc_minus_asos_proxy_high_f"]) for r in subset if r.get("twc_minus_asos_proxy_high_f") is not None]
        summary.append({
            "series_ticker": series,
            "days": len(subset),
            "basis_days": len(vals),
            "mean_basis_f": None if not vals else mean(vals),
            "median_basis_f": None if not vals else median(vals),
            "min_basis_f": None if not vals else min(vals),
            "max_basis_f": None if not vals else max(vals),
            "abs_gt_1f_days": sum(abs(v) > 1.0 for v in vals),
            "abs_gt_2f_days": sum(abs(v) > 2.0 for v in vals),
            "rmse_f": None if not vals else math.sqrt(sum(v*v for v in vals) / len(vals)),
            "interpretation": "EXPLORATORY_PROXY_BASIS_NOT_SETTLEMENT_STATION_TRUTH",
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(out), args.output, compression="zstd")
    pq.write_table(pa.Table.from_pylist(summary), args.summary_output, compression="zstd")
    print(f"rows={len(out)} output={args.output}")
    print(f"summary_output={args.summary_output}")
    for row in summary:
        print("summary=" + repr(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
