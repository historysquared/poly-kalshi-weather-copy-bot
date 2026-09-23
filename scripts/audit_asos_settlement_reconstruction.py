from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from weather_alpha.providers.surface import IemAsosOneMinuteArchive
from weather_alpha.quality.asos_qc import assess_asos_temperature_quality
from weather_alpha.settlement.reconstruction import StationClock, reconstruct_daily_extreme


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit one NWS-style LST settlement day against IEM 1-minute ASOS")
    p.add_argument("--station", required=True, help="ICAO station, e.g. KMDW")
    p.add_argument("--date", required=True, dest="settlement_date", help="Settlement date YYYY-MM-DD")
    p.add_argument("--timezone", required=True, dest="civil_timezone", help="IANA civil timezone, e.g. America/Chicago")
    p.add_argument("--standard-offset", required=True, type=int, help="Fixed LST UTC offset, e.g. -6 for CST")
    p.add_argument("--kind", choices=["high", "low"], default="high")
    p.add_argument("--official-f", type=float, default=None, help="Optional official CLI extreme in Fahrenheit")
    return p.parse_args()


async def run(args: argparse.Namespace) -> int:
    settlement_date = date.fromisoformat(args.settlement_date)
    clock = StationClock(args.station.upper(), args.civil_timezone, args.standard_offset)

    # Build the exact LST window first; fetch a small buffer so source gaps around
    # the boundaries are visible rather than silently clipped.
    from weather_alpha.settlement.reconstruction import local_standard_settlement_window

    window = local_standard_settlement_window(clock, settlement_date)
    fetch_start = window.start_utc - timedelta(minutes=10)
    fetch_end = window.end_utc + timedelta(minutes=10)
    observations = await IemAsosOneMinuteArchive().fetch([clock.station], fetch_start, fetch_end)

    in_window = [o for o in observations if window.start_utc <= o.valid_time < window.end_utc]
    qc = assess_asos_temperature_quality(in_window, as_of=window.end_utc)
    result = reconstruct_daily_extreme(
        observations,
        clock=clock,
        settlement_date=settlement_date,
        official_extreme_f=args.official_f,
        kind=args.kind,
    )

    print(f"station={clock.station}")
    print(f"settlement_date={settlement_date.isoformat()}")
    print(f"kind={args.kind}")
    print(f"window_start_utc={window.start_utc.isoformat()}")
    print(f"window_end_utc={window.end_utc.isoformat()}")
    print(f"window_start_civil={window.start_civil.isoformat()}")
    print(f"window_end_civil={window.end_civil.isoformat()}")
    print(f"observations_fetched={len(observations)}")
    print(f"observations_used={result.observations_used}")
    print(f"qc_status={qc.status.value}")
    print(f"qc_reasons={list(qc.reason_codes)}")
    print(f"observed_extreme_f={result.observed_extreme_f}")
    print(f"official_extreme_f={result.official_extreme_f}")
    print(f"residual_f={result.residual_f}")
    print(f"exact_match={result.exact_match}")
    print(f"within_one_f={result.within_one_f}")
    return 0


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
