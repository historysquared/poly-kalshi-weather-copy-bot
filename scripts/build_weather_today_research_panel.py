#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import yaml

from scripts.build_kalshi_weather_bucket_history import BASE as KALSHI_BASE, SERIES_BY_CITY, event_date
from weather_alpha.providers.google_contrails import GoogleContrailsClient
from weather_alpha.providers.nbm import NbmTextClient


def normal_cdf(x: float, mean: float, sd: float) -> float:
    return 0.5 * (1 + math.erf((x - mean) / (max(0.75, sd) * math.sqrt(2))))


def market_probability(market: dict, mean: float, sd: float) -> float:
    st, lo, hi = market.get("strike_type"), market.get("floor_strike"), market.get("cap_strike")
    if st == "less" and hi is not None:
        return normal_cdf(float(hi) - 0.5, mean, sd)
    if st == "greater" and lo is not None:
        return 1 - normal_cdf(float(lo) + 0.5, mean, sd)
    if st == "between" and lo is not None and hi is not None:
        return normal_cdf(float(hi) + 0.5, mean, sd) - normal_cdf(float(lo) - 0.5, mean, sd)
    return 0.0


def midrank_percentile(values: list[float], x: float) -> float | None:
    if not values:
        return None
    return 100.0 * (sum(v < x for v in values) + 0.5 * sum(v == x for v in values)) / len(values)


async def detection_count(http: httpx.AsyncClient, client: GoogleContrailsClient, loc: dict,
                          start: datetime, end: datetime, radius_km: float) -> int:
    lat, lon = float(loc["latitude"]), float(loc["longitude"])
    satellite = "GOES-WEST-FULL-DISK" if lon < -100 else "GOES-EAST-FULL-DISK"
    params = [("start_time", client._iso(start)), ("end_time", client._iso(end))]
    params += [("bounds", x) for x in client.bounds_square(lat, lon, radius_km)]
    params += [("satellite_origins", satellite)]
    for attempt in range(5):
        r = await http.get(client.BASE + "/detections", params=params, headers=client.headers)
        if r.status_code == 200:
            payload = r.json() if r.content else {}
            return len(payload.get("features", [])) if isinstance(payload, dict) else 0
        if r.status_code != 429:
            r.raise_for_status()
        await asyncio.sleep(1 + attempt * 2)
    r.raise_for_status()
    return 0


