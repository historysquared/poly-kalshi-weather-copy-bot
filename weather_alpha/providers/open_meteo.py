from __future__ import annotations
from datetime import datetime, timezone
from statistics import mean, pstdev
import httpx
from ..models import ForecastDistribution

class OpenMeteoEnsembleClient:
    BASE = "https://ensemble-api.open-meteo.com/v1/ensemble"

    async def daily_high_distribution(
        self,
        station: str,
        lat: float,
        lon: float,
        target_date: str,
        model: str = "gfs_seamless",
        timezone_name: str = "America/New_York",
    ) -> ForecastDistribution:
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m",
            "models": model,
            "temperature_unit": "fahrenheit",
            "timezone": timezone_name,
            "forecast_days": 7,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(self.BASE, params=params)
            r.raise_for_status()
            data = r.json()
        hourly = data["hourly"]
        times = hourly["time"]
        member_keys = [k for k in hourly if k.startswith("temperature_2m_member")]
        if not member_keys and "temperature_2m" in hourly:
            member_keys = ["temperature_2m"]
        daily_highs = []
        for key in member_keys:
            vals = [v for t, v in zip(times, hourly[key]) if t[:10] == target_date and v is not None]
            if vals:
                daily_highs.append(max(vals))
        if not daily_highs:
            raise ValueError(f"No ensemble temperature values returned for {station} {target_date}")
        mu = mean(daily_highs)
        sigma = max(0.75, pstdev(daily_highs) if len(daily_highs) > 1 else 0.75)
        return ForecastDistribution(
            station=station,
            valid_date=target_date,
            source=f"open_meteo:{model}",
            mean_f=float(mu),
            sigma_f=float(sigma),
            members_f=tuple(float(x) for x in daily_highs),
            issued_at=datetime.now(timezone.utc),
        )
