from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx


NOMADS_BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtma/prod"


@dataclass(frozen=True)
class RtmaRuObject:
    valid_time: datetime
    grib_url: str
    idx_url: str


class RtmaRuProvider:
    """Locate CONUS RTMA-RU 2.5 km analyses on NCEP NOMADS.

    RTMA-RU produces analyses at :00/:15/:30/:45. The provider intentionally
    exposes both GRIB and .idx URLs so callers can byte-range only the fields
    needed for station-level feature extraction.
    """

    @staticmethod
    def floor_quarter_hour(when: datetime) -> datetime:
        when = when.astimezone(timezone.utc).replace(second=0, microsecond=0)
        return when.replace(minute=(when.minute // 15) * 15)

    @classmethod
    def object_for(cls, when: datetime) -> RtmaRuObject:
        valid = cls.floor_quarter_hour(when)
        ymd = valid.strftime("%Y%m%d")
        hhmm = valid.strftime("%H%M")
        name = f"rtma2p5_ru.t{hhmm}z.2dvaranl_ndfd.grb2"
        base = f"{NOMADS_BASE}/rtma2p5_ru.{ymd}/{name}"
        return RtmaRuObject(valid_time=valid, grib_url=base, idx_url=base + ".idx")

    async def latest_available(self, when: Optional[datetime] = None, lookback_steps: int = 8) -> RtmaRuObject:
        from datetime import timedelta

        when = when or datetime.now(timezone.utc)
        candidate = self.floor_quarter_hour(when)
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            for i in range(lookback_steps):
                obj = self.object_for(candidate - timedelta(minutes=15 * i))
                r = await client.head(obj.idx_url)
                if r.status_code == 200:
                    return obj
        raise RuntimeError("No RTMA-RU analysis found in lookback window")
