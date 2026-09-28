#!/usr/bin/env python3
"""Forward monitor for the corrected prior-evening contrail top-decile feature.

This process deliberately emits research triggers, not trade recommendations. The
historical q90 study remains insufficient for a frozen executable trading rule.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from weather_alpha.ops.health import StrategyHealth


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_state(path: Path) -> dict[str, Any]:
    try:
        value = load_json(path)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def build_panel(root: Path, args: argparse.Namespace) -> tuple[int, str]:
    cmd = [
        sys.executable, str(root / "scripts/build_weather_today_research_panel.py"),
        "--contrail-history", str(args.history),
        "--output-json", str(args.panel),
        "--output-text", str(args.panel_text),
    ]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, env=os.environ.copy(), timeout=180)
    return proc.returncode, (proc.stderr or proc.stdout)[-1600:]


def cycle(root: Path, args: argparse.Namespace, state: dict[str, Any], health: StrategyHealth) -> None:
    code, detail = build_panel(root, args)
    if code != 0:
        health.report("ERROR", "CONTRAIL_PANEL_BUILD_FAILED", detail=detail)
        print(f"contrail_q90_error={detail}", flush=True)
        return
    rows = load_json(args.panel)
    if not isinstance(rows, list):
        health.report("ERROR", "CONTRAIL_PANEL_INVALID")
        return

    triggers = 0
    seen = state.setdefault("seen", {})
    insufficient: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("name", "").startswith("Stayton"):
            continue
        history_n = int(row.get("prior_evening_history_n") or 0)
        if history_n < args.min_history_days:
            insufficient.append(f"{row.get('location_key')}:{history_n}")
            continue
        if not row.get("prior_evening_top90"):
            continue
        key = f"{row.get('location_key')}|{str(row.get('local_time') or '')[:10]}"
        if key in seen:
            continue
        trigger = {
            "strategy": "contrail_q90_prior_evening_v1",
            "kind": "CONTRAIL_RESEARCH_TRIGGER",
            "signal_time": row.get("local_time"),
            "location_key": row.get("location_key"),
            "city": row.get("name"),
            "station": row.get("station"),
            "prior_evening_count": row.get("prior_evening_18_21_count"),
            "prior_evening_percentile": row.get("prior_evening_percentile"),
            "prior_evening_q90": row.get("prior_evening_q90"),
            "history_n": history_n,
            "today_contrail_count": row.get("today_contrail_count"),
            "matched_window_percentile": row.get("matched_window_percentile"),
            "nbm00z_high_f": row.get("nbm00z_high_f"),
            "nbm00z_sd_f": row.get("nbm00z_sd_f"),
            "kalshi_top_ticker": row.get("kalshi_top_ticker"),
            "kalshi_top_title": row.get("kalshi_top_title"),
            "kalshi_top_probability": row.get("kalshi_top_probability"),
            "trade_decision": "ABSTAIN_RESEARCH_ONLY",
            "reason": "Corrected q90 feature triggered, but the audited historical test does not yet support an executable frozen trade rule.",
            "live_order_submission": False,
        }
        append_jsonl(args.triggers, trigger)
        seen[key] = trigger["signal_time"]
        triggers += 1
        print("CONTRAIL_Q90_TRIGGER " + json.dumps(trigger, sort_keys=True), flush=True)

    save_state(args.state, state)
    if insufficient:
        health.report(
            "DEGRADED", "INSUFFICIENT_CONTRAIL_HISTORY",
            insufficient=insufficient[:20], rows=len(rows), triggers=triggers,
        )
    else:
        health.report(
            "ABSTAIN", "RESEARCH_TRIGGER_ONLY_NOT_TRADE_VALIDATED",
            rows=len(rows), triggers=triggers,
            note="Forward feature monitor is healthy; trading remains intentionally disabled.",
        )
    print(f"contrail_q90_cycle rows={len(rows)} triggers={triggers} seen={len(seen)}", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description="Forward contrail q90 research monitor; no orders")
    p.add_argument("--history", type=Path, default=Path("/data/weather/normalized/contrails/contrail_hourly_history.json"))
    p.add_argument("--panel", type=Path, default=Path("/data/weather/live/weather_research_today.json"))
    p.add_argument("--panel-text", type=Path, default=Path("/data/weather/live/weather_research_today.md"))
    p.add_argument("--triggers", type=Path, default=Path("/data/weather/live/contrail_q90_triggers.jsonl"))
    p.add_argument("--state", type=Path, default=Path("/data/weather/live/contrail_q90_monitor_state.json"))
    p.add_argument("--min-history-days", type=int, default=20)
    p.add_argument("--loop-seconds", type=int, default=900)
    p.add_argument("--once", action="store_true")
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    health = StrategyHealth("contrail_q90_prior_evening_v1")
    state = load_state(args.state)
    print("mode=CONTRAIL_Q90_FORWARD_RESEARCH live_order_submission=false trade_eligible=false", flush=True)
    while True:
        try:
            cycle(root, args, state, health)
        except KeyboardInterrupt:
            save_state(args.state, state)
            raise
        except Exception as exc:
            health.report("ERROR", f"{type(exc).__name__}:{exc}")
            print(f"contrail_q90_error={type(exc).__name__}:{exc}", flush=True)
        if args.once:
            break
        time.sleep(max(60, args.loop_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