async def main_async(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    locations = yaml.safe_load(args.config.read_text(encoding="utf-8"))["locations"]
    history = json.loads(args.contrail_history.read_text(encoding="utf-8"))
    historical = {(r["location_key"], r["local_date"]): {int(k): int(v) for k, v in (r.get("hourly_counts") or {}).items()}
                  for r in history if not r.get("error")}
    history_dates = sorted({r["local_date"] for r in history if not r.get("error")})

    # One NOAA NBM text product contains all station blocks for the cycle date.
    nbm_client = NbmTextClient()
    cycle_date = now.date()
    nbm_text, nbm_url = nbm_client.fetch_text(cycle_date, args.nbm_cycle)
    nbm_by_station = {station: nbm_client.parse_station(nbm_text, station=station, valid_date=cycle_date,
                                                        cycle=args.nbm_cycle, source_url=nbm_url)
                      for station in {str(v.get("settlement_station") or "").upper() for v in locations.values()}}

    contrail_client = GoogleContrailsClient(timeout=args.timeout)
    meta: list[tuple[str, dict, datetime, int]] = []
    calls = []
    limits = httpx.Limits(max_connections=12, max_keepalive_connections=12)
    async with httpx.AsyncClient(timeout=args.timeout, follow_redirects=True, limits=limits) as http:
        for key, loc in locations.items():
            tz = ZoneInfo(str(loc["timezone"]))
            local_now = now.astimezone(tz)
            completed_hour = local_now.hour
            local_day = local_now.date()
            previous = local_day - timedelta(days=1)
            start_today = datetime.combine(local_day, time(0), tzinfo=tz).astimezone(timezone.utc)
            end_today = datetime.combine(local_day, time(completed_hour), tzinfo=tz).astimezone(timezone.utc)
            prev_18 = datetime.combine(previous, time(18), tzinfo=tz).astimezone(timezone.utc)
            prev_21 = datetime.combine(previous, time(21), tzinfo=tz).astimezone(timezone.utc)
            meta.append((key, loc, local_now, completed_hour))
            calls.extend([
                detection_count(http, contrail_client, loc, start_today, end_today, args.radius_km),
                detection_count(http, contrail_client, loc, prev_18, prev_21, args.radius_km),
            ])
        counts = await asyncio.gather(*calls)

    # Public Kalshi active ladders; research read only.
    active_by_city: dict[str, list[dict]] = {}
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": "weather-alpha-today-panel/0.1"}) as client:
        for city, series in SERIES_BY_CITY.items():
            response = client.get(KALSHI_BASE + "/markets", params={"series_ticker": series, "status": "open", "limit": 100})
            if response.status_code != 200:
                active_by_city[city] = []
                continue
            active_by_city[city] = [dict(m) for m in response.json().get("markets", []) if isinstance(m, dict)]

    rows: list[dict] = []
    ci = 0
    for key, loc, local_now, completed_hour in meta:
        today_count, previous_evening_count = counts[ci], counts[ci + 1]
        ci += 2
        prior_cumulative: list[float] = []
        prior_evening: list[float] = []
        for day in history_dates:
            hh = historical.get((key, day), {})
            prior_cumulative.append(float(sum(hh.get(h, 0) for h in range(completed_hour))))
            prior_evening.append(float(sum(hh.get(h, 0) for h in range(18, 21))))
        q90 = float(np.quantile(prior_evening, 0.90)) if prior_evening else None

        station = str(loc.get("settlement_station") or "").upper()
        nbm = nbm_by_station.get(station)
        top_ticker = top_title = None
        top_probability = None
        if nbm is not None and key in active_by_city:
            today_markets = [m for m in active_by_city[key] if event_date(m.get("event_ticker") or m.get("ticker")) == local_now.date()]
            scored = [(market_probability(m, nbm.max_f, nbm.max_sd_f or 2.0), m) for m in today_markets]
            total = sum(p for p, _ in scored)
            if total > 0:
                scored = [(p / total, m) for p, m in scored]
                p_top, market_top = max(scored, key=lambda item: item[0])
                top_ticker, top_title, top_probability = market_top.get("ticker"), market_top.get("title"), p_top
        rows.append({
            "location_key": key, "name": loc.get("name", key), "station": station,
            "local_time": local_now.isoformat(), "completed_through_hour": completed_hour,
            "today_contrail_count": today_count,
            "matched_window_percentile": midrank_percentile(prior_cumulative, float(today_count)),
            "prior_evening_18_21_count": previous_evening_count,
            "prior_evening_q90": q90,
            "prior_evening_top90": bool(q90 is not None and previous_evening_count >= q90),
            "nbm00z_high_f": nbm.max_f if nbm else None,
            "nbm00z_sd_f": nbm.max_sd_f if nbm else None,
            "kalshi_top_ticker": top_ticker, "kalshi_top_title": top_title,
            "kalshi_top_probability": top_probability,
        })

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["City | Contrails | Matched pct | Prev eve top90 | NBM high | Kalshi top bucket", "---|---:|---:|:---:|---:|---"]
    for r in rows:
        pct = "n/a" if r["matched_window_percentile"] is None else f"{r['matched_window_percentile']:.0f}%"
        nbm = "n/a" if r["nbm00z_high_f"] is None else f"{r['nbm00z_high_f']:.0f}F"
        bucket = r["kalshi_top_title"] or "—"
        lines.append(f"{r['name']} | {r['today_contrail_count']} | {pct} | {'YES' if r['prior_evening_top90'] else 'no'} | {nbm} | {bucket}")
    args.output_text.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"json={args.output_json} text={args.output_text}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Build current NBM + contrail + Kalshi weather research panel")
    p.add_argument("--config", type=Path, default=Path("config/contrail_locations.yaml"))
    p.add_argument("--contrail-history", type=Path, default=Path("/data/weather/normalized/contrails/contrail_hourly_history.json"))
    p.add_argument("--output-json", type=Path, default=Path("/data/weather/live/weather_research_today.json"))
    p.add_argument("--output-text", type=Path, default=Path("/data/weather/live/weather_research_today.md"))
    p.add_argument("--radius-km", type=float, default=150.0)
    p.add_argument("--nbm-cycle", type=int, default=0)
    p.add_argument("--timeout", type=float, default=60.0)
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
