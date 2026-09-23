from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq

from weather_alpha.backtest.bulk_weather_pmxt import local_archive_state, plan_archive_hours
from weather_alpha.backtest.kalshi_pmxt import download_kalshi_hour


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


def write_manifest(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, sort_keys=True, default=str) for r in rows) + ("\n" if rows else ""), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Download PMXT weather hours newest-first for manual recent-price validation")
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_bulk_resolved.parquet"))
    p.add_argument("--pmxt-dir", type=Path, default=Path("/data/weather/raw/kalshi/pmxt_orderbooks_recent"))
    p.add_argument("--manifest", type=Path, default=Path("/data/weather/manifests/kalshi_weather_pmxt_recent.jsonl"))
    p.add_argument("--days", type=int, default=31, help="recent settlement days to target when explicit dates are omitted")
    p.add_argument("--start-date", type=date.fromisoformat, default=None)
    p.add_argument("--end-date", type=date.fromisoformat, default=None)
    p.add_argument("--final-hours", type=int, default=6)
    p.add_argument("--max-download-hours", type=int, default=0, help="0 = unlimited")
    p.add_argument("--probe-only", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--oldest-first", action="store_true", help="default is newest-first")
    p.add_argument("--sleep", type=float, default=0.02)
    args = p.parse_args()

    catalog = pq.read_table(args.catalog).to_pylist()
    exact_days = sorted({
        date.fromisoformat(str(r.get("settlement_date"))[:10])
        for r in catalog
        if r.get("status") == "EXACT" and r.get("settlement_date")
    })
    if not exact_days:
        raise SystemExit("catalog has no EXACT settlement dates")

    end_date = args.end_date or exact_days[-1]
    start_date = args.start_date or (end_date - timedelta(days=max(1, args.days) - 1))
    selected = [
        r for r in catalog
        if r.get("status") == "EXACT"
        and r.get("settlement_date")
        and start_date <= date.fromisoformat(str(r["settlement_date"])[:10]) <= end_date
    ]
    if not selected:
        raise SystemExit(f"no EXACT catalog rows in {start_date}..{end_date}")

    plan = list(plan_archive_hours(selected, final_hours=args.final_hours))
    plan.sort(key=lambda x: x.hour_utc, reverse=not args.oldest_first)
    args.pmxt_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"range={start_date}..{end_date} exact_contract_rows={len(selected)} "
        f"planned_hours={len(plan)} order={'oldest-first' if args.oldest_first else 'newest-first'}"
    )

    rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    downloaded = 0
    with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent":"weather-alpha-recent/0.1"}) as client:
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
            rows.append({
                "sequence": i,
                "hour_utc": item.hour_utc.isoformat(),
                "settlement_date": item.settlement_date.isoformat(),
                "station": item.station,
                "weather_event_id": item.weather_event_id,
                "filename": item.filename,
                "url": item.url,
                "local_path": str(path),
                "local_state_before": local_state,
                "remote_state": remote_state,
                "content_length": content_length,
                "status": status,
            })
            if i % 10 == 0 or status.startswith("DOWNLOAD_ERROR"):
                print(f"progress={i}/{len(plan)} latest_hour={item.hour_utc.isoformat()} counts={dict(counters)}", flush=True)
                write_manifest(rows, args.manifest)
            time.sleep(max(0.0, args.sleep))

    write_manifest(rows, args.manifest)
    print(f"status_counts={dict(counters)} downloaded={downloaded}")
    print(f"manifest={args.manifest}")
    print(f"pmxt_dir={args.pmxt_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
