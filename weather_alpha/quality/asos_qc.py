from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Iterable


class QCStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class QCConfig:
    min_temp_f: float = -90.0
    max_temp_f: float = 140.0
    max_jump_f_per_minute: float = 2.5
    max_gap_minutes_warn: float = 10.0
    max_gap_minutes_fail: float = 30.0
    flatline_minutes_warn: float = 120.0
    flatline_minutes_fail: float = 240.0
    stale_minutes_warn: float = 10.0
    stale_minutes_fail: float = 30.0


@dataclass(frozen=True)
class QCResult:
    status: QCStatus
    reason_codes: tuple[str, ...]
    latest_time: datetime | None
    latest_temperature_f: float | None
    max_gap_minutes: float
    max_abs_jump_f_per_minute: float
    flatline_minutes: float
    age_minutes: float | None

    @property
    def live_trading_allowed(self) -> bool:
        return self.status == QCStatus.PASS


def _promote(current: QCStatus, candidate: QCStatus) -> QCStatus:
    order = {QCStatus.PASS: 0, QCStatus.WARN: 1, QCStatus.FAIL: 2}
    return candidate if order[candidate] > order[current] else current


def assess_asos_temperature_quality(
    observations: Iterable[object],
    *,
    as_of: datetime,
    config: QCConfig | None = None,
    time_attr: str = "valid_time",
    temperature_attr: str = "temperature_f",
) -> QCResult:
    """Run fail-closed QC over settling-station temperature observations.

    Thresholds are intentionally configurable. They are initial operational
    guards, not claims about universal physical limits; station/season-specific
    calibration should replace them where historical evidence warrants it.
    """

    cfg = config or QCConfig()
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    now = as_of.astimezone(timezone.utc)

    raw: list[tuple[datetime, float]] = []
    seen: set[datetime] = set()
    duplicate = False
    for obs in observations:
        ts = getattr(obs, time_attr, None)
        temp = getattr(obs, temperature_attr, None)
        if ts is None or temp is None:
            continue
        if ts.tzinfo is None:
            raise ValueError("observation timestamps must be timezone-aware")
        t = ts.astimezone(timezone.utc)
        if t > now:
            continue
        if t in seen:
            duplicate = True
        seen.add(t)
        raw.append((t, float(temp)))
    raw.sort(key=lambda x: x[0])

    if not raw:
        return QCResult(QCStatus.FAIL, ("NO_VALID_TEMPERATURE",), None, None, 0.0, 0.0, 0.0, None)

    status = QCStatus.PASS
    reasons: list[str] = []
    if duplicate:
        status = _promote(status, QCStatus.WARN)
        reasons.append("DUPLICATE_TIMESTAMP")

    for _, temp in raw:
        if temp < cfg.min_temp_f or temp > cfg.max_temp_f:
            status = QCStatus.FAIL
            reasons.append("TEMPERATURE_OUT_OF_RANGE")
            break

    max_gap = 0.0
    max_jump_rate = 0.0
    for (t0, x0), (t1, x1) in zip(raw, raw[1:]):
        mins = (t1 - t0).total_seconds() / 60.0
        if mins <= 0:
            continue
        max_gap = max(max_gap, mins)
        max_jump_rate = max(max_jump_rate, abs(x1 - x0) / mins)
    if max_gap > cfg.max_gap_minutes_fail:
        status = QCStatus.FAIL
        reasons.append("OBSERVATION_GAP_FAIL")
    elif max_gap > cfg.max_gap_minutes_warn:
        status = _promote(status, QCStatus.WARN)
        reasons.append("OBSERVATION_GAP_WARN")
    if max_jump_rate > cfg.max_jump_f_per_minute:
        status = QCStatus.FAIL
        reasons.append("IMPLAUSIBLE_TEMPERATURE_JUMP")

    latest_time, latest_temp = raw[-1]
    age_minutes = max(0.0, (now - latest_time).total_seconds() / 60.0)
    if age_minutes > cfg.stale_minutes_fail:
        status = QCStatus.FAIL
        reasons.append("STALE_STATION_FAIL")
    elif age_minutes > cfg.stale_minutes_warn:
        status = _promote(status, QCStatus.WARN)
        reasons.append("STALE_STATION_WARN")

    # Flatline duration is measured backward from the latest identical value.
    flatline_start = latest_time
    for t, temp in reversed(raw[:-1]):
        if abs(temp - latest_temp) > 1e-12:
            break
        flatline_start = t
    flatline_minutes = (latest_time - flatline_start).total_seconds() / 60.0
    if flatline_minutes >= cfg.flatline_minutes_fail:
        status = QCStatus.FAIL
        reasons.append("TEMPERATURE_FLATLINE_FAIL")
    elif flatline_minutes >= cfg.flatline_minutes_warn:
        status = _promote(status, QCStatus.WARN)
        reasons.append("TEMPERATURE_FLATLINE_WARN")

    return QCResult(
        status=status,
        reason_codes=tuple(dict.fromkeys(reasons)),
        latest_time=latest_time,
        latest_temperature_f=latest_temp,
        max_gap_minutes=max_gap,
        max_abs_jump_f_per_minute=max_jump_rate,
        flatline_minutes=flatline_minutes,
        age_minutes=age_minutes,
    )
