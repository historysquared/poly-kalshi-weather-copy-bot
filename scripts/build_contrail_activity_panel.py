#!/usr/bin/env python3
"""Build matched-window contrail activity percentiles for weather-market locations."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from weather_alpha.providers.google_contrails import GoogleContrailsClient

DEFAULT_LOCATIONS = [
    "stayton_or",
    "new_york_city",
    "chicago",
    "miami",
    "los_angeles",
    "san_francisco",
]


def percentile(values: list[float], target: float) -> float | None:
    if not values:
        return None
    return 100.0 * sum(v <= target for v in values) / len(values)


def robust_z(values: list[float], target: float) -> float | None:
    if not values:
        return None
    med = median(values)
    mad = median(abs(v - med) for v in values)
    if mad == 0:
        return None
    return 0.67448975 * (target - med) / mad


def day_window(day: date, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    start = datetime.combine(day, time(0, 0), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def matched_window(
    day: date, tz_name: str, local_now: datetime
) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    clock = local_now.astimezone(tz).time().replace(tzinfo=None)
    start = datetime.combine(day, time(0, 0), tzinfo=tz)
    end = datetime.combine(day, clock, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


async def summarize(
    client: GoogleContrailsClient,
    loc: dict[str, Any],
    start: datetime,
    end: datetime,
    radius_km: float,
) -> dict[str, Any]:
    lon = float(loc["longitude"])
    satellite = "GOES-WEST-FULL-DISK" if lon < -100 else "GOES-EAST-FULL-DISK"
    s = await client.detection_summary(
        start,
        end,
        float(loc["latitude"]),
        lon,
        radius_km,
        satellite,
    )
    return {
        "count": s.detection_count,
        "frames": s.unique_detection_frames,
        "in_bounds_km": s.in_bounds_length_km,
        "nearest_km": s.nearest_detection_km,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=Path("config/contrail_locations.yaml"))
    ap.add_argument("--location", action="append", dest="locations")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--radius-km", type=float, default=150.0)
    ap.add_argument(
        "--output-json",
        type=Path,
        default=Path("/data/weather/live/contrail_activity_panel.json"),
    )
    ap.add_argument(
        "--output-csv",
        type=Path,
        default=Path("/data/weather/live/contrail_activity_panel.csv"),
    )
    args = ap.parse_args()

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))["locations"]
    keys = args.locations or DEFAULT_LOCATIONS
    missing = [k for k in keys if k not in cfg]
    if missing:
        raise SystemExit("unknown locations: " + ", ".join(missing))

    now_utc = datetime.now(timezone.utc)
    client = GoogleContrailsClient(timeout=120)
    rows: list[dict[str, Any]] = []

    for key in keys:
        loc = cfg[key]
        tz = ZoneInfo(str(loc["timezone"]))
        local_now = now_utc.astimezone(tz)
        today = local_now.date()
        baseline_days = [today - timedelta(days=i) for i in range(args.days, 0, -1)]

        full_rows = []
        matched_rows = []
        for day in baseline_days:
            full_rows.append(
                await summarize(
                    client, loc, *day_window(day, str(loc["timezone"])), args.radius_km
                )
            )
            matched_rows.append(
                await summarize(
                    client,
                    loc,
                    *matched_window(day, str(loc["timezone"]), local_now),
                    args.radius_km,
                )
            )

        current = await summarize(
            client,
            loc,
            *matched_window(today, str(loc["timezone"]), local_now),
            args.radius_km,
        )

        metrics: dict[str, dict[str, float | None]] = {}
        for metric in ("count", "frames", "in_bounds_km"):
            vals = [float(r[metric]) for r in matched_rows]
            target = float(current[metric])
            metrics[metric] = {
                "today": target,
                "baseline_mean": mean(vals),
                "baseline_median": median(vals),
                "percentile": percentile(vals, target),
                "robust_z": robust_z(vals, target),
            }

        component_percentiles = [
            float(metrics[m]["percentile"])
            for m in ("count", "frames", "in_bounds_km")
            if metrics[m]["percentile"] is not None
        ]

        row = {
            "location_key": key,
            "name": loc.get("name", key),
            "station": loc.get("settlement_station"),
            "radius_km": args.radius_km,
            "baseline_days": args.days,
            "full_day_avg_count": mean(float(r["count"]) for r in full_rows),
            "full_day_median_count": median(float(r["count"]) for r in full_rows),
            "full_day_avg_frames": mean(float(r["frames"]) for r in full_rows),
            "today_local_time": local_now.isoformat(),
            "today_count": current["count"],
            "today_frames": current["frames"],
            "today_in_bounds_km": current["in_bounds_km"],
            "today_nearest_km": current["nearest_km"],
            "count_percentile": metrics["count"]["percentile"],
            "frames_percentile": metrics["frames"]["percentile"],
            "in_bounds_km_percentile": metrics["in_bounds_km"]["percentile"],
            "activity_percentile": (
                median(component_percentiles) if component_percentiles else None
            ),
            "count_robust_z": metrics["count"]["robust_z"],
        }
        rows.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
