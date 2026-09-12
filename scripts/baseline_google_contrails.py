#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from weather_alpha.providers.google_contrails import GoogleContrailsClient


def load_locations(path: Path) -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = payload.get("locations") or {}
    if not isinstance(rows, dict):
        raise ValueError("config must contain a locations mapping")
    return {str(k): dict(v) for k, v in rows.items() if isinstance(v, dict)}


def percentile_rank(values: list[float], target: float) -> float | None:
    if not values:
        return None
    return 100.0 * sum(v <= target for v in values) / len(values)


def robust_z(values: list[float], target: float) -> float | None:
    if not values:
        return None
    med = median(values)
    mad = median([abs(v - med) for v in values])
    if mad == 0:
        return None
    return 0.67448975 * (target - med) / mad


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def local_window(day: date, tz_name: str, start: time, end: time) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    start_local = datetime.combine(day, start, tzinfo=tz)
    end_local = datetime.combine(day, end, tzinfo=tz)
    if end_local <= start_local:
        end_local += timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


async def main_async() -> int:
    ap = argparse.ArgumentParser(
        description="Rank one day's Google-observed contrail activity against prior same-hour days"
    )
    ap.add_argument("--config", type=Path, default=Path("config/contrail_locations.yaml"))
    ap.add_argument("--location", default="stayton_or")
    ap.add_argument("--target-date", required=True, help="Local YYYY-MM-DD")
    ap.add_argument("--baseline-days", type=int, default=30)
    ap.add_argument("--start-local", default="00:00")
    ap.add_argument("--end-local", default="21:00")
    ap.add_argument("--radius-km", type=float, default=150.0)
    ap.add_argument("--sleep", type=float, default=0.15)
    ap.add_argument("--output-json", type=Path)
    ap.add_argument("--output-csv", type=Path)
    args = ap.parse_args()

    locations = load_locations(args.config)
    if args.location not in locations:
        raise SystemExit(f"unknown location: {args.location}")
    loc = locations[args.location]
    lat = float(loc["latitude"])
    lon = float(loc["longitude"])
    tz_name = str(loc["timezone"])
    target_date = date.fromisoformat(args.target_date)
    start_local = parse_hhmm(args.start_local)
    end_local = parse_hhmm(args.end_local)
    satellite = "GOES-WEST-FULL-DISK" if lon < -100 else "GOES-EAST-FULL-DISK"
    client = GoogleContrailsClient()

    rows: list[dict[str, Any]] = []
    days = [target_date - timedelta(days=i) for i in range(args.baseline_days, -1, -1)]
    for idx, day in enumerate(days, 1):
        start_utc, end_utc = local_window(day, tz_name, start_local, end_local)
        summary = await client.detection_summary(
            start_utc, end_utc, lat, lon, args.radius_km, satellite
        )
        active_span_minutes = None
        if summary.first_detection_time and summary.last_detection_time:
            active_span_minutes = (
                summary.last_detection_time - summary.first_detection_time
            ).total_seconds() / 60.0
        row = {
            "local_date": day.isoformat(),
            "is_target": day == target_date,
            "window_start_utc": summary.start_time.isoformat(),
            "window_end_utc": summary.end_time.isoformat(),
            "detection_count": summary.detection_count,
            "unique_detection_frames": summary.unique_detection_frames,
            "peak_hour_count": summary.peak_hour_count,
            "peak_hour_time": summary.peak_hour_time,
            "active_span_minutes": active_span_minutes,
            "total_length_km_raw": summary.total_length_km,
            "nearest_detection_km": summary.nearest_detection_km,
        }
        rows.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)
        if idx < len(days) and args.sleep > 0:
            await asyncio.sleep(args.sleep)

    target = next(r for r in rows if r["is_target"])
    baseline = [r for r in rows if not r["is_target"]]
    metrics = [
        "detection_count",
        "unique_detection_frames",
        "peak_hour_count",
        "active_span_minutes",
        "total_length_km_raw",
    ]
    rankings: dict[str, Any] = {}
    for metric in metrics:
        vals = [float(r[metric]) for r in baseline if r.get(metric) is not None]
        tv = target.get(metric)
        rankings[metric] = {
            "target": tv,
            "baseline_n": len(vals),
            "baseline_mean": None if not vals else mean(vals),
            "baseline_median": None if not vals else median(vals),
            "percentile": None if tv is None else percentile_rank(vals, float(tv)),
            "robust_z_mad": None if tv is None else robust_z(vals, float(tv)),
        }

    result = {
        "location_key": args.location,
        "location_name": loc.get("name", args.location),
        "target_date": target_date.isoformat(),
        "baseline_days_requested": args.baseline_days,
        "start_local": args.start_local,
        "end_local": args.end_local,
        "radius_km": args.radius_km,
        "satellite": satellite,
        "target": target,
        "rankings": rankings,
        "rows": rows,
    }

    print("=== RANKING ===")
    for metric, stats in rankings.items():
        pct = stats["percentile"]
        pct_text = "n/a" if pct is None else f"{pct:.1f}"
        print(
            f"{metric}: target={stats['target']} percentile={pct_text} "
            f"median={stats['baseline_median']} robust_z={stats['robust_z_mad']}"
        )

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if args.output_csv:
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        with args.output_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
