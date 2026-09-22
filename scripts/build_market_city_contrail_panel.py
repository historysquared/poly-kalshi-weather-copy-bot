#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

import httpx
import yaml

from weather_alpha.providers.google_contrails import GoogleContrailsClient


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def local_window(day: date, tz_name: str, start: time, end: time) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    a = datetime.combine(day, start, tzinfo=tz)
    b = datetime.combine(day, end, tzinfo=tz)
    if b <= a:
        b += timedelta(days=1)
    return a.astimezone(timezone.utc), b.astimezone(timezone.utc)


async def main_async() -> int:
    ap = argparse.ArgumentParser(description="Build same-local-window Google contrail counts for weather-market cities")
    ap.add_argument("--config", type=Path, default=Path("config/contrail_locations.yaml"))
    ap.add_argument("--target-date", required=True, help="Local YYYY-MM-DD")
    ap.add_argument("--baseline-days", type=int, default=30)
    ap.add_argument("--start-local", default="12:00")
    ap.add_argument("--end-local", default="18:00")
    ap.add_argument("--radius-km", type=float, default=150.0, help="Approximate half-width of square bounds")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--venue", choices=["kalshi", "polymarket_us", "all"], default="all")
    ap.add_argument("--output-json", type=Path, required=True)
    ap.add_argument("--output-csv", type=Path)
    args = ap.parse_args()

    payload = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    all_locations = payload.get("locations") or {}
    wanted = {"kalshi", "polymarket_us"} if args.venue == "all" else {args.venue}
    locations = {
        key: row for key, row in all_locations.items()
        if isinstance(row, dict) and wanted.intersection(row.get("venues") or [])
    }

    target = date.fromisoformat(args.target_date)
    start_local, end_local = parse_hhmm(args.start_local), parse_hhmm(args.end_local)
    days = [target - timedelta(days=i) for i in range(args.baseline_days, -1, -1)]
    sem = asyncio.Semaphore(max(1, args.concurrency))
    client = GoogleContrailsClient(timeout=60.0)

    async def fetch(http: httpx.AsyncClient, key: str, loc: dict, day: date) -> dict:
        a, b = local_window(day, str(loc["timezone"]), start_local, end_local)
        satellite = "GOES-WEST-FULL-DISK" if float(loc["longitude"]) < -100 else "GOES-EAST-FULL-DISK"
        params = [("start_time", client._iso(a)), ("end_time", client._iso(b))]
        params.extend(("bounds", x) for x in client.bounds_square(float(loc["latitude"]), float(loc["longitude"]), args.radius_km))
        params.append(("satellite_origins", satellite))
        error = None
        async with sem:
            for attempt in range(4):
                try:
                    response = await http.get(f"{client.BASE}/detections", params=params, headers=client.headers)
                    response.raise_for_status()
                    features = response.json().get("features", [])
                    times = [
                        (feature.get("properties") or {}).get("time")
                        for feature in features if isinstance(feature, dict)
                    ]
                    times = [x for x in times if x]
                    hourly: dict[str, int] = {}
                    for raw in times:
                        hour = str(raw)[:13] + ":00:00+00:00"
                        hourly[hour] = hourly.get(hour, 0) + 1
                    return {
                        "local_date": day.isoformat(),
                        "location_key": key,
                        "name": loc.get("name", key),
                        "station": loc.get("settlement_station"),
                        "venues": "|".join(loc.get("venues") or []),
                        "feature_detection_count": len(features),
                        "unique_detection_frames": len(set(times)),
                        "detections_per_frame": None if not times else round(len(features) / len(set(times)), 4),
                        "peak_hour_count": max(hourly.values()) if hourly else 0,
                        "bounds_shape": "square",
                        "bounds_half_width_km_approx": args.radius_km,
                        "satellite": satellite,
                        "query_error": None,
                    }
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    if attempt < 3:
                        await asyncio.sleep(2 ** attempt)
        return {
            "local_date": day.isoformat(),
            "location_key": key,
            "name": loc.get("name", key),
            "station": loc.get("settlement_station"),
            "venues": "|".join(loc.get("venues") or []),
            "query_error": error,
        }

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:
        tasks = [fetch(http, key, loc, day) for day in days for key, loc in locations.items()]
        rows = [await future for future in asyncio.as_completed(tasks)]

    rows.sort(key=lambda row: (row["local_date"], row["location_key"]))
    summaries = []
    for key, loc in locations.items():
        good = [r for r in rows if r["location_key"] == key and not r.get("query_error")]
        baseline = [r for r in good if r["local_date"] != target.isoformat()]
        today = next((r for r in good if r["local_date"] == target.isoformat()), None)
        values = [float(r["feature_detection_count"]) for r in baseline]
        today_count = None if today is None else float(today["feature_detection_count"])
        percentile = None if today_count is None or not values else 100.0 * sum(v <= today_count for v in values) / len(values)
        summaries.append({
            "location_key": key,
            "name": loc.get("name", key),
            "station": loc.get("settlement_station"),
            "venues": loc.get("venues") or [],
            "baseline_n": len(values),
            "baseline_mean": None if not values else mean(values),
            "baseline_median": None if not values else median(values),
            "baseline_max": None if not values else max(values),
            "target_count": today_count,
            "target_percentile": percentile,
        })

    result = {
        "target_date": target.isoformat(),
        "baseline_days": args.baseline_days,
        "start_local": args.start_local,
        "end_local": args.end_local,
        "bounds_shape": "square",
        "bounds_half_width_km_approx": args.radius_km,
        "note": "Counts are returned GeoJSON LineString features, not deduplicated physical contrails.",
        "summaries": summaries,
        "rows": rows,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.output_csv:
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        fields = sorted(set().union(*(row.keys() for row in rows)))
        with args.output_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    for row in summaries:
        print(json.dumps(row, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
