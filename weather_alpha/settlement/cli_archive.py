from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Iterable

import httpx


IEM_CLI_URL = "https://mesonet.agron.iastate.edu/json/cli.py"


@dataclass(frozen=True)
class CliDailyRecord:
    station: str
    valid_date: date
    high_f: float | None
    low_f: float | None
    precip_in: float | None
    snow_in: float | None
    high_time_lst: str | None = None
    low_time_lst: str | None = None
    source: str = "NWS_CLI_VIA_IEM"
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["valid_date"] = self.valid_date.isoformat()
        return row


def _num(value: Any, *, trace: float | None = 0.0) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"M", "MM", "NULL", "NONE", "NAN"}:
        return None
    if text.upper() == "T":
        return trace
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _first(row: dict[str, Any], *keys: str) -> Any:
    lower = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        if key in row:
            return row[key]
        if key.lower() in lower:
            return lower[key.lower()]
    return None


def _date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%b%d,%y", "%b%d,%Y", "%m/%d/%Y"):
        from datetime import datetime
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def parse_iem_cli_payload(station: str, payload: Any) -> list[CliDailyRecord]:
    """Parse IEM's CLI JSON defensively.

    IEM is a convenient archive/index of NWS-issued CLI reports, not a replacement
    for the venue's settlement authority. Raw rows are retained for audit.
    """
    if isinstance(payload, dict):
        rows = payload.get("results") or payload.get("data") or payload.get("records") or payload.get("cli") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    out: list[CliDailyRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        valid = _date(_first(row, "valid", "date", "valid_date", "day"))
        if valid is None:
            continue
        out.append(CliDailyRecord(
            station=station.upper(),
            valid_date=valid,
            high_f=_num(_first(row, "high", "max_temp", "maximum", "max", "high_f")),
            low_f=_num(_first(row, "low", "min_temp", "minimum", "min", "low_f")),
            precip_in=_num(_first(row, "precip", "precipitation", "precip_in"), trace=0.0),
            snow_in=_num(_first(row, "snow", "snowfall", "snow_in"), trace=0.0),
            high_time_lst=_clean(_first(row, "high_time", "max_time", "maximum_time")),
            low_time_lst=_clean(_first(row, "low_time", "min_time", "minimum_time")),
            raw=dict(row),
        ))
    return sorted(out, key=lambda x: x.valid_date)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.upper() in {"M", "MM", "NULL", "NONE"} else text


class IemCliArchive:
    def __init__(self, timeout_s: float = 30.0) -> None:
        self.timeout_s = timeout_s

    def fetch_year(self, station: str, year: int) -> list[CliDailyRecord]:
        with httpx.Client(timeout=self.timeout_s, follow_redirects=True, headers={"User-Agent": "weather-alpha-lab/0.3"}) as client:
            response = client.get(IEM_CLI_URL, params={"station": station.upper(), "year": int(year)})
            response.raise_for_status()
            return parse_iem_cli_payload(station, response.json())

    def fetch_years(self, station: str, years: Iterable[int]) -> list[CliDailyRecord]:
        by_date: dict[date, CliDailyRecord] = {}
        for year in sorted(set(int(y) for y in years)):
            for row in self.fetch_year(station, year):
                by_date[row.valid_date] = row
        return [by_date[d] for d in sorted(by_date)]
