from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from weather_alpha.settlement.reconstruction import local_standard_settlement_window
from weather_alpha.settlement.stations import station_clock


@dataclass(frozen=True)
class WeatherPmxtHour:
    hour_utc: datetime
    station: str
    settlement_date: date
    window_end_utc: datetime


def floor_hour(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def derive_weather_pmxt_hours(
    catalog_rows: Iterable[dict],
    *,
    final_hours: int = 6,
    include_status: str = "EXACT",
) -> list[WeatherPmxtHour]:
    """Derive UTC PMXT archive hours for exact weather event settlement windows.

    The interval is the final `final_hours` of the canonical LST settlement day.
    Hours are deduplicated across buckets/contracts sharing the same station-day.
    """
    if final_hours <= 0 or final_hours > 24:
        raise ValueError("final_hours must be between 1 and 24")

    events: set[tuple[str, date]] = set()
    for row in catalog_rows:
        if include_status and row.get("status") != include_status:
            continue
        station = str(row.get("station") or "").upper()
        day_text = str(row.get("settlement_date") or "")[:10]
        if not station or len(day_text) != 10:
            continue
        events.add((station, date.fromisoformat(day_text)))

    result: list[WeatherPmxtHour] = []
    for station, day in sorted(events):
        window = local_standard_settlement_window(station_clock(station), day)
        start = window.end_utc - timedelta(hours=final_hours)
        hour = floor_hour(start)
        while hour < window.end_utc:
            result.append(WeatherPmxtHour(hour, station, day, window.end_utc))
            hour += timedelta(hours=1)

    # An archive hour may serve several stations/events. Keep one row per unique
    # (hour, station, day) for diagnostics; callers can dedupe hours for download.
    return result


def unique_hours(rows: Iterable[WeatherPmxtHour]) -> list[datetime]:
    return sorted({r.hour_utc for r in rows})
