from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

from .models import ExecutionQuality, ForwardMark, MarketSnapshot, Side, Signal, SimulatedFill
from .recorder import StateStore


class WeatherStrategy(Protocol):
    name: str

    def evaluate(self, market: MarketSnapshot, features: dict) -> tuple[list[Signal], object | None]: ...


@dataclass
class ShadowEngine:
    strategy: WeatherStrategy
    store: StateStore
    horizons: tuple[int, ...] = (5, 15, 30, 60, 120, 300, 600, 900)

    def observe(self, market: MarketSnapshot, features: dict) -> list[Signal]:
        signals, evaluation = self.strategy.evaluate(market, features)
        if evaluation is not None:
            self.store.evaluation(evaluation)  # type: ignore[arg-type]
        for signal in signals:
            self.store.signal(signal)
        return signals


@dataclass
class MultiShadowEngine:
    engines: list[ShadowEngine]

    def observe(self, market: MarketSnapshot, features: dict) -> list[Signal]:
        out: list[Signal] = []
        for engine in self.engines:
            try:
                out.extend(engine.observe(market, features))
            except Exception as exc:
                engine.store.strategy_error(market.timestamp, engine.strategy.name, market.venue, market.contract_id, repr(exc))
        return out


@dataclass
class PendingMark:
    signal_id: str
    venue: str
    contract_id: str
    side: Side
    horizon: int
    due: object


@dataclass
class LiveShadowSession:
    engine: ShadowEngine
    pending: list[PendingMark] = field(default_factory=list)

    def observe(self, market: MarketSnapshot, features: dict) -> list[Signal]:
        signals = self.engine.observe(market, features)
        for signal in signals:
            for horizon in self.engine.horizons:
                self.pending.append(
                    PendingMark(signal.signal_id, signal.venue, signal.contract_id, signal.side, horizon,
                                signal.timestamp + timedelta(seconds=horizon))
                )
        return signals

    def record_due(self, market: MarketSnapshot) -> int:
        now = market.timestamp
        due = [x for x in self.pending if x.due <= now]
        self.pending = [x for x in self.pending if x.due > now]
        for item in due:
            matches = item.venue == market.venue and item.contract_id == market.contract_id
            executable = market.executable_bid(item.side) if matches else None
            self.engine.store.mark(
                ForwardMark(
                    signal_id=item.signal_id,
                    horizon_seconds=item.horizon,
                    mark_timestamp=now,
                    venue=item.venue,
                    contract_id=item.contract_id,
                    yes_bid=market.yes_bid if matches else None,
                    yes_ask=market.yes_ask if matches else None,
                    no_bid=market.no_bid if matches else None,
                    no_ask=market.no_ask if matches else None,
                    executable_value=executable,
                )
            )
        return len(due)


@dataclass(frozen=True)
class PaperFillModel:
    latency_seconds: float = 0.0
    slippage: float = 0.0
    max_contracts: int = 1

    def fill(self, signal: Signal, market: MarketSnapshot, *, fee: float = 0.0) -> SimulatedFill | None:
        if signal.venue != market.venue or signal.contract_id != market.contract_id:
            return None
        ask = market.executable_ask(signal.side)
        if ask is None:
            return None
        price = min(0.9999, max(0.0001, ask + self.slippage))
        return SimulatedFill(
            signal_id=signal.signal_id,
            venue=signal.venue,
            contract_id=signal.contract_id,
            side=signal.side,
            executable_price=price,
            contracts=max(1, min(self.max_contracts, 1)),
            fee=float(fee),
            slippage=float(self.slippage),
            fill_model="TOUCH_PLUS_SLIPPAGE",
            timestamp=market.timestamp,
            execution_quality=ExecutionQuality.TOUCH_EXECUTION,
        )
