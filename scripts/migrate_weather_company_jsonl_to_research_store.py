#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from weather_alpha.research.weather_company import STRATEGY_ID, WeatherCompanyResearchBridge

TRACK_STRATEGIES = {
    "A_CONTROL": "weather_company_terminal_high_A_control_v1",
    "B_MODERATE": "weather_company_terminal_high_B_moderate_v1",
    "C_EXPLORATORY": "weather_company_terminal_high_C_exploratory_v1",
    "D_DIAGNOSTIC_NO_LOCK": "weather_company_terminal_high_D_diagnostic_v1",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def side_of(row: dict[str, Any]) -> str:
    return str(row.get("side") or row.get("tournament_side") or "").upper()


def legacy_key(strategy_id: str, row: dict[str, Any]) -> tuple[str, str, str, str]:
    return strategy_id, str(row.get("ticker") or ""), str(row.get("signal_time") or ""), side_of(row)


def fixed_strategy(strategy_id: str) -> Callable[[dict[str, Any]], str]:
    return lambda _row: strategy_id


def track_strategy(row: dict[str, Any]) -> str:
    track = str(row.get("track") or "")
    if track not in TRACK_STRATEGIES:
        raise ValueError(f"unknown tournament track {track!r}")
    return TRACK_STRATEGIES[track]


def migrate_group(
    *, db: Path, signals_path: Path, fills_path: Path, settled_path: Path,
    resolve_strategy: Callable[[dict[str, Any]], str], label: str,
) -> tuple[int, int, int]:
    bridges: dict[str, WeatherCompanyResearchBridge] = {}
    signal_ids: dict[tuple[str, str, str, str], str] = {}

    def bridge(strategy_id: str) -> WeatherCompanyResearchBridge:
        if strategy_id not in bridges:
            bridges[strategy_id] = WeatherCompanyResearchBridge(db, strategy_id)
        return bridges[strategy_id]

    signals = [row for row in read_jsonl(signals_path) if row.get("paper_status") == "PENDING_DELAYED_FILL"]
    fills = [row for row in read_jsonl(fills_path) if row.get("fill_status") == "PAPER_FILLED"]
    settled = [row for row in read_jsonl(settled_path) if row.get("score_status") == "SETTLED_SCORED"]

    for row in signals:
        strategy_id = resolve_strategy(row)
        b = bridge(strategy_id)
        b.record_evaluation(row, emitted=True, reason="MIGRATED_LEGACY_PAPER_SIGNAL")
        fee = float(row.get("estimated_taker_fee") or row.get("tournament_fee_per_contract") or 0.0)
        signal_id = b.record_signal(row, estimated_fee_per_contract=fee)
        signal_ids[legacy_key(strategy_id, row)] = signal_id

    migrated_fills = 0
    for row0 in fills:
        row = dict(row0)
        strategy_id = resolve_strategy(row)
        signal_id = signal_ids.get(legacy_key(strategy_id, row))
        if not signal_id:
            continue
        row["engine_signal_id"] = signal_id
        if bridge(strategy_id).record_fill(row) is not None:
            migrated_fills += 1

    migrated_settlements = 0
    for row in settled:
        strategy_id = resolve_strategy(row)
        bridge(strategy_id).record_settlement(row, {})
        migrated_settlements += 1

    print(f"{label}: signals={len(signals)} fills={migrated_fills}/{len(fills)} settlements={migrated_settlements}/{len(settled)}")
    return len(signals), migrated_fills, migrated_settlements


def main() -> int:
    p = argparse.ArgumentParser(description="Migrate legacy Weather Company paper JSONL into the canonical research store")
    p.add_argument("--db", type=Path, default=Path("/data/weather/live/weather_research.sqlite3"))
    p.add_argument("--paper-signals", type=Path, default=Path("/data/weather/live/weather_company_paper_signals.jsonl"))
    p.add_argument("--paper-fills", type=Path, default=Path("/data/weather/live/weather_company_paper_fills.jsonl"))
    p.add_argument("--paper-settled", type=Path, default=Path("/data/weather/live/weather_company_paper_settled.jsonl"))
    p.add_argument("--tournament-signals", type=Path, default=Path("/data/weather/live/weather_company_tournament_signals.jsonl"))
    p.add_argument("--tournament-fills", type=Path, default=Path("/data/weather/live/weather_company_tournament_fills.jsonl"))
    p.add_argument("--tournament-settled", type=Path, default=Path("/data/weather/live/weather_company_tournament_settled.jsonl"))
    p.add_argument("--diagnostic-signals", type=Path, default=Path("/data/weather/live/weather_company_diagnostic_signals.jsonl"))
    p.add_argument("--diagnostic-fills", type=Path, default=Path("/data/weather/live/weather_company_diagnostic_fills.jsonl"))
    p.add_argument("--diagnostic-settled", type=Path, default=Path("/data/weather/live/weather_company_diagnostic_settled.jsonl"))
    args = p.parse_args()

    totals = [
        migrate_group(db=args.db, signals_path=args.paper_signals, fills_path=args.paper_fills,
                      settled_path=args.paper_settled, resolve_strategy=fixed_strategy(STRATEGY_ID), label="paper"),
        migrate_group(db=args.db, signals_path=args.tournament_signals, fills_path=args.tournament_fills,
                      settled_path=args.tournament_settled, resolve_strategy=track_strategy, label="tournament"),
        migrate_group(db=args.db, signals_path=args.diagnostic_signals, fills_path=args.diagnostic_fills,
                      settled_path=args.diagnostic_settled,
                      resolve_strategy=fixed_strategy(TRACK_STRATEGIES["D_DIAGNOSTIC_NO_LOCK"]), label="diagnostic"),
    ]
    print(f"total_signals={sum(x[0] for x in totals)} total_fills={sum(x[1] for x in totals)} total_settlements={sum(x[2] for x in totals)} db={args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
