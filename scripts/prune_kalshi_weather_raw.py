#!/usr/bin/env python3
"""Delete raw Kalshi weather L2 only after validated compact replay exists.

Default mode is dry-run. --execute is required for deletion. A full day is eligible
only after the entire UTC day is older than retention_days and its manifest is
VALIDATED with exact source/output hashes.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts.compact_kalshi_weather_l2_day import sha256_file, write_manifest


def day_from_manifest(path: Path) -> date:
    return date.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["date"])


def eligible_by_age(day: date, *, now: datetime, retention_days: int) -> bool:
    day_end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    return day_end <= now.astimezone(timezone.utc) - timedelta(days=retention_days)


def verify_manifest_for_prune(manifest: dict, manifest_path: Path) -> list[str]:
    errors: list[str] = []
    if manifest.get("status") != "VALIDATED" or not manifest.get("retention_eligible"):
        errors.append("manifest_not_validated")
    if not manifest.get("complete_day"):
        errors.append("incomplete_day")
    for name, meta in (manifest.get("outputs") or {}).items():
        path = Path(meta["path"])
        if not path.exists():
            errors.append(f"missing_output:{name}")
        elif sha256_file(path) != meta["sha256"]:
            errors.append(f"output_hash_mismatch:{name}")
    for meta in manifest.get("source_files") or []:
        path = Path(meta["path"])
        if not path.exists():
            if manifest.get("raw_deleted_at"):
                continue
            errors.append(f"missing_raw:{path}")
        elif sha256_file(path) != meta["sha256"]:
            errors.append(f"raw_hash_mismatch:{path}")
    return errors


def main() -> int:
    p = argparse.ArgumentParser(description="Fail-closed 7-day raw Kalshi L2 retention")
    p.add_argument("--compact-root", type=Path, default=Path("/data/weather/compact/kalshi_l2"))
    p.add_argument("--retention-days", type=int, default=7)
    p.add_argument("--now", help="Override UTC now for tests, ISO-8601")
    p.add_argument("--execute", action="store_true", help="Actually delete; default is dry-run")
    args = p.parse_args()

    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")).astimezone(timezone.utc) if args.now else datetime.now(timezone.utc)
    manifests = sorted(args.compact_root.glob("*/*/*/manifest.json"))
    eligible: list[tuple[Path, dict]] = []
    blocked: list[dict] = []
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        day = date.fromisoformat(str(manifest["date"]))
        if not eligible_by_age(day, now=now, retention_days=args.retention_days):
            continue
        errors = verify_manifest_for_prune(manifest, path)
        if errors:
            blocked.append({"date": day.isoformat(), "manifest": str(path), "errors": errors})
            continue
        if manifest.get("raw_deleted_at"):
            continue
        eligible.append((path, manifest))

    deleted_files = 0
    deleted_bytes = 0
    for manifest_path, manifest in eligible:
        sources = [Path(x["path"]) for x in manifest.get("source_files") or []]
        day_bytes = sum(x.stat().st_size for x in sources if x.exists())
        if args.execute:
            for source in sources:
                if source.exists():
                    size = source.stat().st_size
                    source.unlink()
                    deleted_files += 1
                    deleted_bytes += size
            manifest["raw_deleted_at"] = now.isoformat()
            manifest["raw_deleted_files"] = len(sources)
            manifest["raw_deleted_bytes"] = day_bytes
            write_manifest(manifest_path, manifest)
        else:
            deleted_files += len(sources)
            deleted_bytes += day_bytes

    print(json.dumps({
        "mode": "EXECUTE" if args.execute else "DRY_RUN",
        "now": now.isoformat(),
        "retention_days": args.retention_days,
        "eligible_days": [m["date"] for _, m in eligible],
        "blocked": blocked,
        "files": deleted_files,
        "bytes": deleted_bytes,
    }, sort_keys=True))
    return 0 if not blocked else 2


if __name__ == "__main__":
    raise SystemExit(main())
