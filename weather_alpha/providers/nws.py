from __future__ import annotations
from datetime import datetime
import httpx
from ..models import Observation

class NWSObservationClient:
    def __init__(self, user_agent: str = "weather-alpha-lab/0.1 contact=research"):
        self.headers = {"User-Agent": user_agent, "Accept": "application/geo+json"}

    async def latest(self, station: str) -> Observation:
        url = f"https://api.weather.gov/stations/{station}/observations/latest"
        async with httpx.AsyncClient(timeout=20, headers=self.headers) as client:
            r = await client.get(url)
            r.raise_for_status()
            p = r.json()["properties"]
        c = p["temperature"]["value"]
        if c is None:
            raise ValueError(f"NWS observation for {station} has no temperature")
        dew_c = (p.get("dewpoint") or {}).get("value")
        wind_kmh = (p.get("windSpeed") or {}).get("value")
        return Observation(
            station=station,
            observed_at=datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00")),
            temp_f=c * 9 / 5 + 32,
            dewpoint_f=(dew_c * 9 / 5 + 32) if dew_c is not None else None,
            wind_mph=(wind_kmh * 0.621371) if wind_kmh is not None else None,
            wind_dir_deg=(p.get("windDirection") or {}).get("value"),
            pressure_hpa=((p.get("barometricPressure") or {}).get("value") or 0) / 100 if (p.get("barometricPressure") or {}).get("value") is not None else None,
            provider="nws",
        )

    async def recent(self, station: str, limit: int = 12) -> list[Observation]:
        url = f"https://api.weather.gov/stations/{station}/observations"
        params = {"limit": limit}
        async with httpx.AsyncClient(timeout=20, headers=self.headers) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            features = r.json().get("features", [])
        out = []
        for f in features:
            p = f.get("properties", {})
            c = (p.get("temperature") or {}).get("value")
            if c is None or not p.get("timestamp"):
                continue
            out.append(Observation(station=station, observed_at=datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00")), temp_f=c * 9 / 5 + 32, provider="nws"))
        return out
