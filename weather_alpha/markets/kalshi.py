from __future__ import annotations
from datetime import datetime
import httpx
from ..models import MarketQuote

class KalshiPublicClient:
    BASE = "https://external-api.kalshi.com/trade-api/v2"

    async def open_markets(self, series_ticker: str, limit: int = 200) -> list[MarketQuote]:
        params = {"series_ticker": series_ticker, "status": "open", "limit": limit}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{self.BASE}/markets", params=params)
            r.raise_for_status()
            data = r.json()
        return [self._parse(m) for m in data.get("markets", [])]

    @staticmethod
    def _num(x):
        if x in (None, ""):
            return None
        return float(x)

    @staticmethod
    def _dt(x):
        if not x:
            return None
        return datetime.fromisoformat(x.replace("Z", "+00:00"))

    def _parse(self, m: dict) -> MarketQuote:
        yes_bid = self._num(m.get("yes_bid_dollars"))
        yes_ask = self._num(m.get("yes_ask_dollars"))
        no_bid = self._num(m.get("no_bid_dollars"))
        no_ask = self._num(m.get("no_ask_dollars"))
        if yes_ask is None and no_bid is not None:
            yes_ask = 1.0 - no_bid
        if no_ask is None and yes_bid is not None:
            no_ask = 1.0 - yes_bid
        return MarketQuote(
            ticker=m["ticker"],
            event_ticker=m.get("event_ticker", ""),
            title=m.get("title", ""),
            subtitle=m.get("yes_sub_title") or m.get("subtitle") or "",
            floor_strike=self._num(m.get("floor_strike")),
            cap_strike=self._num(m.get("cap_strike")),
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            volume=float(m.get("volume_fp") or 0),
            close_time=self._dt(m.get("close_time")),
            updated_time=self._dt(m.get("updated_time")),
        )
