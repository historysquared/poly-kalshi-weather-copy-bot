from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from .models import Side


class LiveTradingDisabled(RuntimeError):
    pass


class UnsafeOrder(RuntimeError):
    pass


class TradingClient(Protocol):
    def create_order(self, order: dict[str, Any]) -> Any: ...
    def cancel_order(self, order_id: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class LiveTradingSettings:
    enabled: bool = False
    max_contracts_per_order: int = 1
    allowed_venues: tuple[str, ...] = ("kalshi", "polymarket")


@dataclass(frozen=True)
class OrderIntent:
    venue: str
    contract_id: str
    side: Side
    contracts: int
    limit_price: float
    post_only: bool = False
    client_order_id: str = ""

    def normalized(self) -> "OrderIntent":
        return OrderIntent(
            venue=self.venue.lower(),
            contract_id=self.contract_id,
            side=self.side,
            contracts=int(self.contracts),
            limit_price=float(self.limit_price),
            post_only=bool(self.post_only),
            client_order_id=self.client_order_id or str(uuid4()),
        )


class GuardedExecutor:
    """Venue-neutral mutation gate adapted from kalshi-15m-lab.

    Live submission requires both an environment/config gate and an explicit CLI
    gate. The wrapper intentionally knows nothing about weather strategy logic.
    """

    def __init__(self, client: TradingClient, *, venue: str, settings: LiveTradingSettings, cli_live: bool = False):
        self.client = client
        self.venue = venue.lower()
        self.settings = settings
        self.cli_live = cli_live

    def _require_live(self) -> None:
        if not (self.settings.enabled and self.cli_live):
            raise LiveTradingDisabled("Live mutations require configured live trading AND an explicit --live gate")
        if self.venue not in {v.lower() for v in self.settings.allowed_venues}:
            raise LiveTradingDisabled(f"Venue {self.venue!r} is not enabled for live trading")

    def validate(self, intent: OrderIntent) -> OrderIntent:
        order = intent.normalized()
        if order.venue != self.venue:
            raise UnsafeOrder(f"Intent venue={order.venue!r} does not match executor venue={self.venue!r}")
        if not order.contract_id:
            raise UnsafeOrder("contract_id is required")
        if order.contracts <= 0 or order.contracts > self.settings.max_contracts_per_order:
            raise UnsafeOrder(f"contracts must be 1..{self.settings.max_contracts_per_order}")
        if not 0.0 < order.limit_price < 1.0:
            raise UnsafeOrder("limit_price must be strictly between 0 and 1")
        return order

    def submit(self, intent: OrderIntent) -> Any:
        self._require_live()
        order = self.validate(intent)
        payload = {
            "client_order_id": order.client_order_id,
            "contract_id": order.contract_id,
            "side": order.side.value.lower(),
            "contracts": order.contracts,
            "limit_price": order.limit_price,
            "post_only": order.post_only,
        }
        return self.client.create_order(payload)

    def cancel(self, order_id: str, **kwargs: Any) -> Any:
        self._require_live()
        if not order_id:
            raise UnsafeOrder("order_id is required")
        return self.client.cancel_order(order_id, **kwargs)
