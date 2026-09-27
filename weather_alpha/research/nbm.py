from __future__ import annotations

from dataclasses import dataclass
from math import erf, sqrt
from typing import Any

from weather_alpha.engine.models import ExecutionQuality, MarketSnapshot, ModelEvaluation, Side, Signal
from .remaining_heating import contract_contains_value

SQRT2 = sqrt(2.0)


def _cdf(x: float, mean: float, sd: float) -> float:
    sd = max(0.75, float(sd))
    return 0.5 * (1.0 + erf((float(x) - float(mean)) / (sd * SQRT2)))


def exact_bucket_probability(metadata: dict[str, Any], mean_f: float, sd_f: float) -> float:
    strike = str(metadata.get('strike_type') or '').lower()
    floor = metadata.get('floor_strike')
    cap = metadata.get('cap_strike')
    if strike == 'between' and floor is not None and cap is not None:
        p = _cdf(float(cap) + 0.5, mean_f, sd_f) - _cdf(float(floor) - 0.5, mean_f, sd_f)
    elif strike == 'less' and cap is not None:
        p = _cdf(float(cap) - 0.5, mean_f, sd_f)
    elif strike == 'greater' and floor is not None:
        p = 1.0 - _cdf(float(floor) + 0.5, mean_f, sd_f)
    else:
        raise ValueError('market metadata must contain a supported strike_type and strike bounds')
    return max(0.0, min(1.0, float(p)))


@dataclass(frozen=True)
class NbmBucketStrategy:
    name: str = 'nbm_bucket_baseline_v1'
    min_edge: float = 0.05
    min_history: int = 0
    use_station_bias: bool = False

    def evaluate(self, market: MarketSnapshot, features: dict[str, object]) -> tuple[list[Signal], ModelEvaluation]:
        nbm = float(features['forecast.nbm_max_f'])
        sd = max(0.75, float(features['forecast.nbm_uncertainty_f']))
        history_n = int(features.get('forecast.nbm_residual_history_n') or 0)
        adjustment = 0.0
        if self.use_station_bias:
            if history_n < self.min_history:
                return [], ModelEvaluation(
                    market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
                    False, False, reason=f'INSUFFICIENT_PRIOR_HISTORY:{history_n}<{self.min_history}',
                    parameters={'min_history': self.min_history, 'use_station_bias': True}, features=dict(features),
                )
            adjustment = float(features['forecast.nbm_residual_median_f'])
        mean_f = nbm + adjustment
        p_yes = exact_bucket_probability(market.metadata, mean_f, sd)
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
                parameters={'mean_f': mean_f, 'sd_f': sd}, features=dict(features),
            )
        edge, side, ask, p_side = max(choices, key=lambda x: x[0])
        eligible = edge >= self.min_edge
        reason = (
            f"NBM={nbm:.1f}F adjustment={adjustment:+.2f}F mean={mean_f:.2f}F "
            f"side={side.value} p={p_side:.3f} ask={ask:.3f} edge={edge:.3f} history_n={history_n}"
        )
        evaluation = ModelEvaluation(
            market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
            eligible, eligible, reason=reason, side=side, model_probability=p_side,
            raw_edge=edge, executable_edge=edge, execution_quality=ExecutionQuality.TOUCH_EXECUTION,
            parameters={'min_edge': self.min_edge, 'min_history': self.min_history,
                        'use_station_bias': self.use_station_bias, 'mean_f': mean_f, 'sd_f': sd},
            features=dict(features),
        )
        if not eligible:
            return [], evaluation
        signal = Signal(
            timestamp=market.timestamp, strategy=self.name, venue=market.venue,
            contract_id=market.contract_id, weather_event_id=market.weather_event_id, side=side,
            model_probability=p_side, market_probability=ask, executable_price=ask,
            raw_edge=edge, executable_edge=edge, expected_value_per_contract=edge,
            execution_quality=ExecutionQuality.TOUCH_EXECUTION,
            metadata={'reasoning': reason, 'station': market.metadata.get('station'),
                      'settlement_date': market.metadata.get('settlement_date'),
                      'forecast_mean_f': mean_f, 'forecast_sd_f': sd,
                      'station_bias_adjustment_f': adjustment, 'history_n': history_n},
        )
        return [signal], evaluation


@dataclass(frozen=True)
class EmpiricalNbmResidualStrategy:
    """F02: exact bucket probabilities from prior-only station NBM residual samples."""

    name: str = 'nbm_empirical_residual_v1'
    min_edge: float = 0.05
    min_history: int = 30

    def evaluate(self, market: MarketSnapshot, features: dict[str, object]) -> tuple[list[Signal], ModelEvaluation]:
        nbm = float(features['forecast.nbm_max_f'])
        residuals = tuple(float(x) for x in (features['forecast.nbm_residual_samples_f'] or ()))
        history_n = int(features.get('forecast.nbm_residual_history_n') or len(residuals))
        if history_n < self.min_history or len(residuals) < self.min_history:
            return [], ModelEvaluation(
                market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
                False, False, reason=f'INSUFFICIENT_PRIOR_HISTORY:{history_n}<{self.min_history}',
                parameters={'min_history': self.min_history}, features=dict(features),
            )
        contract = {
            'strike_type': market.metadata.get('strike_type'),
            'floor_strike': market.metadata.get('floor_strike'),
            'cap_strike': market.metadata.get('cap_strike'),
        }
        final_samples = tuple(nbm + x for x in residuals)
        p_yes = sum(contract_contains_value(contract, x) for x in final_samples) / len(final_samples)
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
                parameters={'history_n': history_n}, features=dict(features),
            )
        edge, side, ask, p_side = max(choices, key=lambda x: x[0])
        eligible = edge >= self.min_edge
        reasoning = (
            f'empirical_nbm_residual history_n={history_n} NBM={nbm:.1f}F '
            f'side={side.value} p={p_side:.3f} ask={ask:.3f} edge={edge:.3f}'
        )
        evaluation = ModelEvaluation(
            market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
            eligible, eligible, reason=reasoning, side=side, model_probability=p_side,
            raw_edge=edge, executable_edge=edge, execution_quality=ExecutionQuality.TOUCH_EXECUTION,
            parameters={'history_n': history_n, 'min_history': self.min_history, 'min_edge': self.min_edge},
            features=dict(features),
        )
        if not eligible:
            return [], evaluation
        signal = Signal(
            market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
            side, p_side, ask, ask, edge, edge, edge, ExecutionQuality.TOUCH_EXECUTION,
            metadata={'reasoning': reasoning, 'station': market.metadata.get('station'),
                      'settlement_date': market.metadata.get('settlement_date'), 'history_n': history_n},
        )
        return [signal], evaluation
