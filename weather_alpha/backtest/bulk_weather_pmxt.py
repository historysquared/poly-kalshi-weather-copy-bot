from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from weather_alpha.backtest.kalshi_pmxt import kalshi_hourly_filename, kalshi_hourly_url
from weather_alpha.settlement.reconstruction import local_standard_settlement_window
from weather_alpha.settlement.stations import station_clock


@dataclass(frozen=True)
class WeatherArchiveHour:
    hour_utc: datetime
    station: str
    settlement_date: date
    weather_event_id: str

    @property
    def filename(self) -> str:
        return kalshi_hourly_filename(self.hour_utc)

    @property
    def url(self) -> str:
        return kalshi_hourly_url(self.hour_utc)


def _floor_hour(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def event_archive_hours(
    *,
    station: str,
    settlement_date: date,
    final_hours: int = 6,
) -> tuple[WeatherArchiveHour, ...]:
    if final_hours <= 0 or final_hours > 24:
        raise ValueError("final_hours must be in 1..24")
    station = station.upper()
    window = local_standard_settlement_window(station_clock(station), settlement_date)
    end_hour = _floor_hour(window.end_utc - timedelta(microseconds=1))
    start_hour = end_hour - timedelta(hours=final_hours - 1)
    event_id = f"{station}_{settlement_date.isoformat()}_DAILY_HIGH"
    return tuple(
        WeatherArchiveHour(
            hour_utc=start_hour + timedelta(hours=i),
            station=station,
            settlement_date=settlement_date,
            weather_event_id=event_id,
        )
        for i in range(final_hours)
    )


def plan_archive_hours(catalog_rows: Iterable[dict], *, final_hours: int = 6) -> tuple[WeatherArchiveHour, ...]:
    by_event: dict[tuple[str, date], WeatherArchiveHour] = {}
    planned: dict[datetime, WeatherArchiveHour] = {}
    for row in catalog_rows:
        if str(row.get("status") or "") != "EXACT":
            continue
        station = str(row.get("station") or "").upper()
        day_text = str(row.get("settlement_date") or "")[:10]
        if not station or len(day_text) != 10:
            continue
        day = date.fromisoformat(day_text)
        by_event[(station, day)] = event_archive_hours(station=station, settlement_date=day, final_hours=final_hours)[0]
    for station, day in sorted(by_event):
        for item in event_archive_hours(station=station, settlement_date=day, final_hours=final_hours):
            planned.setdefault(item.hour_utc, item)
    return tuple(planned[k] for k in sorted(planned))


def local_archive_state(directory: Path, item: WeatherArchiveHour) -> str:
    path = directory / item.filename
    if path.exists() and path.stat().st_size > 0:
        return "LOCAL"
    if path.with_suffix(path.suffix + ".part").exists():
        return "PARTIAL"
    return "MISSING"
