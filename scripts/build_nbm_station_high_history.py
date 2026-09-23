#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import yaml

from weather_alpha.providers.nbm import NbmTextClient


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    p = argparse.ArgumentParser(description="Build auditable NOAA NBM 00Z station daily-high history")
    p.add_argument("--start-date", type=parse_date, required=True)
    p.add_argument("--end-date", type=parse_date, required=True)
    p.add_argument("--cycle", type=int, default=0)
    p.add_argument("--config", type=Path, default=Path("config/contrail_locations.yaml"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/normalized/forecasts/nbm_station_high_history.json"))
    args = p.parse_args()
    if args.end_date < args.start_date:
        raise SystemExit("end-date must be on or after start-date")

    locations = yaml.safe_load(args.config.read_text(encoding="utf-8"))["locations"]
    stations = sorted({str(v["settlement_station"]).upper() for v in locations.values() if v.get("settlement_station")})
    station_to_locations: dict[str, list[str]] = {}
    for key, row in locations.items():
        station_to_locations.setdefault(str(row.get("settlement_station") or "").upper(), []).append(key)

    client = NbmTextClient()
    rows: list[dict] = []
    day = args.start_date
    while day <= args.end_date:
        fetched = client.fetch_station_maxes(day, stations, args.cycle)
        by_station = {x.station: x for x in fetched}
        for station in stations:
            item = by_station.get(station)
            if item is None:
                rows.append({"station": station, "date": day.isoformat(), "error": "station_not_found"})
                continue
            for location_key in station_to_locations.get(station, []):
                rows.append({
                    "location_key": location_key,
                    "station": station,
                    "date": day.isoformat(),
                    "forecast_issue_utc": item.issue_cycle_utc,
                    "nbm_max_f": item.max_f,
                    "nbm_max_sd_f": item.max_sd_f,
                    "txn_values": list(item.txn_values[:6]),
                    "xnd_values": list(item.xnd_values[:6]),
                    "model": "NOAA_NBM_NBS",
                    "source_url": item.source_url,
                    "error": None,
                })
        print(f"date={day.isoformat()} stations={len(fetched)}/{len(stations)} rows={len(rows)}", flush=True)
        day += timedelta(days=1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    errors = [x for x in rows if x.get("error")]
    print(f"output={args.output} rows={len(rows)} errors={len(errors)}")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
