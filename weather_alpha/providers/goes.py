from __future__ import annotations

import asyncio
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import boto3
import numpy as np
import xarray as xr
from botocore import UNSIGNED
from botocore.client import Config
from pyproj import CRS, Transformer


@dataclass(frozen=True)
class GoesObject:
    satellite: int
    band: int
    key: str
    last_modified: datetime


@dataclass(frozen=True)
class GoesStationFeatures:
    station: str
    observed_at: datetime
    satellite: int
    band2_reflectance_mean: float | None
    band2_reflectance_p90: float | None
    band13_brightness_temp_k_mean: float | None
    cold_cloud_fraction: float | None
    cloud_optical_depth_proxy: float | None
    solar_transmission_proxy: float | None
    source_keys: tuple[str, ...]


class GoesAbiProvider:
    """Anonymous NOAA GOES ABI CMIP reader from AWS Open Data.

    Current operational defaults are GOES-19 (East) and GOES-18 (West).
    GOES-16 is retained only for historical periods when it was operational.

    The derived `cloud_optical_depth_proxy` is intentionally named a proxy: ABI
    Band 2 reflectance alone is not a physically retrieved cloud optical depth.
    It is useful as a fast, monotonic cloud/solar-attenuation feature that can be
    calibrated against observed station heating and model irradiance.
    """

    BUCKETS = {16: "noaa-goes16", 18: "noaa-goes18", 19: "noaa-goes19"}
    PRODUCT = "ABI-L2-CMIPC"

    def __init__(self, satellite: int = 19, cache_dir: str | Path = "data/weather/goes_cache") -> None:
        if satellite not in self.BUCKETS:
            raise ValueError(f"Unsupported GOES satellite {satellite}")
        self.satellite = satellite
        self.bucket = self.BUCKETS[satellite]
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED), region_name="us-east-1")

    @staticmethod
    def _prefix(dt: datetime) -> str:
        dt = dt.astimezone(timezone.utc)
        return f"{GoesAbiProvider.PRODUCT}/{dt.year}/{dt.timetuple().tm_yday:03d}/{dt.hour:02d}/"

    @staticmethod
    def _band_token(band: int) -> str:
        return f"C{band:02d}"

    def _list_candidates_sync(self, band: int, around: datetime, lookback_minutes: int = 20) -> list[GoesObject]:
        if band not in (2, 13):
            raise ValueError("This alpha provider currently supports ABI bands 2 and 13")
        start = around.astimezone(timezone.utc) - timedelta(minutes=lookback_minutes)
        hours = sorted({start.replace(minute=0, second=0, microsecond=0), around.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)})
        token = self._band_token(band)
        out: list[GoesObject] = []
        for hour in hours:
            paginator = self.s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self._prefix(hour)):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if token not in key or not key.endswith(".nc"):
                        continue
                    lm = obj["LastModified"].astimezone(timezone.utc)
                    if lm >= start - timedelta(minutes=5) and lm <= around.astimezone(timezone.utc) + timedelta(minutes=10):
                        out.append(GoesObject(self.satellite, band, key, lm))
        return sorted(out, key=lambda x: x.last_modified)

    async def latest_object(self, band: int, around: datetime | None = None, lookback_minutes: int = 20) -> GoesObject:
        around = around or datetime.now(timezone.utc)
        rows = await asyncio.to_thread(self._list_candidates_sync, band, around, lookback_minutes)
        if not rows:
            raise FileNotFoundError(f"No {self.PRODUCT} band {band} object found in {self.bucket}")
        return rows[-1]

    def _download_sync(self, obj: GoesObject) -> Path:
        target = self.cache_dir / Path(obj.key).name
        if not target.exists() or target.stat().st_size == 0:
            tmp = target.with_suffix(target.suffix + ".part")
            self.s3.download_file(self.bucket, obj.key, str(tmp))
            os.replace(tmp, target)
        return target

    async def download(self, obj: GoesObject) -> Path:
        return await asyncio.to_thread(self._download_sync, obj)

    @staticmethod
    def _xy_scan_angles(ds: xr.Dataset, lat: float, lon: float) -> tuple[float, float]:
        proj = ds["goes_imager_projection"]
        attrs = dict(proj.attrs)
        h = float(attrs["perspective_point_height"])
        geos = CRS.from_proj4(
            "+proj=geos "
            f"+h={h} "
            f"+lon_0={float(attrs['longitude_of_projection_origin'])} "
            f"+sweep={attrs.get('sweep_angle_axis', 'x')} "
            f"+a={float(attrs['semi_major_axis'])} "
            f"+b={float(attrs['semi_minor_axis'])} +units=m +no_defs"
        )
        transformer = Transformer.from_crs("EPSG:4326", geos, always_xy=True)
        x_m, y_m = transformer.transform(lon, lat)
        return float(x_m / h), float(y_m / h)

    @classmethod
    def _crop_station(cls, ds: xr.Dataset, lat: float, lon: float, radius_km: float = 12.0) -> xr.DataArray:
        x0, y0 = cls._xy_scan_angles(ds, lat, lon)
        # ABI scan angle scale is roughly distance / satellite height near nadir.
        # We use a slightly expanded window; exact ground footprint varies with view angle.
        h = float(ds["goes_imager_projection"].attrs["perspective_point_height"])
        delta = (radius_km * 1000.0) / h * 1.8
        x = ds["x"].values
        y = ds["y"].values
        xi = np.where((x >= x0 - delta) & (x <= x0 + delta))[0]
        yi = np.where((y >= y0 - delta) & (y <= y0 + delta))[0]
        if not len(xi) or not len(yi):
            raise ValueError("Station is outside the selected GOES product domain")
        return ds["CMI"].isel(x=slice(int(xi.min()), int(xi.max()) + 1), y=slice(int(yi.min()), int(yi.max()) + 1))

    @staticmethod
    def _valid_values(da: xr.DataArray) -> np.ndarray:
        a = np.asarray(da.values, dtype=float)
        return a[np.isfinite(a)]

    @classmethod
    def extract_band2(cls, path: Path, lat: float, lon: float, radius_km: float = 12.0) -> tuple[float, float, float, float]:
        with xr.open_dataset(path, engine="h5netcdf", mask_and_scale=True) as ds:
            vals = cls._valid_values(cls._crop_station(ds, lat, lon, radius_km))
        if not vals.size:
            raise ValueError("No valid ABI Band 2 pixels in station crop")
        # CMIP reflective-band CMI is a reflectance factor. Clip extreme invalid/noisy values.
        refl = np.clip(vals, 0.0, 1.6)
        mean_refl = float(np.mean(refl))
        p90 = float(np.quantile(refl, 0.90))
        # Fast cloud attenuation feature, not a formal ABI COD retrieval.
        # -ln(transmission) maps 0..1 attenuation into a positive optical-depth-like scale.
        transmission = float(np.clip(1.0 - 0.82 * mean_refl, 0.03, 1.0))
        tau_proxy = float(-math.log(transmission))
        return mean_refl, p90, tau_proxy, transmission

    @classmethod
    def extract_band13(cls, path: Path, lat: float, lon: float, radius_km: float = 12.0, cold_threshold_k: float = 270.0) -> tuple[float, float]:
        with xr.open_dataset(path, engine="h5netcdf", mask_and_scale=True) as ds:
            vals = cls._valid_values(cls._crop_station(ds, lat, lon, radius_km))
        if not vals.size:
            raise ValueError("No valid ABI Band 13 pixels in station crop")
        bt = vals[(vals > 150.0) & (vals < 350.0)]
        if not bt.size:
            raise ValueError("No plausible ABI Band 13 brightness temperatures")
        return float(np.mean(bt)), float(np.mean(bt <= cold_threshold_k))

    async def station_features(
        self,
        station: str,
        lat: float,
        lon: float,
        around: datetime | None = None,
        radius_km: float = 12.0,
    ) -> GoesStationFeatures:
        around = around or datetime.now(timezone.utc)
        b2_obj, b13_obj = await asyncio.gather(self.latest_object(2, around), self.latest_object(13, around))
        b2_path, b13_path = await asyncio.gather(self.download(b2_obj), self.download(b13_obj))
        b2 = await asyncio.to_thread(self.extract_band2, b2_path, lat, lon, radius_km)
        b13 = await asyncio.to_thread(self.extract_band13, b13_path, lat, lon, radius_km)
        return GoesStationFeatures(
            station=station,
            observed_at=max(b2_obj.last_modified, b13_obj.last_modified),
            satellite=self.satellite,
            band2_reflectance_mean=b2[0],
            band2_reflectance_p90=b2[1],
            band13_brightness_temp_k_mean=b13[0],
            cold_cloud_fraction=b13[1],
            cloud_optical_depth_proxy=b2[2],
            solar_transmission_proxy=b2[3],
            source_keys=(b2_obj.key, b13_obj.key),
        )
