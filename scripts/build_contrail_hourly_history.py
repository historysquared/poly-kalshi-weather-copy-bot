#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

from weather_alpha.providers.google_contrails import GoogleContrailsClient


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def local_day_bounds(day: date, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    start = datetime.combine(day, time(0), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def segments(start: datetime, end: datetime, hours: int = 12) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        nxt = min(end, cursor + timedelta(hours=hours))
        out.append((cursor, nxt))
        cursor = nxt
    return out


async def fetch_location_day(http: httpx.AsyncClient, sem: asyncio.Semaphore, client: GoogleContrailsClient,
                             key: str, loc: dict, day: date, radius_km: float) -> dict:
    lat, lon = float(loc["latitude"]), float(loc["longitude"])
    satellite = "GOES-WEST-FULL-DISK" if lon < -100 else "GOES-EAST-FULL-DISK"
    start, end = local_day_bounds(day, str(loc["timezone"]))
    features: dict[str, dict] = {}
    try:
        for seg_start, seg_end in segments(start, end):
            params = [("start_time", client._iso(seg_start)), ("end_time", client._iso(seg_end))]
            params += [("bounds", value) for value in client.bounds_square(lat, lon, radius_km)]
            params += [("satellite_origins", satellite)]
            async with sem:
                response = None
                for attempt in range(5):
                    response = await http.get(client.BASE + "/detections", params=params, headers=client.headers)
                    if response.status_code == 200:
                        break
                    if response.status_code != 429:
                        response.raise_for_status()
                    await asyncio.sleep(1 + attempt * 2)
                assert response is not None
                response.raise_for_status()
            payload = response.json() if response.content else {}
            for feature in payload.get("features", []) if isinstance(payload, dict) else []:
                props = feature.get("properties") or {}
                fingerprint = json.dumps([props.get("time"), feature.get("geometry")], sort_keys=True, separators=(",", ":"))
                features[fingerprint] = feature

        tz = ZoneInfo(str(loc["timezone"]))
        hourly: dict[str, int] = {}
        frame_times: set[str] = set()
        for feature in features.values():
            raw = (feature.get("properties") or {}).get("time")
            if not raw:
                continue
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(tz)
            hourly[str(dt.hour)] = hourly.get(str(dt.hour), 0) + 1
            frame_times.add(str(raw))
        return {
            "location_key": key,
            "name": loc.get("name", key),
            "station": loc.get("settlement_station"),
            "local_date": day.isoformat(),
            "count": len(features),
            "frames": len(frame_times),
            "hourly_counts": hourly,
            "radius_km": radius_km,
            "satellite": satellite,
            "source_endpoint": client.BASE + "/detections",
            "error": None,
        }
    except Exception as exc:
        return {"location_key": key, "local_date": day.isoformat(), "error": f"{type(exc).__name__}:{exc}"}


async def main_async(args: argparse.Namespace) -> int:
    locations = yaml.safe_load(args.config.read_text(encoding="utf-8"))["locations"]
    client = GoogleContrailsClient(timeout=args.timeout)
    sem = asyncio.Semaphore(args.concurrency)
    days: list[date] = []
    day = args.start_date
    while day <= args.end_date:
        days.append(day)
        day += timedelta(days=1)

    rows: list[dict] = []
    limits = httpx.Limits(max_connections=max(args.concurrency + 2, 4), max_keepalive_connections=max(args.concurrency + 2, 4))
    async with httpx.AsyncClient(timeout=args.timeout, follow_redirects=True, limits=limits) as http:
        tasks = [fetch_location_day(http, sem, client, key, loc, day, args.radius_km)
                 for day in days for key, loc in locations.items()]
        for i, future in enumerate(asyncio.as_completed(tasks), 1):
            rows.append(await future)
            if i % 100 == 0 or i == len(tasks):
                print(f"progress={i}/{len(tasks)}", flush=True)

    rows.sort(key=lambda x: (x.get("local_date", ""), x.get("location_key", "")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    errors = [r for r in rows if r.get("error")]
    print(f"output={args.output} rows={len(rows)} errors={len(errors)}")
    return 0 if not errors else 2


def main() -> int:
    p = argparse.ArgumentParser(description="Build hourly Google Contrails detection history by settlement city")
    p.add_argument("--start-date", type=parse_date, required=True)
    p.add_argument("--end-date", type=parse_date, required=True)
    p.add_argument("--config", type=Path, default=Path("config/contrail_locations.yaml"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/normalized/contrails/contrail_hourly_history.json"))
    p.add_argument("--radius-km", type=float, default=150.0)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--timeout", type=float, default=60.0)
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
