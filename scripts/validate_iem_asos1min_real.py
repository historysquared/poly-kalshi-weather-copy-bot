from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from weather_alpha.providers.surface import IemAsosOneMinuteArchive, SurfaceFetchError


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate one real IEM NCEI ASOS one-minute request before bulk settlement studies")
    p.add_argument("--station", default="KMDW")
    p.add_argument("--start", default="2026-06-11T06:00:00Z")
    p.add_argument("--end", default="2026-06-11T07:00:00Z")
    p.add_argument("--timeout", type=float, default=60.0)
    return p.parse_args()


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


async def run(args: argparse.Namespace) -> int:
    archive = IemAsosOneMinuteArchive(timeout_s=args.timeout)
    try:
        rows = await archive.fetch([args.station], parse_time(args.start), parse_time(args.end))
    except SurfaceFetchError as exc:
        print(f"status={exc.status.value} http_status={exc.status_code} error={exc}")
        return 2
    print("status=OK")
    print(f"rows={len(rows)}")
    temp_rows = [r for r in rows if r.temperature_f is not None]
    print(f"temperature_rows={len(temp_rows)}")
    print(f"first={rows[0]}")
    print(f"last={rows[-1]}")
    if temp_rows:
        print(f"temperature_min_f={min(r.temperature_f for r in temp_rows)}")
        print(f"temperature_max_f={max(r.temperature_f for r in temp_rows)}")
    return 0


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
