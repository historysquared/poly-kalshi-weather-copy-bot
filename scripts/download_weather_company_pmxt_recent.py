from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq

from weather_alpha.backtest.kalshi_pmxt import download_kalshi_hour, kalshi_hourly_filename, kalshi_hourly_url


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def probe(client: httpx.Client, url: str) -> tuple[str, int | None]:
    try:
        r = client.head(url)
        if r.status_code == 404:
            return "ARCHIVE_MISSING", None
        if r.status_code in {200, 206}:
            size = r.headers.get("content-length")
            return "AVAILABLE", int(size) if size and size.isdigit() else None
        r.raise_for_status()
        return "AVAILABLE", None
    except Exception as exc:
        return f"PROBE_ERROR:{type(exc).__name__}", None


def write_manifest(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, sort_keys=True, default=str) for r in rows) + ("\n" if rows else ""), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Download Weather Company regime PMXT hours newest-first using contract close times")
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/weather_company_regime_history.parquet"))
    p.add_argument("--pmxt-dir", type=Path, default=Path("/data/weather/raw/kalshi/pmxt_orderbooks_weather_company"))
    p.add_argument("--manifest", type=Path, default=Path("/data/weather/manifests/weather_company_pmxt_recent.jsonl"))
    p.add_argument("--start-date", type=date.fromisoformat, default=None)
    p.add_argument("--end-date", type=date.fromisoformat, default=None)
    p.add_argument("--days", type=int, default=31)
    p.add_argument("--final-hours", type=int, default=6)
    p.add_argument("--max-download-hours", type=int, default=0)
    p.add_argument("--probe-only", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--oldest-first", action="store_true")
    p.add_argument("--sleep", type=float, default=0.02)
    args = p.parse_args()

    rows = pq.read_table(args.catalog).to_pylist()
    wc = [r for r in rows if r.get("source_family") == "WEATHER_COMPANY" and r.get("settlement_date")]
    if not wc:
        raise SystemExit("catalog contains no WEATHER_COMPANY rows")
    dates = sorted({date.fromisoformat(str(r["settlement_date"])[:10]) for r in wc})
    end_date = args.end_date or dates[-1]
    start_date = args.start_date or (end_date - timedelta(days=max(1, args.days) - 1))
    selected = [r for r in wc if start_date <= date.fromisoformat(str(r["settlement_date"])[:10]) <= end_date]
    if not selected:
        raise SystemExit(f"no WEATHER_COMPANY rows in {start_date}..{end_date}")

    planned: dict[datetime, dict[str, Any]] = {}
    for row in selected:
        close = parse_dt(row.get("close_time"))
        if close is None:
            continue
        end_hour = close.replace(minute=0, second=0, microsecond=0)
        if close == end_hour:
            end_hour -= timedelta(hours=1)
        for i in range(args.final_hours):
            hour = end_hour - timedelta(hours=i)
            planned.setdefault(hour, {
                "hour_utc": hour,
                "settlement_date": row.get("settlement_date"),
                "series_ticker": row.get("series_ticker"),
                "contract_id": row.get("contract_id"),
            })

    plan = sorted(planned.values(), key=lambda x: x["hour_utc"], reverse=not args.oldest_first)
    args.pmxt_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    downloaded = 0

    print(f"range={start_date}..{end_date} weather_company_rows={len(selected)} planned_hours={len(plan)} order={'oldest-first' if args.oldest_first else 'newest-first'}")
    with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent":"weather-alpha-twc-pmxt/0.1"}) as client:
        for idx, item in enumerate(plan, 1):
            hour = item["hour_utc"]
            filename = kalshi_hourly_filename(hour)
            url = kalshi_hourly_url(hour)
            path = args.pmxt_dir / filename
            if path.exists() and path.stat().st_size > 0:
                status = "LOCAL"
                size = path.stat().st_size
            else:
                remote, size = probe(client, url)
                if remote != "AVAILABLE":
                    status = remote
                elif args.probe_only:
                    status = "AVAILABLE_NOT_DOWNLOADED"
                elif args.max_download_hours and downloaded >= args.max_download_hours:
                    status = "AVAILABLE_DEFERRED_LIMIT"
                else:
                    try:
                        path = download_kalshi_hour(hour, args.pmxt_dir)
                        downloaded += 1
                        status = "DOWNLOADED"
                        size = path.stat().st_size
                    except FileNotFoundError:
                        status = "ARCHIVE_MISSING"
                    except Exception as exc:
                        status = f"DOWNLOAD_ERROR:{type(exc).__name__}"
            counts[status] += 1
            out.append({
                "sequence": idx,
                "hour_utc": hour.isoformat(),
                "settlement_date": item["settlement_date"],
                "series_ticker": item["series_ticker"],
                "contract_id_example": item["contract_id"],
                "filename": filename,
                "url": url,
                "local_path": str(path),
                "content_length": size,
                "status": status,
            })
            if idx % 10 == 0 or status.startswith("DOWNLOAD_ERROR"):
                print(f"progress={idx}/{len(plan)} latest_hour={hour.isoformat()} counts={dict(counts)}", flush=True)
                write_manifest(out, args.manifest)
            time.sleep(max(0.0, args.sleep))

    write_manifest(out, args.manifest)
    print(f"status_counts={dict(counts)} downloaded={downloaded}")
    print(f"manifest={args.manifest}")
    print(f"pmxt_dir={args.pmxt_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
