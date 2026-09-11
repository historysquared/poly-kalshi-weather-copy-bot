from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.backtest.bulk_weather_pmxt import local_archive_state, plan_archive_hours
from weather_alpha.backtest.kalshi_pmxt import download_kalshi_hour, read_kalshi_parquet


def probe(client: httpx.Client, url: str) -> tuple[str, int | None]:
    try:
        response = client.head(url)
        if response.status_code == 404:
            return "ARCHIVE_MISSING", None
        if response.status_code in {200, 206}:
            size = response.headers.get("content-length")
            return "AVAILABLE", int(size) if size and size.isdigit() else None
        if response.status_code == 405:
            with client.stream("GET", url) as stream:
                if stream.status_code == 404:
                    return "ARCHIVE_MISSING", None
                stream.raise_for_status()
                size = stream.headers.get("content-length")
                return "AVAILABLE", int(size) if size and size.isdigit() else None
        response.raise_for_status()
        return "AVAILABLE", None
    except Exception as exc:
        return f"PROBE_ERROR:{type(exc).__name__}", None


def _write_manifest(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, sort_keys=True, default=str) for r in rows) + ("\n" if rows else ""), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Probe, download, and index PMXT Kalshi archive hours needed by the bulk EXACT weather catalog")
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_bulk_resolved.parquet"))
    p.add_argument("--pmxt-dir", type=Path, default=Path("/data/weather/raw/kalshi/pmxt_orderbooks_bulk"))
    p.add_argument("--manifest", type=Path, default=Path("/data/weather/manifests/kalshi_weather_pmxt_bulk.jsonl"))
    p.add_argument("--final-hours", type=int, default=6)
    p.add_argument("--order", choices=("recent-first", "oldest-first"), default="recent-first",
                   help="Archive traversal order. recent-first is the research default so newest verifiable days arrive first.")
    p.add_argument("--probe-only", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--max-download-hours", type=int, default=0, help="0 means unlimited; useful for staged runs")
    p.add_argument("--sleep", type=float, default=0.02)
    args = p.parse_args()

    catalog = pq.read_table(args.catalog).to_pylist()
    exact_tickers = {str(r.get("contract_id") or "") for r in catalog if r.get("status") == "EXACT" and r.get("contract_id")}
    plan = plan_archive_hours(catalog, final_hours=args.final_hours)
    plan = sorted(plan, key=lambda item: item.hour_utc, reverse=args.order == "recent-first")
    args.pmxt_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    downloaded = 0
    with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent":"weather-alpha-lab/0.6"}) as client:
        for i, item in enumerate(plan, 1):
            local_state = local_archive_state(args.pmxt_dir, item)
            remote_state = None
            content_length = None
            path = args.pmxt_dir / item.filename
            if local_state == "LOCAL":
                status = "LOCAL"
            else:
                remote_state, content_length = probe(client, item.url)
                if remote_state != "AVAILABLE":
                    status = remote_state
                elif args.probe_only:
                    status = "AVAILABLE_NOT_DOWNLOADED"
                elif args.max_download_hours and downloaded >= args.max_download_hours:
                    status = "AVAILABLE_DEFERRED_LIMIT"
                else:
                    try:
                        path = download_kalshi_hour(item.hour_utc, args.pmxt_dir)
                        downloaded += 1
                        status = "DOWNLOADED"
                    except FileNotFoundError:
                        status = "ARCHIVE_MISSING"
                    except Exception as exc:
                        status = f"DOWNLOAD_ERROR:{type(exc).__name__}"
            counters[status] += 1
            row = {
                "order": args.order,
                "hour_utc": item.hour_utc.isoformat(),
                "station_example": item.station,
                "settlement_date_example": item.settlement_date.isoformat(),
                "weather_event_id_example": item.weather_event_id,
                "filename": item.filename,
                "url": item.url,
                "local_path": str(path),
                "local_state_before": local_state,
                "remote_state": remote_state,
                "content_length": content_length,
                "status": status,
            }
            manifest_rows.append(row)
            if i % 50 == 0 or status.startswith("DOWNLOAD_ERROR"):
                newest = plan[0].hour_utc.isoformat() if plan else None
                oldest = plan[-1].hour_utc.isoformat() if plan else None
                print(f"progress={i}/{len(plan)} order={args.order} newest={newest} oldest={oldest} counts={dict(counters)}")
                _write_manifest(manifest_rows, args.manifest)
            time.sleep(max(0.0, args.sleep))

    _write_manifest(manifest_rows, args.manifest)

    # Audit weather relevance of every locally available hour without creating a second large copy.
    relevant_events = 0
    covered_tickers: set[str] = set()
    local_files = []
    for row in manifest_rows:
        path = Path(row["local_path"])
        if row["status"] not in {"LOCAL", "DOWNLOADED"} or not path.exists():
            continue
        local_files.append(path)
        try:
            for event in read_kalshi_parquet(path):
                if event.market_ticker in exact_tickers:
                    relevant_events += 1
                    covered_tickers.add(event.market_ticker)
        except Exception as exc:
            counters[f"PARSE_ERROR:{type(exc).__name__}"] += 1

    exact_events = {str(r.get("weather_event_id")) for r in catalog if r.get("status") == "EXACT" and r.get("weather_event_id")}
    settlement_dates = {str(r.get("settlement_date")) for r in catalog if r.get("status") == "EXACT" and r.get("settlement_date")}
    stations = {str(r.get("station")) for r in catalog if r.get("status") == "EXACT" and r.get("station")}
    print(f"planned_hours={len(plan)} order={args.order} exact_contracts={len(exact_tickers)} exact_events={len(exact_events)} settlement_dates={len(settlement_dates)} stations={len(stations)}")
    print(f"status_counts={dict(counters)}")
    print(f"local_files={len(local_files)} relevant_raw_events={relevant_events} covered_contracts={len(covered_tickers)}")
    print(f"manifest={args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
