from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from weather_alpha.engine.models import MarketSnapshot, ModelEvaluation, Signal
from weather_alpha.engine.recorder import StateStore

from .registry import SignalRegistry


class ResearchStrategy(Protocol):
    name: str

    def evaluate(self, market: MarketSnapshot, features: dict[str, object]) -> tuple[list[Signal], ModelEvaluation | None]: ...


@dataclass
class ResearchRunner:
    registry: SignalRegistry
    store: StateStore

    def evaluate(self, strategy: ResearchStrategy, market: MarketSnapshot, features: dict[str, object]) -> list[Signal]:
        definition = self.registry.signal(strategy.name)
        if not definition.enabled:
            return []
        if market.venue.lower() not in {venue.lower() for venue in definition.venues}:
            raise ValueError(f"signal {strategy.name} is not registered for venue {market.venue}")

        missing = self.registry.validate_feature_payload(strategy.name, features)
        if missing:
            evaluation = ModelEvaluation(
                timestamp=market.timestamp,
                strategy=strategy.name,
                venue=market.venue,
                contract_id=market.contract_id,
                weather_event_id=market.weather_event_id,
                eligible=False,
                emitted=False,
                reason="MISSING_REGISTERED_FEATURES:" + ",".join(missing),
                features=dict(features),
                data_quality=["MISSING_FEATURE"],
            )
            self.store.evaluation(evaluation)
            return []

        try:
            signals, evaluation = strategy.evaluate(market, features)
        except Exception as exc:
            self.store.strategy_error(market.timestamp, strategy.name, market.venue, market.contract_id, repr(exc))
            raise

        if evaluation is not None:
            if evaluation.strategy != strategy.name:
                raise ValueError("evaluation.strategy must match registered signal id")
            self.store.evaluation(evaluation)
        for signal in signals:
            if signal.strategy != strategy.name:
                raise ValueError("signal.strategy must match registered signal id")
            self.store.signal(signal)
        return signals
