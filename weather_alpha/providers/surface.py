from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
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


class SurfaceFetchStatus(StrEnum):
    OK = "OK"
    NO_DATA = "NO_DATA"
    API_ERROR = "API_ERROR"


class SurfaceFetchError(RuntimeError):
    def __init__(self, message: str, *, status: SurfaceFetchStatus = SurfaceFetchStatus.API_ERROR, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.status_code = status_code


class IemAsosOneMinuteArchive:
    """Historical NCEI ASOS one-minute data exposed by Iowa State IEM.

    Important: this is the NCEI one-minute archive, not the MADIS one-minute
    stream and not the public whole-C five-minute feed. IEM's current API
    requires explicit ``sts``, ``ets`` and ``vars`` parameters. Legacy
    year/month/day-only requests return HTTP 422.
    """

    URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py"
    VARIABLES = ("tmpf", "dwpf", "drct", "sknt", "pres1")

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

    @staticmethod
    def _iem_station(station: str) -> str:
        value = station.strip().upper()
        # IEM's ASOS1MIN database uses the three-character US station id, e.g.
        # MDW, while our canonical catalog uses ICAO KMDW.
        return value[1:] if len(value) == 4 and value.startswith("K") else value

    @staticmethod
    def _iso_z(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def fetch(self, stations: Iterable[str], start: datetime, end: datetime) -> list[SurfaceObservation]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start/end must be timezone-aware")
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        if end <= start:
            raise ValueError("end must be after start")

        canonical = [s.strip().upper() for s in stations if s and s.strip()]
        if not canonical:
            return []
        iem_ids = [self._iem_station(s) for s in canonical]

        params: list[tuple[str, str]] = [
            ("tz", "UTC"),
            ("sts", self._iso_z(start)),
            ("ets", self._iso_z(end)),
            ("sample", "1min"),
            ("what", "download"),
            ("delim", "comma"),
            ("gis", "false"),
        ]
        for station in iem_ids:
            params.append(("station", station))
        for var in self.VARIABLES:
            params.append(("vars", var))

        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True, headers={"User-Agent": "weather-alpha-lab/0.5"}) as client:
            response = await client.get(self.URL, params=params)

        if response.status_code >= 400:
            body = response.text[:500].replace("\n", " ")
            raise SurfaceFetchError(
                f"IEM ASOS1MIN HTTP {response.status_code}: {body}",
                status=SurfaceFetchStatus.API_ERROR,
                status_code=response.status_code,
            )

        reader = csv.DictReader(io.StringIO(response.text))
        rows: list[SurfaceObservation] = []
        for row in reader:
            station = (row.get("station") or row.get("station_id") or "").strip().upper()
            if station and len(station) == 3:
                station = "K" + station
            valid = row.get("valid(UTC)") or row.get("valid") or row.get("timestamp")
            if not station or not valid:
                continue
            rows.append(SurfaceObservation(
                station=station,
                valid_time=self._parse_time(valid),
                temperature_f=self._num(row.get("tmpf")),
                dewpoint_f=self._num(row.get("dwpf")),
                wind_direction_deg=self._num(row.get("drct")),
                wind_speed_kt=self._num(row.get("sknt")),
                # IEM ASOS1MIN exposes pres1/pres2/pres3 rather than MSLP.
                # Keep the field for compatibility; pressure is not used by the
                # settlement-extreme study until its units/semantics are audited.
                pressure_mb=None,
                source="IEM_NCEI_ASOS_1MIN_ARCHIVE",
            ))

        rows.sort(key=lambda x: x.valid_time)
        if not rows:
            raise SurfaceFetchError(
                f"IEM ASOS1MIN returned no rows for {canonical} {start.isoformat()}..{end.isoformat()}",
                status=SurfaceFetchStatus.NO_DATA,
                status_code=response.status_code,
            )
        return rows


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
