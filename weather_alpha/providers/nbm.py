from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Iterable

import httpx

NBM_TEXT_BASE = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"


@dataclass(frozen=True)
class NbmStationMax:
    station: str
    valid_date: date
    issue_cycle_utc: str
    max_f: float
    max_sd_f: float | None
    txn_values: tuple[int, ...]
    xnd_values: tuple[int, ...]
    source_url: str


class NbmTextClient:
    """Archive client for NOAA NBM NBS station guidance.

    The 00Z NBS TXN row alternates minimum/maximum guidance. For the local
    daily-high use case, TXN[1] is the first forecast maximum after the cycle.
    XND is the corresponding NBM spread/standard-deviation guidance when present.
    """

    def __init__(self, timeout: float = 90.0, retries: int = 5) -> None:
        self.timeout = timeout
        self.retries = retries

    @staticmethod
    def url(valid_date: date, cycle: int = 0) -> str:
        ds = valid_date.strftime("%Y%m%d")
        return f"{NBM_TEXT_BASE}/blend.{ds}/{cycle:02d}/text/blend_nbstx.t{cycle:02d}z"

    def fetch_text(self, valid_date: date, cycle: int = 0) -> tuple[str, str]:
        url = self.url(valid_date, cycle)
        headers = {"User-Agent": "weather-alpha-nbm/0.1"}
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
            last: Exception | None = None
            for attempt in range(self.retries):
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    return response.text, url
                except Exception as exc:  # network/backoff boundary
                    last = exc
                    if attempt + 1 < self.retries:
                        time.sleep(min(20.0, 2.0 ** attempt))
            assert last is not None
            raise last

    @staticmethod
    def parse_station(text: str, *, station: str, valid_date: date, cycle: int = 0,
                      source_url: str = "") -> NbmStationMax | None:
        marker = re.search(rf"(?m)^\s*{re.escape(station.upper())}\s+NBM\s+V", text)
        if marker is None:
            return None
        block = text[marker.start(): marker.start() + 9000]
        txn_match = re.search(r"(?m)^\s*TXN\s+(.+)$", block)
        xnd_match = re.search(r"(?m)^\s*XND\s+(.+)$", block)
        if txn_match is None:
            return None
        txn = tuple(int(x) for x in re.findall(r"-?\d+", txn_match.group(1)))
        xnd = tuple(int(x) for x in re.findall(r"-?\d+", xnd_match.group(1))) if xnd_match else ()
        if len(txn) < 2:
            return None
        return NbmStationMax(
            station=station.upper(),
            valid_date=valid_date,
            issue_cycle_utc=f"{valid_date.isoformat()}T{cycle:02d}:00:00Z",
            max_f=float(txn[1]),
            max_sd_f=float(xnd[1]) if len(xnd) >= 2 else None,
            txn_values=txn,
            xnd_values=xnd,
            source_url=source_url,
        )

    def fetch_station_maxes(self, valid_date: date, stations: Iterable[str], cycle: int = 0) -> list[NbmStationMax]:
        text, url = self.fetch_text(valid_date, cycle)
        out: list[NbmStationMax] = []
        for station in stations:
            row = self.parse_station(text, station=station, valid_date=valid_date, cycle=cycle, source_url=url)
            if row is not None:
                out.append(row)
        return out
