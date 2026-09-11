from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


@dataclass(frozen=True)
class LiveTemperatureObservation:
    station: str
    valid_time: datetime
    temperature_f: float
    source: str
    raw: dict[str, Any]


_TENTHS_RE = re.compile(r"\bT([01])(\d{3})([01])(\d{3})\b")
_IEM_NETWORK = {
    "KNYC": "NY_ASOS",
    "KMDW": "IL_ASOS",
    "KMIA": "FL_ASOS",
    "KLAX": "CA_ASOS",
    "KDEN": "CO_ASOS",
}


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        x = float(value)
        if x > 1e12:
            x /= 1000.0
        return datetime.fromtimestamp(x, tz=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _num(value: Any) -> float | None:
    if value in (None, "", "M", "NULL"):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _temp_f_from_metar(row: dict[str, Any]) -> tuple[float | None, str]:
    raw = str(row.get("rawOb") or row.get("raw_text") or row.get("raw") or "")
    m = _TENTHS_RE.search(raw)
    if m:
        sign = -1.0 if m.group(1) == "1" else 1.0
        c = sign * (int(m.group(2)) / 10.0)
        return c * 9.0 / 5.0 + 32.0, "AWC_METAR_TENTHS_REMARK"
    c = _num(row.get("temp"))
    if c is None:
        c = _num(row.get("tempC"))
    if c is not None:
        return c * 9.0 / 5.0 + 32.0, "AWC_METAR_BODY"
    return None, "AWC_METAR"


async def fetch_awc_metar(station: str, start: datetime, end: datetime) -> list[LiveTemperatureObservation]:
    hours = max(1, min(30 * 24, int(math.ceil((end - start).total_seconds() / 3600.0)) + 2))
    params = {"ids": station.upper(), "format": "json", "hours": str(hours)}
    headers = {"User-Agent": "weather-alpha-paper/0.2 contact=research"}
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers=headers) as client:
        r = await client.get("https://aviationweather.gov/api/data/metar", params=params)
    if r.status_code == 204:
        return []
    r.raise_for_status()
    payload = r.json()
    rows = payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []
    out: list[LiveTemperatureObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        valid = _parse_time(row.get("obsTime") or row.get("reportTime") or row.get("receiptTime"))
        if valid is None or valid < start or valid > end:
            continue
        temp_f, source = _temp_f_from_metar(row)
        if temp_f is None:
            continue
        out.append(LiveTemperatureObservation(station.upper(), valid, temp_f, source, row))
    out.sort(key=lambda x: x.valid_time)
    return out


async def fetch_iem_hourly(station: str, start: datetime, end: datetime) -> list[LiveTemperatureObservation]:
    station = station.upper()
    network = _IEM_NETWORK.get(station)
    if network is None:
        return []
    sid = station[1:] if len(station) == 4 and station.startswith("K") else station
    # IEM asos.py end date is exclusive, so add one UTC calendar day via explicit end timestamp parts.
    params: list[tuple[str, str]] = [
        ("network", network), ("station", sid), ("data", "tmpf"),
        ("year1", str(start.year)), ("month1", str(start.month)), ("day1", str(start.day)),
        ("year2", str(end.year)), ("month2", str(end.month)), ("day2", str(end.day)),
        ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"), ("elev", "no"),
        ("missing", "M"), ("trace", "T"), ("direct", "no"),
        ("report_type", "1"), ("report_type", "2"), ("report_type", "3"), ("report_type", "4"),
    ]
    headers = {"User-Agent": "weather-alpha-paper/0.2"}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
        r = await client.get("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py", params=params)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    out: list[LiveTemperatureObservation] = []
    for row in reader:
        valid = _parse_time(row.get("valid") or row.get("valid(UTC)"))
        temp_f = _num(row.get("tmpf"))
        if valid is None or temp_f is None or valid < start or valid > end:
            continue
        out.append(LiveTemperatureObservation(station, valid, temp_f, "IEM_REALTIME_ASOS_METAR", dict(row)))
    out.sort(key=lambda x: x.valid_time)
    return out


async def fetch_live_temperature_series(station: str, start: datetime, end: datetime, *, max_age_minutes: int = 100) -> list[LiveTemperatureObservation]:
    """Fetch current/recent station temperatures for forward paper trading.

    AWC METAR is primary for airport stations. IEM's near-real-time ASOS/METAR archive
    is the fallback and is also needed for KNYC. The function fails closed if the
    newest observation is stale. This feed is for forward paper trading, not for
    settlement-truth certification.
    """
    errors: list[str] = []
    rows: list[LiveTemperatureObservation] = []
    try:
        rows = await fetch_awc_metar(station, start, end)
    except Exception as exc:
        errors.append(f"AWC:{type(exc).__name__}:{exc}")
    if not rows:
        try:
            rows = await fetch_iem_hourly(station, start, end)
        except Exception as exc:
            errors.append(f"IEM:{type(exc).__name__}:{exc}")
    if not rows:
        raise RuntimeError(f"no live observations for {station}; {' | '.join(errors) or 'no rows'}")
    latest = rows[-1].valid_time
    age = (end.astimezone(timezone.utc) - latest).total_seconds() / 60.0
    if age > max_age_minutes:
        raise RuntimeError(f"stale live observations for {station}: age_minutes={age:.1f} source={rows[-1].source}")
    # Deduplicate same timestamp, preserving the last provider row.
    dedup = {r.valid_time: r for r in rows}
    return [dedup[k] for k in sorted(dedup)]
