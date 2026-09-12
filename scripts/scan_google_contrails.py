#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
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


def today_window(timezone_name: str, now_utc: datetime | None = None) -> tuple[datetime, datetime]:
    now_utc = now_utc or datetime.now(timezone.utc)
    local_now = now_utc.astimezone(ZoneInfo(timezone_name))
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc), now_utc.astimezone(timezone.utc)


def send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error: {payload}")


def alert_text(name: str, station: str | None, det, forecast, alert: bool) -> str:
    nearest = "n/a" if det.nearest_detection_km is None else f"{det.nearest_detection_km:.0f} km"
    cfi = "n/a" if forecast.max_cfi is None else f"{forecast.max_cfi:.2f}/4"
    probability = (
        "n/a"
        if forecast.max_persistent_formation_probability is None
        else f"{forecast.max_persistent_formation_probability:.1f}%"
    )
    icon = "🚨" if alert else "☁️"
    lines = [
        f"{icon} CONTRAIL WEATHER SIGNAL — {name}",
        f"Station: {station or 'n/a'}",
        f"Google detections: {det.detection_count}",
        f"Detected line length: {det.total_length_km:.0f} km",
        f"Nearest detected line: {nearest}",
        f"Current max CFI: {cfi}",
        f"Persistent-formation probability: {probability}",
    ]
    if forecast.peak_flight_level is not None:
        lines.append(f"Peak flight level: FL{forecast.peak_flight_level}")
    lines.append("CFI measures contrail-warming severity, not surface temperature degrees.")
    return "\n".join(lines)


async def scan_one(
    client: GoogleContrailsClient,
    key: str,
    loc: dict[str, Any],
    *,
    radius_km: float,
    min_count: int,
    min_length_km: float,
    min_cfi: float,
    fixed_start: datetime | None,
    fixed_end: datetime | None,
) -> dict[str, Any]:
    lat = float(loc["latitude"])
    lon = float(loc["longitude"])
    tz = str(loc["timezone"])
    start, end = (fixed_start, fixed_end) if fixed_start and fixed_end else today_window(tz)
    satellite = "GOES-WEST-FULL-DISK" if lon < -100 else "GOES-EAST-FULL-DISK"

    det = await client.detection_summary(start, end, lat, lon, radius_km, satellite)

    # Forecast grids are hourly. Round down so the request is deterministic.
    forecast_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    forecast = await client.forecast_point(forecast_time, lat, lon)

    alert = (
        det.detection_count >= min_count
        or det.total_length_km >= min_length_km
        or (forecast.max_cfi or 0) >= min_cfi
    )
    return {
        "key": key,
        "name": loc.get("name", key),
        "station": loc.get("settlement_station"),
        "venues": loc.get("venues", []),
        "alert": alert,
        "window_start": det.start_time.isoformat(),
        "window_end": det.end_time.isoformat(),
        "detection_count": det.detection_count,
        "total_length_km": round(det.total_length_km, 3),
        "max_length_km": round(det.max_length_km, 3),
        "nearest_detection_km": (
            None if det.nearest_detection_km is None else round(det.nearest_detection_km, 3)
        ),
        "satellite_counts": det.satellite_counts,
        "max_cfi": forecast.max_cfi,
        "mean_cfi": forecast.mean_cfi,
        "max_persistent_formation_probability": forecast.max_persistent_formation_probability,
        "max_nominal_cocip_effective_energy_forcing": (
            forecast.max_nominal_cocip_effective_energy_forcing
        ),
        "peak_flight_level": forecast.peak_flight_level,
        "telegram_text": alert_text(
            str(loc.get("name", key)),
            loc.get("settlement_station"),
            det,
            forecast,
            alert,
        ),
    }


async def amain() -> int:
    ap = argparse.ArgumentParser(
        description="Google Contrails scan for weather-market cities and Stayton"
    )
    ap.add_argument(
        "--config",
        type=Path,
        default=Path("config/contrail_locations.yaml"),
    )
    ap.add_argument(
        "--location",
        action="append",
        help="location key; repeatable. Default: all",
    )
    ap.add_argument(
        "--venue",
        choices=["kalshi", "polymarket_us", "research"],
        help="filter locations by venue tag",
    )
    ap.add_argument("--radius-km", type=float, default=150.0)
    ap.add_argument("--min-count", type=int, default=5)
    ap.add_argument("--min-total-length-km", type=float, default=150.0)
    ap.add_argument("--min-cfi", type=float, default=2.0)
    ap.add_argument("--start", help="UTC ISO start time; must be paired with --end")
    ap.add_argument("--end", help="UTC ISO end time; must be paired with --start")
    ap.add_argument("--telegram", action="store_true", help="send Telegram only for alert=true")
    ap.add_argument(
        "--telegram-all",
        action="store_true",
        help="send Telegram for every scanned location",
    )
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()

    locations = load_locations(args.config)
    keys = args.location or list(locations)
    missing = [key for key in keys if key not in locations]
    if missing:
        raise SystemExit(f"unknown location(s): {', '.join(missing)}")

    selected = [
        (key, locations[key])
        for key in keys
        if not args.venue or args.venue in locations[key].get("venues", [])
    ]

    if bool(args.start) != bool(args.end):
        raise SystemExit("--start and --end must be supplied together")
    fixed_start = (
        datetime.fromisoformat(args.start.replace("Z", "+00:00"))
        if args.start
        else None
    )
    fixed_end = (
        datetime.fromisoformat(args.end.replace("Z", "+00:00"))
        if args.end
        else None
    )

    client = GoogleContrailsClient()
    results: list[dict[str, Any]] = []
    for key, loc in selected:
        try:
            row = await scan_one(
                client,
                key,
                loc,
                radius_km=args.radius_km,
                min_count=args.min_count,
                min_length_km=args.min_total_length_km,
                min_cfi=args.min_cfi,
                fixed_start=fixed_start,
                fixed_end=fixed_end,
            )
        except Exception as exc:
            row = {
                "key": key,
                "name": loc.get("name", key),
                "error": f"{type(exc).__name__}: {exc}",
            }

        results.append(row)
        print(json.dumps(row, sort_keys=True, default=str), flush=True)

        if "error" not in row and (
            (args.telegram and row["alert"]) or args.telegram_all
        ):
            send_telegram(row["telegram_text"])

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(results, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
