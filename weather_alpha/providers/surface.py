from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import httpx


@dataclass(frozen=True)
class SurfaceObservation:
    station: str
    valid_time: datetime
    temperature_f: float | None
    dewpoint_f: float | None
    wind_direction_deg: float | None
    wind_speed_kt: float | None
    pressure_mb: float | None
    source: str
    received_time: datetime | None = None


class IemAsosOneMinuteArchive:
    """Historical one-minute ASOS adapter for backtesting.

    IEM documents this dataset as a processed NCEI/MADIS archive with limited QC.
    It is therefore a feature source, not the settlement authority.
    """

    URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py"

    def __init__(self, timeout_s: float = 45.0) -> None:
        self.timeout_s = timeout_s

    @staticmethod
    def _num(value: str | None) -> float | None:
        if value is None:
            return None
        v = value.strip()
        if not v or v.upper() in {"M", "NULL", "NONE", "NAN"}:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    @staticmethod
    def _parse_time(value: str) -> datetime:
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                raise
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc)

    async def fetch(self, stations: Iterable[str], start: datetime, end: datetime) -> list[SurfaceObservation]:
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        params: list[tuple[str, str]] = [
            ("tz", "UTC"),
            ("year1", str(start.year)), ("month1", str(start.month)), ("day1", str(start.day)),
            ("hour1", str(start.hour)), ("minute1", str(start.minute)),
            ("year2", str(end.year)), ("month2", str(end.month)), ("day2", str(end.day)),
            ("hour2", str(end.hour)), ("minute2", str(end.minute)),
            ("sample", "1min"), ("what", "download"), ("delim", "comma"), ("gis", "no"),
        ]
        for station in stations:
            params.append(("station", station.removeprefix("K")))
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            r = await client.get(self.URL, params=params)
            r.raise_for_status()
        rows: list[SurfaceObservation] = []
        text = r.text
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            station = (row.get("station") or row.get("station_id") or "").strip().upper()
            if station and len(station) == 3:
                station = "K" + station
            valid = row.get("valid") or row.get("valid(UTC)") or row.get("timestamp")
            if not station or not valid:
                continue
            rows.append(SurfaceObservation(
                station=station,
                valid_time=self._parse_time(valid),
                temperature_f=self._num(row.get("tmpf") or row.get("tmpf_1min")),
                dewpoint_f=self._num(row.get("dwpf") or row.get("dwpf_1min")),
                wind_direction_deg=self._num(row.get("drct") or row.get("drct_1min")),
                wind_speed_kt=self._num(row.get("sknt") or row.get("sknt_1min")),
                pressure_mb=self._num(row.get("mslp") or row.get("mslp_1min")),
                source="IEM_ASOS_1MIN_ARCHIVE",
            ))
        return sorted(rows, key=lambda x: x.valid_time)


class SurfaceSeries:
    @staticmethod
    def temperature_velocity_f_per_minute(observations: list[SurfaceObservation], lookback_minutes: int = 15) -> float | None:
        valid = [o for o in observations if o.temperature_f is not None]
        if len(valid) < 2:
            return None
        latest = valid[-1]
        cutoff = latest.valid_time - timedelta(minutes=lookback_minutes)
        window = [o for o in valid if o.valid_time >= cutoff]
        if len(window) < 2:
            return None
        t0, t1 = window[0], window[-1]
        mins = (t1.valid_time - t0.valid_time).total_seconds() / 60.0
        if mins <= 0:
            return None
        return (float(t1.temperature_f) - float(t0.temperature_f)) / mins

    @staticmethod
    def pressure_velocity_mb_per_minute(observations: list[SurfaceObservation], lookback_minutes: int = 15) -> float | None:
        valid = [o for o in observations if o.pressure_mb is not None]
        if len(valid) < 2:
            return None
        latest = valid[-1]
        cutoff = latest.valid_time - timedelta(minutes=lookback_minutes)
        window = [o for o in valid if o.valid_time >= cutoff]
        if len(window) < 2:
            return None
        t0, t1 = window[0], window[-1]
        mins = (t1.valid_time - t0.valid_time).total_seconds() / 60.0
        return None if mins <= 0 else (float(t1.pressure_mb) - float(t0.pressure_mb)) / mins
