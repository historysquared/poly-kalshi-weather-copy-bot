from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .settings import EngineSettings


class KalshiAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class KalshiOrderRequest:
    ticker: str
    client_order_id: str
    side: str
    count: str
    price: str
    time_in_force: str = "good_till_canceled"
    self_trade_prevention_type: str = "taker_at_cross"
    post_only: bool = False
    cancel_order_on_pause: bool = True
    reduce_only: bool = False
    subaccount: int = 0

    def payload(self) -> dict[str, Any]:
        if self.side not in {"bid", "ask"}:
            raise ValueError("side must be 'bid' or 'ask'")
        return {
            "ticker": self.ticker,
            "client_order_id": self.client_order_id,
            "side": self.side,
            "count": self.count,
            "price": self.price,
            "time_in_force": self.time_in_force,
            "self_trade_prevention_type": self.self_trade_prevention_type,
            "post_only": self.post_only,
            "cancel_order_on_pause": self.cancel_order_on_pause,
            "reduce_only": self.reduce_only,
            "subaccount": self.subaccount,
        }


class KalshiLiveClient:
    """Direct Kalshi REST client copied/adapted from kalshi-15m-lab.

    It is weather-agnostic: weather contract discovery/settlement mapping remains in
    the weather package. Authenticated mutation is only exposed through the guarded
    execution layer.
    """

    def __init__(self, settings: EngineSettings | None = None, timeout: float = 10.0):
        self.settings = settings or EngineSettings()
        self.timeout = timeout

    @staticmethod
    def signing_message(timestamp: str, method: str, path: str) -> bytes:
        return f"{timestamp}{method.upper()}{path.split('?', 1)[0]}".encode()

    def _headers(self, method: str, path: str) -> dict[str, str]:
        if not self.settings.kalshi_authenticated:
            raise KalshiAPIError("Kalshi credentials are not configured")
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError as exc:
            raise KalshiAPIError("Install cryptography for authenticated requests") from exc
        timestamp = str(int(time.time() * 1000))
        key = serialization.load_pem_private_key(Path(self.settings.kalshi_private_key_path).read_bytes(), password=None)
        signature = key.sign(
            self.signing_message(timestamp, method, path),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.settings.kalshi_api_key_id or "",
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        }

    def request(self, method: str, endpoint: str, *, params: dict[str, Any] | None = None,
                body: dict[str, Any] | None = None, auth: bool = False) -> dict[str, Any]:
        path = "/trade-api/v2" + endpoint
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = self.settings.kalshi_base_url.rstrip("/") + endpoint + ("?" + query if query else "")
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "User-Agent": "weather-alpha/0.2"}
        if auth:
            headers.update(self._headers(method, path))
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise KalshiAPIError(f"Kalshi HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc

    def markets(self, **params: Any) -> dict[str, Any]:
        return self.request("GET", "/markets", params=params)

    def market(self, ticker: str) -> dict[str, Any]:
        return self.request("GET", f"/markets/{ticker}")

    def orderbook(self, ticker: str, depth: int = 100) -> dict[str, Any]:
        return self.request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})

    def trades(self, ticker: str | None = None, **params: Any) -> dict[str, Any]:
        return self.request("GET", "/markets/trades", params={"ticker": ticker, **params})

    def balance(self) -> dict[str, Any]:
        return self.request("GET", "/portfolio/balance", auth=True)

    def orders(self, **params: Any) -> dict[str, Any]:
        return self.request("GET", "/portfolio/events/orders", params=params, auth=True)

    def fills(self, *, order_id: str, **params: Any) -> dict[str, Any]:
        return self.request("GET", "/portfolio/fills", params={"order_id": order_id, **params}, auth=True)

    def create_order(self, order: KalshiOrderRequest | dict[str, Any]) -> dict[str, Any]:
        body = order.payload() if isinstance(order, KalshiOrderRequest) else order
        return self.request("POST", "/portfolio/events/orders", body=body, auth=True)

    def cancel_order(self, order_id: str, *, subaccount: int | None = None) -> dict[str, Any]:
        return self.request("DELETE", f"/portfolio/events/orders/{order_id}", params={"subaccount": subaccount}, auth=True)
