from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import xarray as xr


@dataclass(frozen=True)
class HrrrPointForecast:
    cycle: datetime
    forecast_hour: int
    valid_time: datetime
    lat: float
    lon: float
    temperature_f: float | None
    total_cloud_cover_pct: float | None
    downward_shortwave_w_m2: float | None
    source_key: str


@dataclass(frozen=True)
class _IndexRecord:
    number: int
    start: int
    descriptor: str


class HrrrAwsProvider:
    """Fetch only needed HRRR GRIB messages from NOAA AWS using `.idx` byte ranges.

    Backtests should pass an explicit cycle that was actually available at the
    simulated decision time. Do not infer a future cycle from the valid time.
    """

    BASE = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"

    def __init__(self, timeout_s: float = 30.0) -> None:
        self.timeout_s = timeout_s

    @staticmethod
    def key(cycle: datetime, forecast_hour: int, product: str = "wrfsfc") -> str:
        c = cycle.astimezone(timezone.utc)
        return f"hrrr.{c:%Y%m%d}/conus/hrrr.t{c:%H}z.{product}f{forecast_hour:02d}.grib2"

    @staticmethod
    def parse_idx(text: str) -> list[_IndexRecord]:
        rows: list[_IndexRecord] = []
        for line in text.splitlines():
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            try:
                rows.append(_IndexRecord(int(parts[0]), int(parts[1]), parts[2]))
            except ValueError:
                continue
        return rows

    @staticmethod
    def _find_record(rows: list[_IndexRecord], patterns: tuple[str, ...]) -> _IndexRecord | None:
        for row in rows:
            d = row.descriptor
            if all(p.lower() in d.lower() for p in patterns):
                return row
        return None

    @staticmethod
    def _byte_range(rows: list[_IndexRecord], record: _IndexRecord, object_size: int) -> tuple[int, int]:
        ordered = sorted(rows, key=lambda r: r.start)
        i = ordered.index(record)
        end = ordered[i + 1].start - 1 if i + 1 < len(ordered) else object_size - 1
        return record.start, end

    async def _metadata(self, key: str) -> tuple[list[_IndexRecord], int]:
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            idx_url = f"{self.BASE}/{key}.idx"
            grib_url = f"{self.BASE}/{key}"
            idx_resp, head_resp = await asyncio.gather(client.get(idx_url), client.head(grib_url))
            idx_resp.raise_for_status()
            head_resp.raise_for_status()
            size = int(head_resp.headers["content-length"])
            return self.parse_idx(idx_resp.text), size

    async def _fetch_message(self, key: str, start: int, end: int) -> bytes:
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            r = await client.get(f"{self.BASE}/{key}", headers={"Range": f"bytes={start}-{end}"})
            r.raise_for_status()
            return r.content

    @staticmethod
    def _nearest_value(blob: bytes, lat: float, lon: float) -> float | None:
        with tempfile.NamedTemporaryFile(suffix=".grib2") as f:
            f.write(blob)
            f.flush()
            try:
                ds = xr.open_dataset(f.name, engine="cfgrib", backend_kwargs={"indexpath": ""})
            except Exception:
                return None
            try:
                data_vars = list(ds.data_vars)
                if not data_vars:
                    return None
                var = ds[data_vars[0]]
                if "latitude" in ds and "longitude" in ds:
                    lats = np.asarray(ds["latitude"].values, dtype=float)
                    lons = np.asarray(ds["longitude"].values, dtype=float)
                    target_lon = lon % 360.0 if np.nanmax(lons) > 180 else lon
                    dist2 = (lats - lat) ** 2 + ((lons - target_lon) * np.cos(np.deg2rad(lat))) ** 2
                    idx = np.unravel_index(np.nanargmin(dist2), dist2.shape)
                    value = float(np.asarray(var.values)[idx])
                else:
                    value = float(np.asarray(var.values).squeeze())
                return value if np.isfinite(value) else None
            finally:
                ds.close()

    async def point_forecast(self, cycle: datetime, forecast_hour: int, lat: float, lon: float) -> HrrrPointForecast:
        key = self.key(cycle, forecast_hour)
        rows, size = await self._metadata(key)
        desired = {
            "temperature_k": self._find_record(rows, ("TMP", "2 m above ground")),
            "tcdc_pct": self._find_record(rows, ("TCDC", "entire atmosphere")),
            "dswrf": self._find_record(rows, ("DSWRF", "surface")),
        }

        async def fetch(rec: _IndexRecord | None) -> float | None:
            if rec is None:
                return None
            start, end = self._byte_range(rows, rec, size)
            blob = await self._fetch_message(key, start, end)
            return await asyncio.to_thread(self._nearest_value, blob, lat, lon)

        t_k, tcdc, dswrf = await asyncio.gather(*(fetch(desired[k]) for k in ("temperature_k", "tcdc_pct", "dswrf")))
        temp_f = None if t_k is None else (t_k - 273.15) * 9.0 / 5.0 + 32.0
        c = cycle.astimezone(timezone.utc)
        from datetime import timedelta
        return HrrrPointForecast(
            cycle=c,
            forecast_hour=forecast_hour,
            valid_time=c + timedelta(hours=forecast_hour),
            lat=lat,
            lon=lon,
            temperature_f=temp_f,
            total_cloud_cover_pct=tcdc,
            downward_shortwave_w_m2=dswrf,
            source_key=key,
        )
