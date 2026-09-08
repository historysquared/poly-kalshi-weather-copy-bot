from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import sqrt
from random import Random
from statistics import mean
from typing import Iterable, Sequence


class Verdict(str, Enum):
    KEEP = "keep"
    PROVISIONAL = "provisional"
    KILL = "kill"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class EventReturn:
    strategy: str
    event_id: str
    date: str
    station: str
    latency_seconds: int
    execution_case: str
    pnl: float
    capital: float

    @property
    def roi(self) -> float:
        return self.pnl / self.capital if self.capital > 0 else 0.0


@dataclass(frozen=True)
class ValidationResult:
    strategy: str
    events: int
    stations: int
    net_pnl: float
    mean_event_roi: float
    roi_ci_low: float
    roi_ci_high: float
    profitable_event_rate: float
    max_drawdown: float
    latency_seconds: int
    execution_case: str
    verdict: Verdict
    reason: str


def _event_aggregate(rows: Iterable[EventReturn]) -> list[EventReturn]:
    grouped: dict[tuple[str, str, str, int, str], list[EventReturn]] = {}
    for r in rows:
        key = (r.strategy, r.event_id, r.station, r.latency_seconds, r.execution_case)
        grouped.setdefault(key, []).append(r)
    out: list[EventReturn] = []
    for (strategy, event_id, station, latency, case), items in grouped.items():
        out.append(EventReturn(
            strategy=strategy,
            event_id=event_id,
            date=min(i.date for i in items),
            station=station,
            latency_seconds=latency,
            execution_case=case,
            pnl=sum(i.pnl for i in items),
            capital=sum(i.capital for i in items),
        ))
    return sorted(out, key=lambda r: (r.date, r.event_id))


def bootstrap_mean_ci(values: Sequence[float], *, samples: int = 4000, seed: int = 7,
                      alpha: float = 0.05) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], values[0]
    rng = Random(seed)
    n = len(values)
    means = []
    for _ in range(samples):
        means.append(mean(values[rng.randrange(n)] for _ in range(n)))
    means.sort()
    lo = means[max(0, int((alpha / 2) * samples))]
    hi = means[min(samples - 1, int((1 - alpha / 2) * samples) - 1)]
    return float(lo), float(hi)


def max_drawdown(rows: Sequence[EventReturn]) -> float:
    equity = peak = 0.0
    worst = 0.0
    for r in rows:
        equity += r.pnl
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return float(worst)


def validate_strategy(
    rows: Iterable[EventReturn],
    *,
    min_events: int = 30,
    min_stations: int = 2,
    require_positive_ci_for_keep: bool = True,
) -> ValidationResult:
    events = _event_aggregate(rows)
    if not events:
        return ValidationResult("unknown", 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, "unknown",
                                Verdict.INSUFFICIENT_DATA, "no resolved event-level returns")
    strategy = events[0].strategy
    latency = events[0].latency_seconds
    case = events[0].execution_case
    rois = [r.roi for r in events if r.capital > 0]
    ci_lo, ci_hi = bootstrap_mean_ci(rois)
    net = sum(r.pnl for r in events)
    avg = mean(rois) if rois else 0.0
    win_rate = sum(r.pnl > 0 for r in events) / len(events)
    stations = len({r.station for r in events if r.station})
    dd = max_drawdown(events)

    if len(events) < min_events or stations < min_stations:
        verdict = Verdict.INSUFFICIENT_DATA
        reason = f"need >= {min_events} independent events and >= {min_stations} stations"
    elif net <= 0 or avg <= 0:
        verdict = Verdict.KILL
        reason = "negative out-of-sample economics after realistic execution"
    elif ci_hi <= 0:
        verdict = Verdict.KILL
        reason = "95% event-bootstrap ROI interval is entirely non-positive"
    elif require_positive_ci_for_keep and ci_lo <= 0:
        verdict = Verdict.PROVISIONAL
        reason = "positive point estimate but 95% event-bootstrap ROI interval crosses zero"
    else:
        verdict = Verdict.KEEP
        reason = "positive OOS economics with positive event-bootstrap lower bound"

    return ValidationResult(
        strategy=strategy,
        events=len(events),
        stations=stations,
        net_pnl=float(net),
        mean_event_roi=float(avg),
        roi_ci_low=ci_lo,
        roi_ci_high=ci_hi,
        profitable_event_rate=float(win_rate),
        max_drawdown=dd,
        latency_seconds=latency,
        execution_case=case,
        verdict=verdict,
        reason=reason,
    )


def rank_results(results: Iterable[ValidationResult]) -> list[ValidationResult]:
    order = {Verdict.KEEP: 3, Verdict.PROVISIONAL: 2, Verdict.INSUFFICIENT_DATA: 1, Verdict.KILL: 0}
    return sorted(results, key=lambda r: (order[r.verdict], r.roi_ci_low, r.mean_event_roi, r.net_pnl), reverse=True)
