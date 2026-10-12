#!/usr/bin/env python3
"""Read-only acceptance check for a Polymarket US collector handover."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

VERSION = "polymarket-us-weather-snapshot-v1"

def timestamp(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return stamp.astimezone(timezone.utc)

def check(health, current, *, now, max_age_seconds=150, after=None):
    errors = []
    if max_age_seconds <= 0:
        return ["invalid_max_age"]
    stamps = []
    for name, row, field in (("health", health, "generated_at"),
                              ("current", current, "local_receive_timestamp")):
        try:
            stamp = timestamp(row.get(field))
            age = (now - stamp).total_seconds()
            if not 0 <= age <= max_age_seconds:
                errors.append(name + "_stale_or_future")
            stamps.append(stamp)
        except (ValueError, TypeError, OverflowError):
            errors.append(name + "_invalid_timestamp")
        if row.get("collector_version") != VERSION:
            errors.append(name + "_wrong_version")
        if row.get("read_only") is not True:
            errors.append(name + "_not_read_only")
        for field in ("event_count", "market_count"):
            value = row.get(field)
            if type(value) is not int or value <= 0:
                errors.append(name + "_invalid_" + field)
    if len(stamps) == 2:
        if stamps[0] != stamps[1]:
            errors.append("snapshot_health_not_same_cycle")
        if after is not None and not all(stamp > after for stamp in stamps):
            errors.append("no_new_successful_cycle")
    if health.get("last_error") is not None or health.get("consecutive_failures") != 0:
        errors.append("collector_error")
    if current.get("live_order_submission") is not False:
        errors.append("orders_not_explicitly_disabled")
    for field in ("event_count", "market_count"):
        if health.get(field) != current.get(field):
            errors.append("count_mismatch_" + field)
    if not isinstance(current.get("markets"), list) or len(current["markets"]) != current.get("market_count"):
        errors.append("market_rows_mismatch")
    return errors

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--health", type=Path, default=Path("/data/weather/status/polymarket_us_weather_health.json"))
    p.add_argument("--current", type=Path, default=Path("/data/weather/status/polymarket_us_weather_current.json"))
    p.add_argument("--max-age-seconds", type=float, default=150)
    p.add_argument("--after", help="Require a successful cycle newer than this timezone-aware timestamp")
    args = p.parse_args()
    try:
        health = json.loads(args.health.read_text())
        current = json.loads(args.current.read_text())
        if not isinstance(health, dict) or not isinstance(current, dict):
            raise ValueError("health/current must be objects")
        errors = check(health, current, now=datetime.now(timezone.utc),
                       max_age_seconds=args.max_age_seconds,
                       after=timestamp(args.after) if args.after else None)
    except (OSError, ValueError, TypeError) as exc:
        errors = [type(exc).__name__ + ": " + str(exc)]
    print(json.dumps({"status": "FAIL" if errors else "PASS", "errors": errors}))
    return 2 if errors else 0

if __name__ == "__main__":
    raise SystemExit(main())
