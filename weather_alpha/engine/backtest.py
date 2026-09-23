from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from statistics import mean

from .models import ExecutionQuality, MarketSnapshot, Side, Signal, SimulatedFill


@dataclass(frozen=True)
class FeeQuote:
    amount: Decimal
    treatment: str


@dataclass
class TakerExecution:
    """Leakage-safe executable-touch fill model with explicit latency/stress."""

    latency_seconds: float = 0.0
    slippage: float = 0.0
    stress_ticks: int = 0
    tick_size: float = 0.01

    def _eligible_snapshot(self, signal: Signal, observations: list[MarketSnapshot]) -> MarketSnapshot | None:
        target = signal.timestamp + timedelta(seconds=self.latency_seconds)
        eligible = [
            m for m in observations
            if m.venue == signal.venue and m.contract_id == signal.contract_id and m.timestamp >= target
        ]
        return min(eligible, key=lambda m: m.timestamp) if eligible else None

    def fill(
        self,
        signal: Signal,
        observations: list[MarketSnapshot],
        *,
        contracts: int = 1,
        fee: float = 0.0,
        quality: ExecutionQuality = ExecutionQuality.TOUCH_EXECUTION,
    ) -> SimulatedFill | None:
        market = self._eligible_snapshot(signal, observations)
        if market is None:
            return None
        touch = market.executable_ask(signal.side)
        if touch is None:
            return None
        size = market.yes_ask_size if signal.side == Side.YES else market.no_ask_size
        quantity = min(int(contracts), int(size)) if size is not None else int(contracts)
        if quantity <= 0:
            return None
        adverse = float(self.slippage) + int(self.stress_ticks) * float(self.tick_size)
        price = min(0.9999, max(0.0001, float(touch) + adverse))
        return SimulatedFill(
            signal_id=signal.signal_id,
            venue=signal.venue,
            contract_id=signal.contract_id,
            side=signal.side,
            executable_price=price,
            contracts=quantity,
            fee=float(fee),
            slippage=adverse,
            fill_model="TAKER_TOUCH",
            timestamp=market.timestamp,
            execution_quality=quality,
            available_size=size,
        )


@dataclass(frozen=True)
class SettledTrade:
    signal: Signal
    fill: SimulatedFill
    won: bool

    @property
    def pnl(self) -> float:
        return self.fill.contracts * ((1.0 if self.won else 0.0) - self.fill.executable_price) - self.fill.fee

    @property
    def roi(self) -> float:
        capital = self.fill.contracts * self.fill.executable_price + self.fill.fee
        return self.pnl / capital if capital > 0 else 0.0


def summarize_settled(trades: list[SettledTrade]) -> dict[str, float | int | None]:
    if not trades:
        return {"trades": 0, "wins": 0, "win_rate": None, "net_pnl": 0.0, "mean_roi": None}
    wins = sum(int(x.won) for x in trades)
    return {
        "trades": len(trades),
        "wins": wins,
        "win_rate": wins / len(trades),
        "net_pnl": sum(x.pnl for x in trades),
        "mean_roi": mean(x.roi for x in trades),
    }
