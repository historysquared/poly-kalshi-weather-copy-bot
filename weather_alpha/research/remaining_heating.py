from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import sqrt
from statistics import median
from typing import Any, Iterable

from weather_alpha.engine.models import ExecutionQuality, MarketSnapshot, ModelEvaluation, Side, Signal


@dataclass(frozen=True)
class RemainingHeatingPrediction:
    sample_count: int
    median_residual_f: float | None
    terminal_probability: float | None
    residual_samples_f: tuple[float, ...]

    def final_high_samples(self, high_so_far_f: float) -> tuple[float, ...]:
        return tuple(float(high_so_far_f) + x for x in self.residual_samples_f)


class EmpiricalRemainingHeatingModel:
    """Prior-only station-specific empirical remaining-heating model.

    Historical neighbors are restricted to earlier settlement dates at the same
    station and nearby Local Standard Time snapshot hours. Distance is computed
    from causal surface state only. The target is official CLI high minus public
    ASOS high-so-far, so the empirical residual naturally includes both remaining
    heating and the observed settlement-basis discrepancy.
    """

    def __init__(self, history: Iterable[dict[str, Any]], *, k: int = 80, min_samples: int = 30,
                 max_hour_delta: int = 2) -> None:
        self.k = int(k)
        self.min_samples = int(min_samples)
        self.max_hour_delta = int(max_hour_delta)
        if self.k <= 0 or self.min_samples <= 0:
            raise ValueError('k and min_samples must be positive')
        self.rows = [self._normalize(x) for x in history]

    @staticmethod
    def _normalize(row: dict[str, Any]) -> dict[str, Any]:
        return {
            **row,
            '_date': date.fromisoformat(str(row['settlement_date'])[:10]),
            'station': str(row['station']).upper(),
            'snapshot_hour_lst': int(row['snapshot_hour_lst']),
            'high_so_far_f': float(row['high_so_far_f']),
            'official_cli_high_f': None if row.get('official_cli_high_f') is None else float(row['official_cli_high_f']),
        }

    @staticmethod
    def _value(row: dict[str, Any], key: str) -> float | None:
        value = row.get(key)
        if value is None:
            return None
        try:
            if value != value:  # nan
                return None
        except Exception:
            pass
        return float(value)

    def _distance(self, current: dict[str, Any], hist: dict[str, Any]) -> float:
        # Fixed physical scales, intentionally not tuned on the test interval.
        specs = (
            ('snapshot_hour_lst', 1.5), ('high_so_far_f', 10.0),
            ('minutes_since_high', 60.0), ('drop_from_high_f', 1.0),
            ('slope_15m_f_per_min', 0.04), ('dewpoint_depression_f', 8.0),
        )
        total = 0.0
        used = 0
        for key, scale in specs:
            a = self._value(current, key)
            b = self._value(hist, key)
            if a is None or b is None:
                continue
            total += ((a - b) / scale) ** 2
            used += 1
        return sqrt(total / used) if used else float('inf')

    def predict(self, current: dict[str, Any]) -> RemainingHeatingPrediction:
        row = self._normalize(current)
        candidates: list[tuple[float, float]] = []
        for hist in self.rows:
            if hist['station'] != row['station'] or hist['_date'] >= row['_date']:
                continue
            if abs(hist['snapshot_hour_lst'] - row['snapshot_hour_lst']) > self.max_hour_delta:
                continue
            if hist['official_cli_high_f'] is None:
                continue
            residual = hist['official_cli_high_f'] - hist['high_so_far_f']
            candidates.append((self._distance(row, hist), float(residual)))
        candidates.sort(key=lambda x: x[0])
        residuals = tuple(x[1] for x in candidates[: self.k])
        if len(residuals) < self.min_samples:
            return RemainingHeatingPrediction(len(residuals), None, None, residuals)
        return RemainingHeatingPrediction(
            sample_count=len(residuals), median_residual_f=float(median(residuals)),
            terminal_probability=sum(x <= 0.0 for x in residuals) / len(residuals),
            residual_samples_f=residuals,
        )


