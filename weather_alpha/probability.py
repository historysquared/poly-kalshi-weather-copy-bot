from __future__ import annotations
from math import erf, sqrt
from statistics import mean, pstdev

SQRT2 = sqrt(2.0)

def _cdf(x: float, mu: float, sigma: float) -> float:
    sigma = max(float(sigma), 0.15)
    return 0.5 * (1.0 + erf((x - mu) / (sigma * SQRT2)))

def gaussian_bucket_probability(mu_f: float, sigma_f: float, low_f: float, high_f: float) -> float:
    if high_f < low_f:
        raise ValueError("high_f must be >= low_f")
    return max(0.0, min(1.0, _cdf(high_f + 0.5, mu_f, sigma_f) - _cdf(low_f - 0.5, mu_f, sigma_f)))

def gaussian_above_probability(mu_f: float, sigma_f: float, threshold_f: float) -> float:
    return max(0.0, min(1.0, 1.0 - _cdf(threshold_f - 0.5, mu_f, sigma_f)))

def gaussian_below_probability(mu_f: float, sigma_f: float, threshold_f: float) -> float:
    return max(0.0, min(1.0, _cdf(threshold_f + 0.5, mu_f, sigma_f)))

def empirical_distribution(members_f: list[float], sigma_floor_f: float = 0.75) -> tuple[float, float]:
    if not members_f:
        raise ValueError("members_f cannot be empty")
    mu = mean(members_f)
    sigma = max(pstdev(members_f) if len(members_f) > 1 else 0.0, sigma_floor_f)
    return float(mu), float(sigma)

def brier_score(probability: float, outcome: bool) -> float:
    p = min(1.0, max(0.0, probability))
    y = 1.0 if outcome else 0.0
    return (p - y) ** 2
