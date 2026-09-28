#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weather_alpha.ops.health import StrategyHealth


def main() -> int:
    p = argparse.ArgumentParser(description="Append the most recent complete day to contrail hourly history")
    p.add_argument("--output", type=Path, default=Path("/data/weather/normalized/contrails/contrail_hourly_history.json"))
    p.add_argument("--concurrency", type=int, default=8)
    args = p.parse_args()
    health = StrategyHealth("contrail_hourly_history")
    if not os.environ.get("GOOGLE_CONTRAILS_API_KEY"):
        health.report("ERROR", "GOOGLE_CONTRAILS_API_KEY_MISSING")
        return 2

    day = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    root = Path(__file__).resolve().parents[1]
    cmd = [
        sys.executable, str(root / "scripts/build_contrail_hourly_history.py"),
        "--start-date", day, "--end-date", day,
        "--output", str(args.output), "--concurrency", str(args.concurrency),
        "--merge-existing",
    ]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, env=os.environ.copy(), timeout=300)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout)[-1200:]
        health.report("ERROR", "HISTORY_REFRESH_FAILED", date=day, detail=detail)
        print(detail, flush=True)
        return proc.returncode
    try:
        rows = json.loads(args.output.read_text(encoding="utf-8"))
    except Exception as exc:
        health.report("ERROR", "HISTORY_OUTPUT_UNREADABLE", detail=f"{type(exc).__name__}:{exc}")
        return 3
    dates = sorted({str(r.get("local_date")) for r in rows if isinstance(r, dict) and not r.get("error")})
    health.report(
        "OK", "HISTORY_CURRENT", refreshed_date=day, rows=len(rows),
        first_date=dates[0] if dates else None, last_date=dates[-1] if dates else None,
    )
    print(f"contrail_history refreshed={day} rows={len(rows)} last={dates[-1] if dates else None}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