def contract_contains_value(contract: dict[str, Any], value_f: float) -> bool:
    strike = str(contract.get('strike_type') or '').lower()
    floor = contract.get('floor_strike')
    cap = contract.get('cap_strike')
    if strike == 'between' and floor is not None and cap is not None:
        return float(floor) <= value_f <= float(cap)
    if strike == 'less' and cap is not None:
        return value_f < float(cap)
    if strike == 'greater' and floor is not None:
        return value_f > float(floor)
    return False


def empirical_bucket_probabilities(contracts: Iterable[dict[str, Any]], final_high_samples: Iterable[float]) -> dict[str, float]:
    samples = tuple(float(x) for x in final_high_samples)
    if not samples:
        return {}
    out: dict[str, float] = {}
    for contract in contracts:
        ticker = str(contract.get('ticker') or '')
        if not ticker:
            continue
        out[ticker] = sum(contract_contains_value(contract, x) for x in samples) / len(samples)
    return out


@dataclass(frozen=True)
class EmpiricalRemainingHeatingStrategy:
    """Turn a prior-only empirical final-high sample distribution into an exact bucket signal."""

    name: str = 'terminal_high_empirical_v2'
    min_edge: float = 0.05
    min_samples: int = 30

    def evaluate(self, market: MarketSnapshot, features: dict[str, object]) -> tuple[list[Signal], ModelEvaluation]:
        source_family = str(features.get('market.settlement_source_family') or '').upper()
        if source_family != 'NWS_CLI':
            return [], ModelEvaluation(
                market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
                False, False, reason=f'INCOMPATIBLE_SETTLEMENT_SOURCE:{source_family or "MISSING"}; validated=NWS_CLI',
                features=dict(features), data_quality=['SETTLEMENT_SOURCE_MISMATCH'],
            )
        samples = tuple(float(x) for x in (features['forecast.remaining_heating_final_high_samples_f'] or ()))
        history_n = int(features.get('forecast.remaining_heating_history_n') or len(samples))
        if history_n < self.min_samples or len(samples) < self.min_samples:
            return [], ModelEvaluation(
                market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
                False, False, reason=f'INSUFFICIENT_PRIOR_HISTORY:{history_n}<{self.min_samples}',
                parameters={'min_samples': self.min_samples}, features=dict(features),
            )
        contract = {
            'ticker': market.contract_id,
            'strike_type': market.metadata.get('strike_type'),
            'floor_strike': market.metadata.get('floor_strike'),
            'cap_strike': market.metadata.get('cap_strike'),
        }
        p_yes = sum(contract_contains_value(contract, x) for x in samples) / len(samples)
        choices: list[tuple[float, Side, float, float]] = []
        if market.yes_ask is not None:
            choices.append((p_yes - market.yes_ask, Side.YES, market.yes_ask, p_yes))
        if market.no_ask is not None:
            p_no = 1.0 - p_yes
            choices.append((p_no - market.no_ask, Side.NO, market.no_ask, p_no))
        if not choices:
            return [], ModelEvaluation(
                market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
                False, False, reason='NO_EXECUTABLE_ASK', model_probability=p_yes,
                parameters={'history_n': history_n, 'min_samples': self.min_samples}, features=dict(features),
            )
        edge, side, ask, p_side = max(choices, key=lambda x: x[0])
        eligible = edge >= self.min_edge
        terminal_probability = features.get('forecast.remaining_heating_terminal_probability')
        reasoning = (
            f'empirical_remaining_heating history_n={history_n} side={side.value} '
            f'p={p_side:.3f} ask={ask:.3f} edge={edge:.3f} terminal_p={terminal_probability}'
        )
        evaluation = ModelEvaluation(
            market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
            eligible, eligible, reason=reasoning, side=side, model_probability=p_side,
            raw_edge=edge, executable_edge=edge, execution_quality=ExecutionQuality.TOUCH_EXECUTION,
            parameters={'history_n': history_n, 'min_samples': self.min_samples, 'min_edge': self.min_edge},
            features=dict(features),
        )
        if not eligible:
            return [], evaluation
        signal = Signal(
            market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
            side, p_side, ask, ask, edge, edge, edge, ExecutionQuality.TOUCH_EXECUTION,
            metadata={'reasoning': reasoning, 'station': market.metadata.get('station'),
                      'settlement_date': market.metadata.get('settlement_date'),
                      'history_n': history_n, 'terminal_probability': terminal_probability},
        )
        return [signal], evaluation
