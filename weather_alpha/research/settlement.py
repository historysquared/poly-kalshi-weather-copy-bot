from __future__ import annotations

from dataclasses import dataclass

from weather_alpha.engine.models import ExecutionQuality, MarketSnapshot, ModelEvaluation, Side, Signal


def yes_eliminated_by_high_so_far(metadata: dict, high_so_far_f: float) -> tuple[bool, str]:
    strike = str(metadata.get('strike_type') or '').lower()
    floor = metadata.get('floor_strike')
    cap = metadata.get('cap_strike')
    high = float(high_so_far_f)
    if strike == 'between' and cap is not None:
        return high > float(cap), f'high_so_far={high:.2f} > bucket_cap={float(cap):.2f}'
    if strike == 'less' and cap is not None:
        return high >= float(cap), f'high_so_far={high:.2f} >= less_threshold={float(cap):.2f}'
    if strike == 'greater' and floor is not None:
        return False, 'greater-than daily-high contract cannot be eliminated by an already-observed high'
    return False, 'unsupported_or_incomplete_strike_metadata'


@dataclass(frozen=True)
class EliminatedBucketNoStrategy:
    name: str = 'settlement_eliminated_bucket_no_v1'
    min_edge: float = 0.02
    max_no_ask: float = 0.98

    def evaluate(self, market: MarketSnapshot, features: dict[str, object]) -> tuple[list[Signal], ModelEvaluation]:
        verified = bool(features['market.settlement_semantics_verified'])
        high = float(features['settlement.high_so_far_f'])
        if not verified:
            return [], ModelEvaluation(
                market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
                False, False, reason='UNVERIFIED_SETTLEMENT_SEMANTICS', features=dict(features),
            )
        eliminated, physical_reason = yes_eliminated_by_high_so_far(market.metadata, high)
        if not eliminated:
            return [], ModelEvaluation(
                market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
                False, False, reason='NOT_ELIMINATED:' + physical_reason, features=dict(features),
            )
        if market.no_ask is None:
            return [], ModelEvaluation(
                market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
                False, False, reason='NO_EXECUTABLE_NO_ASK', side=Side.NO, model_probability=1.0,
                features=dict(features),
            )
        edge = 1.0 - float(market.no_ask)
        eligible = edge >= self.min_edge and float(market.no_ask) <= self.max_no_ask
        reason = f'{physical_reason}; NO ask={market.no_ask:.3f}; gross_edge={edge:.3f}'
        evaluation = ModelEvaluation(
            market.timestamp, self.name, market.venue, market.contract_id, market.weather_event_id,
            eligible, eligible, reason=reason, side=Side.NO, model_probability=1.0,
            raw_edge=edge, executable_edge=edge, execution_quality=ExecutionQuality.TOUCH_EXECUTION,
            parameters={'min_edge': self.min_edge, 'max_no_ask': self.max_no_ask}, features=dict(features),
        )
        if not eligible:
            return [], evaluation
        signal = Signal(
            timestamp=market.timestamp, strategy=self.name, venue=market.venue,
            contract_id=market.contract_id, weather_event_id=market.weather_event_id, side=Side.NO,
            model_probability=1.0, market_probability=float(market.no_ask),
            executable_price=float(market.no_ask), raw_edge=edge, executable_edge=edge,
            expected_value_per_contract=edge, execution_quality=ExecutionQuality.TOUCH_EXECUTION,
            metadata={'reasoning': reason, 'station': market.metadata.get('station'),
                      'settlement_date': market.metadata.get('settlement_date'),
                      'high_so_far_f': high, 'physical_elimination': True},
        )
        return [signal], evaluation
