from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

import boto3
import numpy as np
import xarray as xr
from botocore import UNSIGNED
from botocore.config import Config


EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angular_difference_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


@dataclass(frozen=True)
class StationPoint:
    station: str
    lat: float
    lon: float


@dataclass(frozen=True)
class ConvectiveFeature:
    observed_at: datetime
    station: str
    source: str
    precip_core_distance_km: Optional[float] = None
    precip_core_bearing_deg: Optional[float] = None
    max_reflectivity_dbz_nearby: Optional[float] = None
    lightning_flashes_10km_5m: int = 0
    lightning_flashes_25km_10m: int = 0
    mrms_precip_rate_mm_h: Optional[float] = None
    upwind_core: bool = False
    cooling_risk_score: float = 0.0


class _AnonS3:
    def __init__(self) -> None:
        self.client = boto3.client("s3", config=Config(signature_version=UNSIGNED))

    def latest_keys(self, bucket: str, prefix: str, limit: int = 20) -> list[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        rows: list[tuple[datetime, str]] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                rows.append((obj["LastModified"], obj["Key"]))
        rows.sort(reverse=True)
        return [key for _, key in rows[:limit]]


class NexradArchive:
    """Discovery helper for the current public Level-II NEXRAD archive.

    The historical/current bucket moved from the deprecated `noaa-nexrad-level2`
    name to `unidata-nexrad-level2` in 2025. Decoding is intentionally delegated
    to Py-ART or another Level-II decoder in the runtime because the binary format
    is not an xarray/NetCDF product.
    """

    bucket = "unidata-nexrad-level2"

    def __init__(self) -> None:
        self.s3 = _AnonS3()

    @staticmethod
    def prefix(radar_id: str, when: datetime) -> str:
        when = when.astimezone(timezone.utc)
        return f"{when:%Y/%m/%d}/{radar_id.upper()}/"

    def latest_volume_keys(self, radar_id: str, when: datetime, limit: int = 12) -> list[str]:
        return self.s3.latest_keys(self.bucket, self.prefix(radar_id, when), limit=limit)


class GoesGlmArchive:
    """GOES GLM Level-2 lightning discovery and station-neighborhood counting."""

    def __init__(self, satellite: int) -> None:
        if satellite not in (18, 19):
            raise ValueError("live GLM satellite must be 18 (West) or 19 (East)")
        self.satellite = satellite
        self.bucket = f"noaa-goes{satellite}"
        self.s3 = _AnonS3()

    @staticmethod
    def _prefix(when: datetime) -> str:
        when = when.astimezone(timezone.utc)
        doy = when.timetuple().tm_yday
        return f"GLM-L2-LCFA/{when:%Y}/{doy:03d}/{when:%H}/"

    def latest_keys(self, when: datetime, limit: int = 40) -> list[str]:
        return self.s3.latest_keys(self.bucket, self._prefix(when), limit=limit)

    @staticmethod
    def count_flashes(ds: xr.Dataset, station: StationPoint, radius_km: float) -> int:
        lat_name = "flash_lat" if "flash_lat" in ds else "group_lat"
        lon_name = "flash_lon" if "flash_lon" in ds else "group_lon"
        if lat_name not in ds or lon_name not in ds:
            return 0
        lats = np.asarray(ds[lat_name].values, dtype=float).ravel()
        lons = np.asarray(ds[lon_name].values, dtype=float).ravel()
        count = 0
        for lat, lon in zip(lats, lons, strict=False):
            if np.isfinite(lat) and np.isfinite(lon) and haversine_km(station.lat, station.lon, lat, lon) <= radius_km:
                count += 1
        return count


class MrmsArchive:
    """Anonymous AWS discovery for NOAA MRMS products.

    Exact product prefixes vary by MRMS product; callers pass the product prefix
    they want to study (for example a precip-rate or reflectivity product) so the
    backtester can keep raw provenance rather than baking one product assumption
    into the trading model.
    """

    bucket = "noaa-mrms-pds"

    def __init__(self) -> None:
        self.s3 = _AnonS3()

    def latest_keys(self, prefix: str, limit: int = 20) -> list[str]:
        return self.s3.latest_keys(self.bucket, prefix, limit=limit)


def nearest_precip_core(
    station: StationPoint,
    lats: np.ndarray,
    lons: np.ndarray,
    reflectivity_dbz: np.ndarray,
    threshold_dbz: float = 35.0,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    mask = np.isfinite(reflectivity_dbz) & (reflectivity_dbz >= threshold_dbz)
    if not np.any(mask):
        return None, None, None
    best: tuple[float, float, float] | None = None
    for lat, lon, dbz in zip(lats[mask].ravel(), lons[mask].ravel(), reflectivity_dbz[mask].ravel(), strict=False):
        d = haversine_km(station.lat, station.lon, float(lat), float(lon))
        if best is None or d < best[0]:
            best = (d, initial_bearing_deg(station.lat, station.lon, float(lat), float(lon)), float(dbz))
    return best if best is not None else (None, None, None)


def convective_cooling_score(
    *,
    precip_core_distance_km: Optional[float],
    precip_core_bearing_deg: Optional[float],
    surface_wind_from_deg: Optional[float],
    max_reflectivity_dbz: Optional[float],
    lightning_flashes_10km_5m: int,
    mrms_precip_rate_mm_h: Optional[float],
) -> tuple[float, bool]:
    """Return a transparent 0..1 short-horizon convective-cooling risk score.

    This is a feature score, not a calibrated probability. Historical weather and
    market data must determine how much it shifts final-high probabilities.
    """
    score = 0.0
    upwind = False
    if precip_core_distance_km is not None:
        score += max(0.0, min(0.30, (20.0 - precip_core_distance_km) / 20.0 * 0.30))
    if max_reflectivity_dbz is not None:
        score += max(0.0, min(0.25, (max_reflectivity_dbz - 30.0) / 30.0 * 0.25))
    if lightning_flashes_10km_5m > 0:
        score += min(0.20, math.log1p(lightning_flashes_10km_5m) / math.log(21.0) * 0.20)
    if mrms_precip_rate_mm_h is not None:
        score += max(0.0, min(0.15, mrms_precip_rate_mm_h / 25.0 * 0.15))
    if precip_core_bearing_deg is not None and surface_wind_from_deg is not None:
        # A core is broadly upwind if it lies near the direction the wind is coming from.
        upwind = angular_difference_deg(precip_core_bearing_deg, surface_wind_from_deg) <= 45.0
        if upwind:
            score += 0.10
    return min(1.0, score), upwind
