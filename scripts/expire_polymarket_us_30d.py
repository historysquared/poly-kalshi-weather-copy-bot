#!/usr/bin/env python3
"""Expire only completed Polymarket US UTC-day archives older than 30 days."""
import argparse
import fcntl
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAW_ROOT = Path("/data/weather/rolling30/polymarket_us/raw")
NAME = re.compile(r"polymarket_us_weather_(\d{4}-\d{2}-\d{2})T([01]\d|2[0-3])\.jsonl\.gz")
DAYS = 30

def expire(root, *, now, execute=False):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    root = root.absolute()
    if not root.is_dir() or root.resolve() != root:
        raise ValueError("raw root missing or contains symlink")
    cutoff = now.astimezone(timezone.utc) - timedelta(days=DAYS)
    result = {"mode": "EXECUTE" if execute else "DRY_RUN", "retention_days": DAYS,
              "cutoff_utc": cutoff.isoformat(), "eligible_files": 0, "bytes": 0,
              "deleted_files": 0, "blocked": []}
    for day_dir in sorted(root.glob("*/*/*")):
        relative = day_dir.relative_to(root)
        if any(p.is_symlink() for p in [day_dir, day_dir.parent, day_dir.parent.parent]):
            result["blocked"].append(str(relative) + ":symlink")
            continue
        try:
            day = date.fromisoformat("-".join(relative.parts))
            if relative.parts != (f"{day.year:04d}", f"{day.month:02d}", f"{day.day:02d}"):
                raise ValueError()
        except ValueError:
            result["blocked"].append(str(relative) + ":invalid_day")
            continue
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), timezone.utc)
        if end > cutoff:
            continue
        if not day_dir.is_dir():
            result["blocked"].append(str(relative) + ":not_directory")
            continue
        candidates = []
        for path in sorted(day_dir.iterdir()):
            match = NAME.fullmatch(path.name)
            if path.is_symlink() or not path.is_file() or not match or match[1] != day.isoformat():
                result["blocked"].append(str(relative) + ":unexpected_entry")
                candidates = None
                break
            st = path.stat()
            if st.st_mtime > cutoff.timestamp():
                result["blocked"].append(str(relative) + ":recently_modified")
                candidates = None
                break
            candidates.append((path, st))
        if candidates is None:
            continue
        for path, before in candidates:
            current = path.stat(follow_symlinks=False)
            fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            if path.is_symlink() or fingerprint(current) != fingerprint(before):
                result["blocked"].append(str(relative) + ":changed_file")
                continue
            result["eligible_files"] += 1
            result["bytes"] += before.st_size
            if execute:
                path.unlink()
                result["deleted_files"] += 1
        if execute and not any(day_dir.iterdir()):
            day_dir.rmdir()
    return result

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true", help="Default is dry-run")
    args = p.parse_args()
    RAW_ROOT.parent.mkdir(parents=True, exist_ok=True)
    with (RAW_ROOT.parent / "retention.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = expire(RAW_ROOT, now=datetime.now(timezone.utc), execute=args.execute)
        status = RAW_ROOT.parent / "retention-status.json"
        temp = status.with_suffix(".tmp")
        temp.write_text(json.dumps(result, indent=2) + "\n")
        temp.replace(status)
    print(json.dumps(result))
    return 2 if result["blocked"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
