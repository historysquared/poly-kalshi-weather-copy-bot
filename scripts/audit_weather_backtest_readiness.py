from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def parquet_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return pq.read_table(path).to_pylist()


def uniq(rows: list[dict[str, Any]], key: str) -> set[str]:
    return {str(r.get(key)) for r in rows if r.get(key) not in (None, "")}


def file_status(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    return f"OK size_mb={path.stat().st_size / 1024 / 1024:.1f}"


def main() -> int:
    p = argparse.ArgumentParser(description="Audit local historical/live weather data coverage and backtest readiness")
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_bulk_resolved.parquet"))
    p.add_argument("--replay", type=Path, default=Path("/data/weather/normalized/orderbooks/kalshi_weather_pmxt_bulk.parquet"))
    p.add_argument("--cli", type=Path, default=Path("/data/weather/normalized/settlements/nws_cli_daily.parquet"))
    p.add_argument("--pmxt-manifest", type=Path, default=Path("/data/weather/manifests/kalshi_weather_pmxt_bulk.jsonl"))
    p.add_argument("--signals", type=Path, default=Path("/data/weather/results/settlement_reconstruction/causal_settlement_pmxt_bulk.parquet"))
    p.add_argument("--summary", type=Path, default=Path("/data/weather/results/settlement_reconstruction/causal_trade_backtest_bulk_summary.parquet"))
    p.add_argument("--robustness", type=Path, default=Path("/data/weather/results/settlement_reconstruction/date_block_robustness_summary.parquet"))
    p.add_argument("--live-contract-history", type=Path, default=Path("/data/weather/live/weather_company_contract_history.jsonl"))
    args = p.parse_args()

    print("WEATHER BACKTEST READINESS AUDIT")
    print("===============================")
    for name, path in (
        ("catalog", args.catalog), ("replay", args.replay), ("cli", args.cli),
        ("pmxt_manifest", args.pmxt_manifest), ("signals", args.signals),
        ("summary", args.summary), ("robustness", args.robustness),
        ("live_contract_history", args.live_contract_history),
    ):
        print(f"{name:22} {file_status(path)} {path}")

    catalog = parquet_rows(args.catalog)
    exact = [r for r in catalog if r.get("status") == "EXACT"]
    print("\nCATALOG")
    print(f"rows={len(catalog)} exact_contracts={len(exact)} exact_events={len(uniq(exact, 'weather_event_id'))} "
          f"dates={len(uniq(exact, 'settlement_date'))} stations={len(uniq(exact, 'station'))}")
    if exact:
        dates = sorted(uniq(exact, "settlement_date"))
        print(f"date_min={dates[0]} date_max={dates[-1]}")
        print(f"station_counts={dict(Counter(str(r.get('station')) for r in exact))}")

    manifest = read_jsonl(args.pmxt_manifest)
    print("\nPMXT ARCHIVE COVERAGE")
    if not manifest:
        print("manifest_rows=0")
    else:
        sc = Counter(str(r.get("status") or "UNKNOWN") for r in manifest)
        hours = sorted(str(r.get("hour_utc")) for r in manifest if r.get("hour_utc"))
        print(f"manifest_rows={len(manifest)} status_counts={dict(sc)}")
        if hours:
            print(f"hour_min={hours[0]} hour_max={hours[-1]}")
        local = [r for r in manifest if r.get("status") in {"LOCAL", "DOWNLOADED"}]
        print(f"local_or_downloaded_hours={len(local)}")

    if args.replay.exists():
        pf = pq.ParquetFile(args.replay)
        print("\nCOMPACT REPLAY")
        print(f"rows={pf.metadata.num_rows} row_groups={pf.metadata.num_row_groups} columns={pf.schema_arrow.names}")

    cli = parquet_rows(args.cli)
    print("\nSETTLEMENT TRUTH")
    print(f"cli_rows={len(cli)} cli_dates={len(uniq(cli, 'valid_date'))} cli_stations={len(uniq(cli, 'station'))}")

    sig = parquet_rows(args.signals)
    print("\nCAUSAL SIGNAL SET")
    if not sig:
        print("rows=0")
    else:
        statuses = Counter(str(r.get("status") or "UNKNOWN") for r in sig)
        candidate_dates = {str(r.get("settlement_date")) for r in sig if r.get("status") == "CANDIDATE"}
        candidate_events = {(str(r.get("station")), str(r.get("settlement_date"))) for r in sig if r.get("status") == "CANDIDATE"}
        print(f"rows={len(sig)} status_counts={dict(statuses)}")
        print(f"candidate_physical_events={len(candidate_events)} candidate_dates={len(candidate_dates)}")

    summary = parquet_rows(args.summary)
    print("\nECONOMIC BACKTEST")
    if not summary:
        print("summary_rows=0")
    else:
        executed = [r for r in summary if int(r.get("executed_trades") or 0) > 0]
        ranked = sorted(executed, key=lambda r: float(r.get("roi") or -999), reverse=True)
        print(f"parameter_cells={len(summary)} executable_cells={len(executed)}")
        for r in ranked[:8]:
            print("cell " + " ".join([
                f"latency={r.get('latency_seconds')}", f"depth={r.get('depth_contracts')}",
                f"floor={r.get('price_floor')}", f"trades={r.get('executed_trades')}",
                f"wins={r.get('wins')}", f"losses={r.get('losses')}", f"roi={r.get('roi')}",
                f"dates={r.get('settlement_dates')}", f"bootstrap={r.get('bootstrap_status')}",
            ]))

    robust = parquet_rows(args.robustness)
    print("\nDATE-BLOCK ROBUSTNESS")
    if not robust:
        print("rows=0 or file missing")
    else:
        verdicts = Counter(str(r.get("robustness_verdict") or r.get("verdict") or "UNKNOWN") for r in robust)
        print(f"rows={len(robust)} verdict_counts={dict(verdicts)}")

    live = read_jsonl(args.live_contract_history)
    print("\nFORWARD DATA")
    print(f"contract_history_rows={len(live)} snapshots={len(uniq(live, 'snapshot_time'))} "
          f"events={len(uniq(live, 'event_id'))} stations={len(uniq(live, 'station'))}")

    exact_dates = len(uniq(exact, "settlement_date"))
    local_hours = sum(1 for r in manifest if r.get("status") in {"LOCAL", "DOWNLOADED"})
    candidate_dates = len({str(r.get("settlement_date")) for r in sig if r.get("status") == "CANDIDATE"}) if sig else 0
    print("\nREADINESS VERDICT")
    checks = {
        "settlement_truth": bool(exact and cli),
        "executable_books": bool(args.replay.exists() and local_hours > 0),
        "causal_candidates": candidate_dates >= 20,
        "serious_candidate_dates_50": candidate_dates >= 50,
        "preferred_dates_100": candidate_dates >= 100,
        "forward_collection": len(live) > 0,
    }
    for k, v in checks.items():
        print(f"{k}={'PASS' if v else 'NEEDS_WORK'}")
    print(f"catalog_dates={exact_dates} pmxt_local_hours={local_hours} candidate_dates={candidate_dates}")
    print("next=expand PMXT recent-first; rebuild compact replay; extend ASOS/model features; rerun frozen chronological OOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
