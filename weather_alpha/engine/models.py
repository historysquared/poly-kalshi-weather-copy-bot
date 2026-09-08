from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Side(StrEnum):
    YES = "YES"
    NO = "NO"


class ExecutionQuality(StrEnum):
    OBSERVATION_ONLY = "OBSERVATION_ONLY"
    TRADE_PRICE_PROXY = "TRADE_PRICE_PROXY"
    TOUCH_EXECUTION = "TOUCH_EXECUTION"
    L2_EXECUTION = "L2_EXECUTION"


@dataclass(slots=True)
class MarketSnapshot:
    timestamp: datetime
    venue: str
    contract_id: str
    weather_event_id: str
    yes_bid: float | None = None
    yes_ask: float | None = None
    no_bid: float | None = None
    no_ask: float | None = None
    yes_bid_size: float | None = None
    yes_ask_size: float | None = None
    no_bid_size: float | None = None
    no_ask_size: float | None = None
    depth: dict[str, Any] | None = None
    close_time: datetime | None = None
    status: str | None = None
    source_exchange_timestamp: datetime | None = None
    source_receive_timestamp: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def spread(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid

    def executable_ask(self, side: Side) -> float | None:
        return self.yes_ask if side == Side.YES else self.no_ask

    def executable_bid(self, side: Side) -> float | None:
        return self.yes_bid if side == Side.YES else self.no_bid


@dataclass(slots=True)
class ModelEvaluation:
    """One model calculation, including abstentions; not necessarily a trade."""

    timestamp: datetime
    strategy: str
    venue: str
    contract_id: str
    weather_event_id: str
    eligible: bool
    emitted: bool = False
    reason: str | None = None
    side: Side | None = None
    model_probability: float | None = None
    raw_edge: float | None = None
    executable_edge: float | None = None
    execution_quality: ExecutionQuality = ExecutionQuality.OBSERVATION_ONLY
    data_quality: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    features: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Signal:
    timestamp: datetime
    strategy: str
    venue: str
    contract_id: str
    weather_event_id: str
    side: Side
    model_probability: float
    market_probability: float
    executable_price: float
    raw_edge: float
    executable_edge: float
    expected_value_per_contract: float
    execution_quality: ExecutionQuality
    metadata: dict[str, Any] = field(default_factory=dict)
    signal_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(slots=True)
class SimulatedFill:
    signal_id: str
    venue: str
    contract_id: str
    side: Side
    executable_price: float
    contracts: int
    fee: float
    slippage: float
    fill_model: str
    timestamp: datetime
    execution_quality: ExecutionQuality = ExecutionQuality.TOUCH_EXECUTION
    fee_treatment: str = "ESTIMATED"
    available_size: float | None = None


@dataclass(slots=True)
class Settlement:
    venue: str
    contract_id: str
    weather_event_id: str
    official_result: Side | str
    resolved_timestamp: datetime
    official_settlement_value: float | None = None
    settlement_source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ForwardMark:
    signal_id: str
    horizon_seconds: int
    mark_timestamp: datetime
    venue: str
    contract_id: str
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    executable_value: float | None


def jsonable(obj: Any) -> Any:
    def clean(value: Any, *, root: bool = False) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, StrEnum):
            return value.value
        if isinstance(value, dict):
            if not value and not root:
                return None
            return {str(k): clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        return value

    value = asdict(obj) if is_dataclass(obj) else obj
    return clean(value, root=True)
