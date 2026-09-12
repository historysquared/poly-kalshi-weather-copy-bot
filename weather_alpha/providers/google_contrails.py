from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import xarray as xr


@dataclass(frozen=True)
class ContrailDetectionSummary:
    start_time: datetime
    end_time: datetime
    latitude: float
    longitude: float
    radius_km: float
    detection_count: int
    total_length_km: float
    max_length_km: float
    nearest_detection_km: float | None
    satellite_counts: dict[str, int]


@dataclass(frozen=True)
class ContrailForecastPoint:
    valid_time: datetime
    forecast_reference_time: datetime | None
    latitude: float
    longitude: float
    max_cfi: float | None
    mean_cfi: float | None
    max_persistent_formation_probability: float | None
    max_nominal_cocip_effective_energy_forcing: float | None
    peak_flight_level: int | None


class GoogleContrailsClient:
    """Thin async client for Google's public Contrails API v2.

    Detections are Google-produced satellite-observed contrail LineStrings.
    Forecast grids expose CFI (0..4), persistent formation probability, and
    CoCiP energy-forcing fields. CFI is a contrail-warming severity index, not a
    surface-temperature change in degrees.
    """

    BASE = "https://contrails.googleapis.com/v2"

    def __init__(self, api_key: str | None = None, timeout: float = 45.0) -> None:
        self.api_key = api_key or os.environ.get("GOOGLE_CONTRAILS_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_CONTRAILS_API_KEY must be set")
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key, "User-Agent": "weather-alpha-lab/contrails"}

    @staticmethod
    def _utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _iso(dt: datetime) -> str:
        return GoogleContrailsClient._utc(dt).isoformat().replace("+00:00", "Z")

    @staticmethod
    def bounds_square(latitude: float, longitude: float, radius_km: float) -> list[str]:
        dlat = radius_km / 111.0
        cos_lat = max(0.15, math.cos(math.radians(latitude)))
        dlon = radius_km / (111.0 * cos_lat)
        south, north = latitude - dlat, latitude + dlat
        west, east = longitude - dlon, longitude + dlon
        return [
            f"{south:.6f},{west:.6f}",
            f"{south:.6f},{east:.6f}",
            f"{north:.6f},{east:.6f}",
            f"{north:.6f},{west:.6f}",
        ]

    @staticmethod
    def bbox(latitude: float, longitude: float, radius_km: float) -> list[float]:
        dlat = radius_km / 111.0
        cos_lat = max(0.15, math.cos(math.radians(latitude)))
        dlon = radius_km / (111.0 * cos_lat)
        return [longitude - dlon, latitude - dlat, longitude + dlon, latitude + dlat]

    @staticmethod
    def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371.0088
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))

    @classmethod
    def line_length_km(cls, coords: list[list[float]]) -> float:
        total = 0.0
        for a, b in zip(coords, coords[1:]):
            if len(a) < 2 or len(b) < 2:
                continue
            total += cls.haversine_km(float(a[1]), float(a[0]), float(b[1]), float(b[0]))
        return total

    async def detection_summary(
        self,
        start_time: datetime,
        end_time: datetime,
        latitude: float,
        longitude: float,
        radius_km: float = 150.0,
        satellite_origin: str | None = None,
    ) -> ContrailDetectionSummary:
        start = self._utc(start_time)
        end = self._utc(end_time)
        if end <= start:
            raise ValueError("end_time must be after start_time")
        if (end - start).total_seconds() > 24 * 3600:
            raise ValueError("Google detections requests are limited to 24 hours")

        params: list[tuple[str, str]] = [
            ("start_time", self._iso(start)),
            ("end_time", self._iso(end)),
        ]
        params.extend(("bounds", value) for value in self.bounds_square(latitude, longitude, radius_km))
        if satellite_origin:
            params.append(("satellite_origins", satellite_origin))

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(f"{self.BASE}/detections", params=params, headers=self.headers)
            response.raise_for_status()
            payload = response.json()

        lengths: list[float] = []
        nearest: float | None = None
        satellite_counts: dict[str, int] = {}
        for feature in payload.get("features", []) if isinstance(payload, dict) else []:
            if not isinstance(feature, dict):
                continue
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates") or []
            if geometry.get("type") != "LineString" or not isinstance(coords, list):
                continue
            length = self.line_length_km(coords)
            lengths.append(length)
            for point in coords:
                if isinstance(point, list) and len(point) >= 2:
                    d = self.haversine_km(latitude, longitude, float(point[1]), float(point[0]))
                    nearest = d if nearest is None else min(nearest, d)
            origin = str((feature.get("properties") or {}).get("satellite_origin") or "unknown")
            satellite_counts[origin] = satellite_counts.get(origin, 0) + 1

        return ContrailDetectionSummary(
            start_time=start,
            end_time=end,
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            detection_count=len(lengths),
            total_length_km=float(sum(lengths)),
            max_length_km=float(max(lengths, default=0.0)),
            nearest_detection_km=nearest,
            satellite_counts=satellite_counts,
        )

    async def forecast_point(
        self,
        valid_time: datetime,
        latitude: float,
        longitude: float,
        radius_km: float = 35.0,
        flight_levels: tuple[int, ...] = (300, 310, 320, 330, 340, 350, 360, 370, 380, 390, 400),
    ) -> ContrailForecastPoint:
        params: list[tuple[str, str]] = [("time", self._iso(valid_time))]
        params.extend(("bbox", str(x)) for x in self.bbox(latitude, longitude, radius_km))
        params.extend(("flightLevel", str(fl)) for fl in flight_levels)
        for variable in (
            "contrails",
            "persistent_formation_probability",
            "nominal_cocip_effective_energy_forcing",
        ):
            params.append(("data", variable))

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(f"{self.BASE}/grids", params=params, headers=self.headers)
            response.raise_for_status()
            content = response.content

        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as fh:
            fh.write(content)
            path = Path(fh.name)
        try:
            with xr.open_dataset(path) as ds:
                point = ds.sel(latitude=latitude, longitude=longitude, method="nearest")
                cfi = np.asarray(point["contrails"].values, dtype=float) if "contrails" in point else np.array([])
                prob = (
                    np.asarray(point["persistent_formation_probability"].values, dtype=float)
                    if "persistent_formation_probability" in point
                    else np.array([])
                )
                eef = (
                    np.asarray(point["nominal_cocip_effective_energy_forcing"].values, dtype=float)
                    if "nominal_cocip_effective_energy_forcing" in point
                    else np.array([])
                )
                max_cfi = float(np.nanmax(cfi)) if cfi.size and np.isfinite(cfi).any() else None
                mean_cfi = float(np.nanmean(cfi)) if cfi.size and np.isfinite(cfi).any() else None
                max_prob = float(np.nanmax(prob)) if prob.size and np.isfinite(prob).any() else None
                max_eef = float(np.nanmax(eef)) if eef.size and np.isfinite(eef).any() else None
                peak_fl = None
                if cfi.size and "flight_level" in point.coords and np.isfinite(cfi).any():
                    values = np.asarray(point["contrails"].squeeze().values, dtype=float)
                    levels = np.asarray(point["flight_level"].values)
                    if values.ndim == 1 and len(values) == len(levels):
                        peak_fl = int(levels[int(np.nanargmax(values))])
                ref = None
                if "forecast_reference_time" in ds:
                    raw = np.asarray(ds["forecast_reference_time"].values).reshape(-1)[0]
                    ref = datetime.fromisoformat(str(np.datetime_as_string(raw, unit="s"))).replace(tzinfo=timezone.utc)
        finally:
            path.unlink(missing_ok=True)

        return ContrailForecastPoint(
            valid_time=self._utc(valid_time),
            forecast_reference_time=ref,
            latitude=latitude,
            longitude=longitude,
            max_cfi=max_cfi,
            mean_cfi=mean_cfi,
            max_persistent_formation_probability=max_prob,
            max_nominal_cocip_effective_energy_forcing=max_eef,
            peak_flight_level=peak_fl,
        )
