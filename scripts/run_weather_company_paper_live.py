from __future__ import annotations

from decimal import Decimal

import scripts.run_experimental_weather_company_paper as paper
from weather_alpha.backtest.causal_settlement import TimedTemperature
from weather_alpha.providers.live_surface import fetch_live_temperature_series


async def fetch_obs_live(station, start, end):
    rows = await fetch_live_temperature_series(station, start, end, max_age_minutes=100)
    latest = rows[-1]
    print(
        f"live_obs station={station} rows={len(rows)} latest={latest.valid_time.isoformat()} "
        f"temp_f={latest.temperature_f:.2f} source={latest.source}",
        flush=True,
    )
    return [TimedTemperature(r.valid_time, Decimal(str(r.temperature_f))) for r in rows]


# Monkey-patch only the observation provider. All paper-trading logic, safety gates,
# latency model, one-trade-per-event state, and no-live-order behavior remain in the
# original experimental forward-paper module.
paper.fetch_obs = fetch_obs_live


if __name__ == "__main__":
    raise SystemExit(paper.main())
