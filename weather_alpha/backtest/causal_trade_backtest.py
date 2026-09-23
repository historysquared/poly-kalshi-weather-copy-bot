from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from typing import Any, Iterable


@dataclass(frozen=True)
class ExecutableFill:
    vwap: Decimal | None
    filled: Decimal
    complete: bool


def D(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text else Decimal(text)


def parse_dt(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def ask_levels(snapshot: dict[str, Any], side: str) -> list[tuple[Decimal, Decimal]]:
    side = side.upper()
    if side not in {"YES", "NO"}:
        raise ValueError("side must be YES or NO")
    complement_key = "no_bids" if side == "YES" else "yes_bids"
    out: list[tuple[Decimal, Decimal]] = []
    for row in snapshot.get(complement_key) or []:
        p = D(row.get("price") if isinstance(row, dict) else row[0])
        q = D(row.get("size") if isinstance(row, dict) else row[1])
        if p is None or q is None or q <= 0:
            continue
        out.append((Decimal("1") - p, q))
    return sorted(out, key=lambda x: x[0])


def executable_buy(snapshot: dict[str, Any], side: str, contracts: Decimal) -> ExecutableFill:
    remaining = contracts
    cost = Decimal("0")
    filled = Decimal("0")
    for price, size in ask_levels(snapshot, side):
        if remaining <= 0:
            break
        take = min(remaining, size)
        cost += take * price
        filled += take
        remaining -= take
    return ExecutableFill(None if filled == 0 else cost / filled, filled, remaining <= 0)


def kalshi_fee(*, coefficient: Decimal, contracts: Decimal, price: Decimal) -> Decimal:
    """Official event-contract formula with round-up to next cent.

    Caller is responsible for supplying the verified coefficient for the series.
    General published coefficients have historically been 0.07 taker and 0.0175 maker,
    but this helper does not assume which applies.
    """
    raw = coefficient * contracts * price * (Decimal("1") - price)
    return (raw * Decimal("100")).to_integral_value(rounding=ROUND_CEILING) / Decimal("100")


def snapshot_at_or_after(rows: Iterable[dict[str, Any]], signal_time: datetime, latency_seconds: int) -> dict[str, Any] | None:
    target = signal_time + timedelta(seconds=int(latency_seconds))
    eligible = [r for r in rows if parse_dt(r["book_time"]) >= target]
    return min(eligible, key=lambda r: parse_dt(r["book_time"])) if eligible else None


def terminal_pnl(*, side: str, price: Decimal, contracts: Decimal, official_yes_winner: bool, fee: Decimal) -> Decimal:
    wins = official_yes_winner if side.upper() == "YES" else (not official_yes_winner)
    payout = contracts if wins else Decimal("0")
    return payout - contracts * price - fee
