from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
from typing import Callable, Iterable

from weather_alpha.engine.backtest import TakerExecution
from weather_alpha.engine.models import ForwardMark, MarketSnapshot, Settlement, Signal, SimulatedFill
from weather_alpha.engine.recorder import StateStore

from .registry import SignalRegistry
from .runner import ResearchRunner, ResearchStrategy
from .scorecard import EconomicScorecard, build_scorecards


FeeFunction = Callable[[float, int], float]


def kalshi_taker_fee_ceil_cent(price: float, contracts: int, coefficient: float = 0.07) -> float:
    if contracts <= 0:
        return 0.0
    p = Decimal(str(min(1.0, max(0.0, float(price)))))
    raw = Decimal(str(coefficient)) * Decimal(int(contracts)) * p * (Decimal('1') - p)
    cents = (raw * Decimal('100')).to_integral_value(rounding=ROUND_CEILING)
    return float(cents / Decimal('100'))


@dataclass(frozen=True)
class HistoricalResearchCase:
    market: MarketSnapshot
    features: dict[str, object]
    execution_snapshots: tuple[MarketSnapshot, ...]
    settlement: Settlement


@dataclass(frozen=True)
class HistoricalReplaySummary:
    cases: int
    signals: int
    fills: int
    reconciled_fills: int
    scorecards: tuple[EconomicScorecard, ...]


@dataclass
class HistoricalResearchReplay:
    registry: SignalRegistry
    store: StateStore
    execution: TakerExecution
    fee_function: FeeFunction
    markout_horizons: tuple[int, ...] = (60, 300, 900)

    def _mark_signal(self, signal: Signal, snapshots: tuple[MarketSnapshot, ...]) -> None:
        ordered = sorted(
            (m for m in snapshots if m.venue == signal.venue and m.contract_id == signal.contract_id),
            key=lambda m: m.timestamp,
        )
        for horizon in self.markout_horizons:
            target = signal.timestamp + timedelta(seconds=horizon)
            snap = next((m for m in ordered if m.timestamp >= target), None)
            if snap is None:
                continue
            self.store.mark(ForwardMark(
                signal_id=signal.signal_id, horizon_seconds=horizon, mark_timestamp=snap.timestamp,
                venue=signal.venue, contract_id=signal.contract_id,
                yes_bid=snap.yes_bid, yes_ask=snap.yes_ask, no_bid=snap.no_bid, no_ask=snap.no_ask,
                executable_value=snap.executable_bid(signal.side),
            ))

    def run(self, strategy: ResearchStrategy, cases: Iterable[HistoricalResearchCase], *, contracts: int = 1) -> HistoricalReplaySummary:
        runner = ResearchRunner(self.registry, self.store)
        case_rows = sorted(cases, key=lambda row: row.market.timestamp)
        signal_count = fill_count = reconciled = 0
        for case in case_rows:
            signals = runner.evaluate(strategy, case.market, dict(case.features))
            signal_count += len(signals)
            for signal in signals:
                provisional = self.execution.fill(signal, list(case.execution_snapshots), contracts=contracts, fee=0.0)
                if provisional is None:
                    continue
                fee = self.fee_function(provisional.executable_price, provisional.contracts)
                fill: SimulatedFill = replace(provisional, fee=fee, fee_treatment='EXPLICIT_REPLAY_FEE')
                self.store.fill(fill)
                fill_count += 1
                self._mark_signal(signal, case.execution_snapshots)
            self.store.settlement(case.settlement)
            reconciled += self.store.reconcile_paper(case.settlement.venue, case.settlement.contract_id)
        return HistoricalReplaySummary(
            cases=len(case_rows), signals=signal_count, fills=fill_count, reconciled_fills=reconciled,
            scorecards=tuple(build_scorecards(self.store.db.execute('PRAGMA database_list').fetchone()[2], strategy.name)),
        )
