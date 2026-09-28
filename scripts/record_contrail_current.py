#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from weather_alpha.ops.health import StrategyHealth


def main() -> int:
    ap = argparse.ArgumentParser(description="Record current all-city contrail snapshot with explicit health")
    ap.add_argument("--output", type=Path, default=Path("/data/weather/live/contrails_current.json"))
    ap.add_argument("--history-output", type=Path, default=Path("/data/weather/live/contrails_current_history.jsonl"))
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    health = StrategyHealth("contrail_current_collector")
    root = Path(__file__).resolve().parents[1]
    if not os.environ.get("GOOGLE_CONTRAILS_API_KEY"):
        health.report("ERROR", "GOOGLE_CONTRAILS_API_KEY_MISSING")
        print("contrail_current_error=GOOGLE_CONTRAILS_API_KEY_MISSING", flush=True)
        return 2

    cmd = [
        sys.executable, str(root / "scripts/scan_google_contrails.py"),
        "--concurrency", str(max(1, args.concurrency)),
        "--output", str(args.output),
        "--history-output", str(args.history_output),
    ]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, env=os.environ.copy(), timeout=240)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout)[-1200:]
        health.report("ERROR", "CONTRAIL_SCAN_FAILED", detail=detail)
        print(f"contrail_current_error={detail}", flush=True)
        return proc.returncode

    try:
        rows = json.loads(args.output.read_text(encoding="utf-8"))
    except Exception as exc:
        health.report("ERROR", "CONTRAIL_OUTPUT_UNREADABLE", detail=f"{type(exc).__name__}:{exc}")
        return 3
    if not isinstance(rows, list):
        health.report("ERROR", "CONTRAIL_OUTPUT_INVALID")
        return 4

    errors = [r for r in rows if isinstance(r, dict) and r.get("error")]
    if errors:
        health.report(
            "DEGRADED", "PARTIAL_LOCATION_FAILURE",
            locations=len(rows), error_count=len(errors),
            errors=[f"{r.get('key')}:{r.get('error')}" for r in errors[:10]],
        )
    else:
        health.report("OK", "FRESH_SNAPSHOT", locations=len(rows))
    print(f"contrail_current locations={len(rows)} errors={len(errors)} output={args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
