from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from typing import Iterable


@dataclass(frozen=True)
class StationClock:
    """Clock definition for NWS-style Local Standard Time settlement windows.

    `standard_utc_offset_hours` is the fixed standard-time offset (for example,
    -6 for Chicago/Central Standard Time) and intentionally does not change
    during daylight-saving periods.
    """

    station: str
    civil_timezone: str
    standard_utc_offset_hours: int


@dataclass(frozen=True)
class SettlementWindow:
    station: str
    settlement_date: date
    start_utc: datetime
    end_utc: datetime
    start_civil: datetime
    end_civil: datetime
    standard_utc_offset_hours: int


@dataclass(frozen=True)
class SettlementReconstruction:
    station: str
    settlement_date: date
    observed_extreme_f: float | None
    official_extreme_f: float | None
    residual_f: float | None
    exact_match: bool | None
    within_one_f: bool | None
    observations_used: int
    window: SettlementWindow


def local_standard_settlement_window(clock: StationClock, settlement_date: date) -> SettlementWindow:
    """Return the 24h [00:00, 24:00) Local Standard Time window in UTC.

    This deliberately uses a fixed standard-time offset rather than the civil
    timezone's DST offset. The civil timestamps are included for diagnostics;
    in summer they will commonly appear as 01:00 -> 01:00 local daylight time.
    """

    fixed = timezone(timedelta(hours=clock.standard_utc_offset_hours))
    civil = ZoneInfo(clock.civil_timezone)
    start_standard = datetime.combine(settlement_date, time.min, tzinfo=fixed)
    end_standard = start_standard + timedelta(days=1)
    start_utc = start_standard.astimezone(timezone.utc)
    end_utc = end_standard.astimezone(timezone.utc)
    return SettlementWindow(
        station=clock.station,
        settlement_date=settlement_date,
        start_utc=start_utc,
        end_utc=end_utc,
        start_civil=start_utc.astimezone(civil),
        end_civil=end_utc.astimezone(civil),
        standard_utc_offset_hours=clock.standard_utc_offset_hours,
    )


def nws_round_celsius(value_c: Decimal | float | int) -> int:
    """Round to the nearest whole degree using decimal half-up semantics."""

    value = value_c if isinstance(value_c, Decimal) else Decimal(str(value_c))
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def whole_f_candidates_for_reported_c(reported_c: int, *, search_min_f: int = -120, search_max_f: int = 160) -> tuple[int, ...]:
    """Invert a whole-C public report into compatible whole-F source values.

    This captures the deterministic F->C rounding ambiguity that can matter at
    contract boundaries. Example: a reported 47C is compatible with 116F and
    117F under nearest-whole-C conversion.
    """

    candidates: list[int] = []
    for whole_f in range(search_min_f, search_max_f + 1):
        c = (Decimal(whole_f) - Decimal(32)) * Decimal(5) / Decimal(9)
        if nws_round_celsius(c) == int(reported_c):
            candidates.append(whole_f)
    return tuple(candidates)


def extreme_within_window(
    observations: Iterable[object],
    window: SettlementWindow,
    *,
    kind: str = "high",
    time_attr: str = "valid_time",
    temperature_attr: str = "temperature_f",
) -> tuple[float | None, int]:
    """Compute the max/min observed value inside the exact settlement window."""

    if kind not in {"high", "low"}:
        raise ValueError("kind must be 'high' or 'low'")
    values: list[float] = []
    for obs in observations:
        ts = getattr(obs, time_attr, None)
        value = getattr(obs, temperature_attr, None)
        if ts is None or value is None:
            continue
        if ts.tzinfo is None:
            raise ValueError("observation timestamps must be timezone-aware")
        ts_utc = ts.astimezone(timezone.utc)
        if window.start_utc <= ts_utc < window.end_utc:
            values.append(float(value))
    if not values:
        return None, 0
    return (max(values) if kind == "high" else min(values)), len(values)


def reconstruct_daily_extreme(
    observations: Iterable[object],
    *,
    clock: StationClock,
    settlement_date: date,
    official_extreme_f: float | None = None,
    kind: str = "high",
) -> SettlementReconstruction:
    window = local_standard_settlement_window(clock, settlement_date)
    observed, count = extreme_within_window(observations, window, kind=kind)
    residual = None if observed is None or official_extreme_f is None else float(official_extreme_f) - observed
    exact = None if residual is None else abs(residual) < 1e-9
    within_one = None if residual is None else abs(residual) <= 1.0 + 1e-9
    return SettlementReconstruction(
        station=clock.station,
        settlement_date=settlement_date,
        observed_extreme_f=observed,
        official_extreme_f=float(official_extreme_f) if official_extreme_f is not None else None,
        residual_f=residual,
        exact_match=exact,
        within_one_f=within_one,
        observations_used=count,
        window=window,
    )
