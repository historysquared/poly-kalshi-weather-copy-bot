from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from math import exp
from statistics import mean, pstdev
from .models import Observation, ForecastDistribution

@dataclass(frozen=True)
class Nowcast:
    station: str
    estimated_now_f: float
    projected_daily_high_f: float
    sigma_f: float
    official_anchor_f: float
    nearby_trend_f_per_hour: float
    forecast_mean_f: float
    confidence: float

def settlement_nowcast(
    official: Observation,
    nearby_current: list[Observation],
    nearby_previous: list[Observation],
    forecasts: list[ForecastDistribution],
    now: datetime | None = None,
) -> Nowcast:
    now = now or datetime.now(timezone.utc)
    anchor_age_min = max(0.0, (now - official.observed_at).total_seconds() / 60.0)
    prev_by_station = {o.station: o for o in nearby_previous}
    slopes: list[float] = []
    for cur in nearby_current:
        prev = prev_by_station.get(cur.station)
        if not prev:
            continue
        dt_h = (cur.observed_at - prev.observed_at).total_seconds() / 3600.0
        if 0.03 <= dt_h <= 1.5:
            slopes.append((cur.temp_f - prev.temp_f) / dt_h)
    trend = mean(slopes) if slopes else 0.0
    trend_horizon_h = min(anchor_age_min / 60.0, 0.5)
    trend_adjustment = max(-2.5, min(2.5, trend * trend_horizon_h))
    estimated_now = official.temp_f + trend_adjustment
    if forecasts:
        weights = [1.0 / max(f.sigma_f, 0.75) ** 2 for f in forecasts]
        total = sum(weights)
        forecast_mean = sum(f.mean_f * w for f, w in zip(forecasts, weights)) / total
        forecast_sigma = max(0.75, mean([f.sigma_f for f in forecasts]))
    else:
        forecast_mean = estimated_now
        forecast_sigma = 2.0
    forecast_weight = min(0.55, 0.12 + anchor_age_min / 120.0)
    projected_high = max(official.temp_f, (1 - forecast_weight) * estimated_now + forecast_weight * forecast_mean)
    disagreement = abs(forecast_mean - estimated_now)
    slope_dispersion = pstdev(slopes) if len(slopes) > 1 else 0.0
    sigma = max(0.55, min(4.0, 0.55 + 0.20 * forecast_sigma + 0.18 * disagreement + 0.08 * slope_dispersion))
    confidence = exp(-0.35 * sigma) * exp(-0.01 * anchor_age_min)
    return Nowcast(
        station=official.station,
        estimated_now_f=round(estimated_now, 3),
        projected_daily_high_f=round(projected_high, 3),
        sigma_f=round(sigma, 3),
        official_anchor_f=official.temp_f,
        nearby_trend_f_per_hour=round(trend, 3),
        forecast_mean_f=round(forecast_mean, 3),
        confidence=round(max(0.0, min(1.0, confidence)), 4),
    )
