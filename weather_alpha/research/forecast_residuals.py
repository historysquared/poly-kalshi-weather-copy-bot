from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from statistics import mean, median
from typing import Any, Iterable


@dataclass(frozen=True)
class NbmResidualCalibration:
    location_key: str
    station: str
    date: str
    nbm_max_f: float
    actual_high_f: float
    history_n: int
    lookback_days: int
    min_history: int
    bias_mean_f: float | None
    bias_median_f: float | None
    historical_mae_f: float | None
    adjusted_max_f: float | None
    raw_error_f: float
    adjusted_error_f: float | None
    eligible: bool
    residual_samples_f: tuple[float, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_prior_only_residual_calibration(
    rows: Iterable[dict[str, Any]], *, lookback_days: int = 90, min_history: int = 30
) -> list[NbmResidualCalibration]:
    if lookback_days <= 0:
        raise ValueError('lookback_days must be positive')
    if min_history <= 0:
        raise ValueError('min_history must be positive')

    clean: list[dict[str, Any]] = []
    for row in rows:
        if row.get('nbm_max_f') is None or row.get('actual_high_f') is None:
            continue
        clean.append({
            **row,
            '_date': date.fromisoformat(str(row['date'])[:10]),
            'nbm_max_f': float(row['nbm_max_f']),
            'actual_high_f': float(row['actual_high_f']),
        })
    clean.sort(key=lambda r: (str(r.get('location_key') or ''), str(r.get('station') or ''), r['_date']))

    history: dict[tuple[str, str], list[tuple[date, float]]] = {}
    out: list[NbmResidualCalibration] = []
    for row in clean:
        key = (str(row.get('location_key') or ''), str(row.get('station') or ''))
        day = row['_date']
        cutoff = day - timedelta(days=lookback_days)
        prior = [(d, e) for d, e in history.get(key, []) if cutoff <= d < day]
        errors = [e for _d, e in prior]
        n = len(errors)
        eligible = n >= min_history
        bias_mean = mean(errors) if errors else None
        bias_median = median(errors) if errors else None
        hist_mae = mean(abs(x) for x in errors) if errors else None
        nbm = row['nbm_max_f']
        actual = row['actual_high_f']
        adjusted = nbm + float(bias_median) if eligible and bias_median is not None else None
        out.append(NbmResidualCalibration(
            location_key=key[0], station=key[1], date=day.isoformat(), nbm_max_f=nbm,
            actual_high_f=actual, history_n=n, lookback_days=lookback_days, min_history=min_history,
            bias_mean_f=None if bias_mean is None else float(bias_mean),
            bias_median_f=None if bias_median is None else float(bias_median),
            historical_mae_f=None if hist_mae is None else float(hist_mae),
            adjusted_max_f=adjusted, raw_error_f=actual - nbm,
            adjusted_error_f=None if adjusted is None else actual - adjusted, eligible=eligible,
            residual_samples_f=tuple(float(x) for x in errors),
        ))
        history.setdefault(key, []).append((day, actual - nbm))
    return out
