#!/usr/bin/env python3
"""Generate a concise current-day Google Contrails report for Stayton, Oregon.

Research only. CFI / forcing are not surface-temperature changes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from weather_alpha.providers.google_contrails import GoogleContrailsClient

DEFAULT_CONFIG = Path("config/contrail_locations.yaml")
DEFAULT_OUTPUT = Path("/data/weather/live/stayton_contrails_today.json")
DEFAULT_BASELINE = Path("/data/weather/live/stayton_contrail_30d_afternoon.json")


def percentile(values: list[float], target: float | None) -> float | None:
    if target is None or not values:
        return None
    return 100.0 * sum(v <= target for v in values) / len(values)


def load_stayton(config: Path) -> dict[str, Any]:
    payload = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    row = (payload.get("locations") or {}).get("stayton_or")
    if not isinstance(row, dict):
        raise RuntimeError("stayton_or is missing from contrail location config")
    return row


def local_day_window(tz_name: str, now_utc: datetime) -> tuple[datetime, datetime, datetime]:
    tz = ZoneInfo(tz_name)
    local_now = now_utc.astimezone(tz)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc), now_utc, local_now


def baseline_percentiles(path: Path, current: dict[str, Any], current_start_local: str, current_end_local: str) -> dict[str, Any]:
    if not path.exists():
        return {"status": "baseline_missing", "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "baseline_unreadable", "path": str(path), "error": f"{type(exc).__name__}:{exc}"}

    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {"status": "baseline_invalid", "path": str(path)}

    pairs = {
        "detection_count": current.get("detection_count"),
        "unique_detection_frames": current.get("unique_detection_frames"),
        "peak_hour_count": current.get("peak_hour_count"),
        "total_length_km": current.get("total_length_km"),
    }
    out: dict[str, Any] = {"status": "ok", "baseline_n": 0, "metrics": {}}
    for metric, target in pairs.items():
        source_key = "total_length_km_raw" if metric == "total_length_km" else metric
        vals = []
        for row in rows:
            if not isinstance(row, dict) or row.get("is_target"):
                continue
            value = row.get(source_key)
            if value is not None:
                try:
                    vals.append(float(value))
                except (TypeError, ValueError):
                    pass
        out["baseline_n"] = max(out["baseline_n"], len(vals))
        out["metrics"][metric] = {
            "target": target,
            "percentile": percentile(vals, None if target is None else float(target)),
            "median": None if not vals else median(vals),
            "n": len(vals),
        }
    return out


async def amain() -> int:
    ap = argparse.ArgumentParser(description="Report today's Stayton Google Contrails conditions")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--radius-km", type=float, default=150.0)
    ap.add_argument("--forecast-radius-km", type=float, default=35.0)
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    loc = load_stayton(args.config)
    lat, lon = float(loc["latitude"]), float(loc["longitude"])
    tz_name = str(loc["timezone"])
    now_utc = datetime.now(timezone.utc)
    start_utc, end_utc, local_now = local_day_window(tz_name, now_utc)
    satellite = "GOES-WEST-FULL-DISK"

    client = GoogleContrailsClient(timeout=args.timeout)
    det = await client.detection_summary(
        start_utc,
        end_utc,
        lat,
        lon,
        args.radius_km,
        satellite,
    )

    forecast_error = None
    forecast = None
    try:
        forecast_time = now_utc.replace(minute=0, second=0, microsecond=0)
        forecast = await client.forecast_point(
            forecast_time,
            lat,
            lon,
            radius_km=args.forecast_radius_km,
        )
    except Exception as exc:
        forecast_error = f"{type(exc).__name__}: {exc}"

    current = {
        "generated_at_utc": now_utc.isoformat(),
        "local_time": local_now.isoformat(),
        "local_date": local_now.date().isoformat(),
        "location": loc.get("name", "Stayton, Oregon"),
        "latitude": lat,
        "longitude": lon,
        "radius_km": args.radius_km,
        "window_start_utc": det.start_time.isoformat(),
        "window_end_utc": det.end_time.isoformat(),
        "detection_count": det.detection_count,
        "unique_detection_frames": det.unique_detection_frames,
        "total_length_km": round(det.total_length_km, 3),
        "max_length_km": round(det.max_length_km, 3),
        "nearest_detection_km": None if det.nearest_detection_km is None else round(det.nearest_detection_km, 3),
        "first_detection_time": None if det.first_detection_time is None else det.first_detection_time.isoformat(),
        "last_detection_time": None if det.last_detection_time is None else det.last_detection_time.isoformat(),
        "peak_hour_time": det.peak_hour_time,
        "peak_hour_count": det.peak_hour_count,
        "satellite_counts": det.satellite_counts,
        "max_cfi": None if forecast is None else forecast.max_cfi,
        "mean_cfi": None if forecast is None else forecast.mean_cfi,
        "max_persistent_formation_probability": None if forecast is None else forecast.max_persistent_formation_probability,
        "max_expected_effective_energy_forcing": None if forecast is None else forecast.max_expected_effective_energy_forcing,
        "max_nominal_cocip_effective_energy_forcing": None if forecast is None else forecast.max_nominal_cocip_effective_energy_forcing,
        "peak_flight_level": None if forecast is None else forecast.peak_flight_level,
        "forecast_error": forecast_error,
        "research_note": "CFI and energy forcing are contrail metrics, not surface-temperature changes.",
    }
    current_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%H:%M")\n    current_end_local = local_now.strftime("%H:%M")\n    current["baseline"] = baseline_percentiles(args.baseline, current, current_start_local, current_end_local)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(args.output)

    print(f"STAYTON CONTRAILS — {current['local_date']} through {local_now.strftime('%I:%M %p %Z')}")
    print(f"detections={det.detection_count} frames={det.unique_detection_frames} total_line_km={det.total_length_km:.0f}")
    nearest = "n/a" if det.nearest_detection_km is None else f"{det.nearest_detection_km:.1f} km"
    print(f"nearest={nearest} peak_hour={det.peak_hour_time or 'n/a'} peak_count={det.peak_hour_count}")
    if forecast is not None:
        print(
            "forecast "
            f"max_cfi={forecast.max_cfi} "
            f"persistent_prob={forecast.max_persistent_formation_probability} "
            f"expected_eef={forecast.max_expected_effective_energy_forcing} "
            f"peak_fl={forecast.peak_flight_level}"
        )
    else:
        print(f"forecast unavailable: {forecast_error}")

    baseline = current["baseline"]
    if baseline.get("status") == "ok":
        parts = []
        for metric in ("detection_count", "unique_detection_frames", "peak_hour_count", "total_length_km"):
            row = baseline["metrics"].get(metric, {})
            pct = row.get("percentile")
            if pct is not None:
                parts.append(f"{metric}={pct:.1f}pct")
        print("30d_baseline " + " ".join(parts))
    else:
        print(f"30d_baseline {baseline.get('status')}")

    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
