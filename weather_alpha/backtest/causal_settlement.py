from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Sequence


@dataclass(frozen=True)
class TimedTemperature:
    valid_time: datetime
    temperature_f: Decimal


@dataclass(frozen=True)
class CausalSurfaceState:
    as_of: datetime
    high_so_far_f: Decimal | None
    latest_temp_f: Decimal | None
    minutes_since_high: Decimal | None
    drop_from_high_f: Decimal | None
    slope_15m_f_per_min: Decimal | None
    observations_used: int


@dataclass(frozen=True)
class EmpiricalPosterior:
    sample_count: int
    probability_yes: Decimal | None
    basis_samples_f: tuple[Decimal, ...]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return dt.astimezone(timezone.utc)


def surface_state(observations: Iterable[TimedTemperature], *, as_of: datetime) -> CausalSurfaceState:
    cutoff = _utc(as_of)
    rows = sorted((o for o in observations if _utc(o.valid_time) <= cutoff), key=lambda o: o.valid_time)
    if not rows:
        return CausalSurfaceState(cutoff, None, None, None, None, None, 0)

    high = max(o.temperature_f for o in rows)
    high_times = [o.valid_time for o in rows if o.temperature_f == high]
    latest = rows[-1]
    last_high_time = max(high_times)
    mins_since = Decimal(str((cutoff - _utc(last_high_time)).total_seconds() / 60.0))
    drop = high - latest.temperature_f

    slope = None
    lookback = cutoff.timestamp() - 15 * 60
    window = [o for o in rows if _utc(o.valid_time).timestamp() >= lookback]
    if len(window) >= 2:
        dt_min = Decimal(str((_utc(window[-1].valid_time) - _utc(window[0].valid_time)).total_seconds() / 60.0))
        if dt_min > 0:
            slope = (window[-1].temperature_f - window[0].temperature_f) / dt_min

    return CausalSurfaceState(
        as_of=cutoff,
        high_so_far_f=high,
        latest_temp_f=latest.temperature_f,
        minutes_since_high=mins_since,
        drop_from_high_f=drop,
        slope_15m_f_per_min=slope,
        observations_used=len(rows),
    )


def lock_gate(
    state: CausalSurfaceState,
    *,
    min_minutes_since_high: Decimal = Decimal("60"),
    min_drop_from_high_f: Decimal = Decimal("1.0"),
    max_positive_slope_f_per_min: Decimal = Decimal("0.02"),
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if state.high_so_far_f is None or state.latest_temp_f is None:
        reasons.append("NO_SURFACE_DATA")
    if state.minutes_since_high is None or state.minutes_since_high < min_minutes_since_high:
        reasons.append("HIGH_NOT_STALE_ENOUGH")
    if state.drop_from_high_f is None or state.drop_from_high_f < min_drop_from_high_f:
        reasons.append("INSUFFICIENT_DROP_FROM_HIGH")
    if state.slope_15m_f_per_min is not None and state.slope_15m_f_per_min > max_positive_slope_f_per_min:
        reasons.append("TEMPERATURE_STILL_RISING")
    return (len(reasons) == 0, tuple(reasons))


def contract_contains(shape: str, lower: Decimal | None, upper: Decimal | None, value: Decimal) -> bool:
    shape = str(shape).lower()
    if shape == "below":
        if upper is None:
            raise ValueError("below contract requires upper")
        return value <= upper
    if shape == "above":
        if lower is None:
            raise ValueError("above contract requires lower")
        return value >= lower
    if shape == "bucket":
        if lower is None or upper is None:
            raise ValueError("bucket contract requires lower and upper")
        return lower <= value <= upper
    raise ValueError(f"unsupported shape: {shape}")


def empirical_settlement_posterior(
    *,
    high_so_far_f: Decimal,
    prior_basis_samples_f: Sequence[Decimal],
    shape: str,
    lower: Decimal | None,
    upper: Decimal | None,
    minimum_samples: int = 20,
) -> EmpiricalPosterior:
    samples = tuple(Decimal(x) for x in prior_basis_samples_f)
    if len(samples) < minimum_samples:
        return EmpiricalPosterior(len(samples), None, samples)
    wins = sum(contract_contains(shape, lower, upper, high_so_far_f + basis) for basis in samples)
    return EmpiricalPosterior(len(samples), Decimal(wins) / Decimal(len(samples)), samples)
