from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"


def _d(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def load_private_key(path: str | Path):
    with Path(path).expanduser().open("rb") as fh:
        return serialization.load_pem_private_key(fh.read(), password=None)


def sign_pss_text(private_key, text: str) -> str:
    signature = private_key.sign(
        text.encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def websocket_auth_headers(
    *,
    key_id: str | None = None,
    private_key_path: str | Path | None = None,
    timestamp_ms: int | None = None,
) -> dict[str, str]:
    key_id = key_id or os.getenv("KALSHI_API_KEY_ID") or os.getenv("KALSHI_ACCESS_KEY")
    private_key_path = private_key_path or os.getenv("KALSHI_PRIVATE_KEY_PATH")
    if not key_id or not private_key_path:
        raise RuntimeError("KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH must be set")
    ts = str(timestamp_ms if timestamp_ms is not None else int(time.time() * 1000))
    private_key = load_private_key(private_key_path)
    signature = sign_pss_text(private_key, ts + "GET" + WS_PATH)
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": ts,
    }


@dataclass(slots=True)
class KalshiBook:
    ticker: str
    yes_bids: dict[Decimal, Decimal] = field(default_factory=dict)
    no_bids: dict[Decimal, Decimal] = field(default_factory=dict)
    sequence: int | None = None
    last_exchange_ts: str | None = None

    def apply_snapshot(self, msg: dict[str, Any], sequence: int | None = None) -> None:
        self.yes_bids = self._levels(msg.get("yes_dollars_fp") or msg.get("yes") or [])
        self.no_bids = self._levels(msg.get("no_dollars_fp") or msg.get("no") or [])
        self.sequence = sequence
        self.last_exchange_ts = str(msg.get("ts") or msg.get("ts_ms") or "") or None

    def apply_delta(self, msg: dict[str, Any], sequence: int | None = None) -> None:
        side = str(msg.get("side") or "").lower()
        if side not in {"yes", "no"}:
            return
        price_raw = msg.get("price_dollars")
        if price_raw is None:
            price_raw = msg.get("price")
            if price_raw is not None:
                price_raw = _d(price_raw) / Decimal("100")
        delta_raw = msg.get("delta_fp")
        if delta_raw is None:
            delta_raw = msg.get("delta")
        if price_raw is None or delta_raw is None:
            return
        price = _d(price_raw)
        delta = _d(delta_raw)
        book = self.yes_bids if side == "yes" else self.no_bids
        new_size = book.get(price, Decimal("0")) + delta
        if new_size <= 0:
            book.pop(price, None)
        else:
            book[price] = new_size
        self.sequence = sequence
        self.last_exchange_ts = str(msg.get("ts") or msg.get("ts_ms") or "") or self.last_exchange_ts

    @staticmethod
    def _levels(values: Any) -> dict[Decimal, Decimal]:
        out: dict[Decimal, Decimal] = {}
        for item in values or []:
            try:
                price, size = _d(item[0]), _d(item[1])
            except Exception:
                continue
            if size > 0:
                out[price] = size
        return out

    @property
    def yes_best_bid(self) -> Decimal | None:
        return max(self.yes_bids, default=None)

    @property
    def no_best_bid(self) -> Decimal | None:
        return max(self.no_bids, default=None)

    @property
    def yes_best_ask(self) -> Decimal | None:
        bid = self.no_best_bid
        return None if bid is None else Decimal("1") - bid

    @property
    def no_best_ask(self) -> Decimal | None:
        bid = self.yes_best_bid
        return None if bid is None else Decimal("1") - bid

    def bid_levels(self, outcome: str) -> list[tuple[Decimal, Decimal]]:
        source = self.yes_bids if outcome.upper() == "YES" else self.no_bids
        return sorted(source.items(), key=lambda x: x[0], reverse=True)

    def ask_levels(self, outcome: str) -> list[tuple[Decimal, Decimal]]:
        source = self.no_bids if outcome.upper() == "YES" else self.yes_bids
        return sorted(((Decimal("1") - p, q) for p, q in source.items()), key=lambda x: x[0])

    def executable_buy_vwap(self, outcome: str, contracts: Decimal | int | float) -> tuple[Decimal | None, Decimal]:
        remaining = _d(contracts)
        cost = Decimal("0")
        filled = Decimal("0")
        for price, size in self.ask_levels(outcome):
            if remaining <= 0:
                break
            take = min(remaining, size)
            if take <= 0:
                continue
            cost += take * price
            filled += take
            remaining -= take
        return ((cost / filled) if filled > 0 else None, filled)

    def summary(self, depth_levels: int = 5) -> dict[str, Any]:
        yb = self.bid_levels("YES")
        nb = self.bid_levels("NO")
        ya = self.ask_levels("YES")
        na = self.ask_levels("NO")
        v1y, f1y = self.executable_buy_vwap("YES", 1)
        v5y, f5y = self.executable_buy_vwap("YES", 5)
        v1n, f1n = self.executable_buy_vwap("NO", 1)
        v5n, f5n = self.executable_buy_vwap("NO", 5)
        return {
            "ticker": self.ticker,
            "sequence": self.sequence,
            "exchange_ts": self.last_exchange_ts,
            "yes_best_bid": _s(self.yes_best_bid),
            "yes_best_ask": _s(self.yes_best_ask),
            "no_best_bid": _s(self.no_best_bid),
            "no_best_ask": _s(self.no_best_ask),
            "yes_spread": _s(None if self.yes_best_bid is None or self.yes_best_ask is None else self.yes_best_ask - self.yes_best_bid),
            "no_spread": _s(None if self.no_best_bid is None or self.no_best_ask is None else self.no_best_ask - self.no_best_bid),
            "yes_bid_depth_top5": _s(sum((q for _, q in yb[:depth_levels]), Decimal("0"))),
            "yes_ask_depth_top5": _s(sum((q for _, q in ya[:depth_levels]), Decimal("0"))),
            "no_bid_depth_top5": _s(sum((q for _, q in nb[:depth_levels]), Decimal("0"))),
            "no_ask_depth_top5": _s(sum((q for _, q in na[:depth_levels]), Decimal("0"))),
            "yes_buy_vwap_1": _s(v1y), "yes_buy_filled_1": _s(f1y),
            "yes_buy_vwap_5": _s(v5y), "yes_buy_filled_5": _s(f5y),
            "no_buy_vwap_1": _s(v1n), "no_buy_filled_1": _s(f1n),
            "no_buy_vwap_5": _s(v5n), "no_buy_filled_5": _s(f5n),
            "yes_bid_levels_top5": [[_s(p), _s(q)] for p, q in yb[:depth_levels]],
            "yes_ask_levels_top5": [[_s(p), _s(q)] for p, q in ya[:depth_levels]],
            "no_bid_levels_top5": [[_s(p), _s(q)] for p, q in nb[:depth_levels]],
            "no_ask_levels_top5": [[_s(p), _s(q)] for p, q in na[:depth_levels]],
        }


def _s(value: Any) -> str | None:
    return None if value is None else str(value)
