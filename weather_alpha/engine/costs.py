from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeSchedule:
    venue: str
    taker_rate: float = 0.0
    maker_rate: float = 0.0
    source: str = "UNVERIFIED"
    verified: bool = False

    def fee(self, *, price: float, contracts: int, maker: bool) -> float:
        rate = self.maker_rate if maker else self.taker_rate
        if contracts <= 0:
            return 0.0
        p = min(1.0, max(0.0, float(price)))
        return float(contracts) * rate * p * (1.0 - p)


class FeeScheduleRouter:
    """Fail-closed venue/series fee router.

    Unknown fees must not silently become zero in production. Research callers may
    explicitly request allow_unverified=True and should label those results.
    """

    def __init__(self):
        self._by_key: dict[tuple[str, str | None], FeeSchedule] = {}

    def register(self, schedule: FeeSchedule, *, series: str | None = None) -> None:
        self._by_key[(schedule.venue.lower(), series)] = schedule

    def resolve(self, venue: str, *, series: str | None = None, allow_unverified: bool = False) -> FeeSchedule:
        key = (venue.lower(), series)
        schedule = self._by_key.get(key) or self._by_key.get((venue.lower(), None))
        if schedule is None:
            raise LookupError(f"No fee schedule configured for venue={venue!r} series={series!r}")
        if not schedule.verified and not allow_unverified:
            raise RuntimeError(f"Fee schedule is not verified: {schedule.source}")
        return schedule


def expected_value(*, model_probability: float, executable_price: float, fee_per_contract: float = 0.0,
                   slippage_per_contract: float = 0.0) -> float:
    return float(model_probability) - float(executable_price) - float(fee_per_contract) - float(slippage_per_contract)
