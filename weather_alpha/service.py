from __future__ import annotations
from datetime import date
from .markets.kalshi import KalshiPublicClient
from .providers.nws import NWSObservationClient
from .providers.open_meteo import OpenMeteoEnsembleClient
from .probability import gaussian_above_probability, gaussian_below_probability, gaussian_bucket_probability
from .signals import signal_from_probability
from .storage import JsonlArchive

class WeatherAlphaService:
    def __init__(self):
        self.kalshi = KalshiPublicClient()
        self.nws = NWSObservationClient()
        self.ensemble = OpenMeteoEnsembleClient()
        self.archive = JsonlArchive()

    async def scan_series(self, station: str, lat: float, lon: float, series: str, target_date: str | None = None):
        target_date = target_date or date.today().isoformat()
        obs = await self.nws.latest(station)
        dist = await self.ensemble.daily_high_distribution(station, lat, lon, target_date)
        markets = await self.kalshi.open_markets(series)
        results = []
        for m in markets:
            if m.floor_strike is not None and m.cap_strike is not None:
                p_yes = gaussian_bucket_probability(dist.mean_f, dist.sigma_f, m.floor_strike, m.cap_strike)
            elif m.floor_strike is not None:
                p_yes = gaussian_above_probability(dist.mean_f, dist.sigma_f, m.floor_strike)
            elif m.cap_strike is not None:
                p_yes = gaussian_below_probability(dist.mean_f, dist.sigma_f, m.cap_strike)
            else:
                continue
            sig = signal_from_probability(m, p_yes)
            row = {
                "station": station,
                "observation_f": obs.temp_f,
                "forecast_mean_f": dist.mean_f,
                "forecast_sigma_f": dist.sigma_f,
                "ticker": m.ticker,
                "p_yes": p_yes,
                "yes_bid": m.yes_bid,
                "yes_ask": m.yes_ask,
                "signal": sig.__dict__ if sig else None,
            }
            self.archive.append("scans", row)
            results.append(row)
        return results
