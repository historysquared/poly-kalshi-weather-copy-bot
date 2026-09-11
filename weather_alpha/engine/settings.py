from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EngineSettings:
    data_root: Path = Path(os.getenv("WEATHER_DATA_ROOT", "/data/weather"))
    state_db_path: Path = Path(os.getenv("WEATHER_STATE_DB", "/data/weather/live/state.sqlite3"))
    live_data_path: Path = Path(os.getenv("WEATHER_LIVE_DATA", "/data/weather/live/parquet"))

    kalshi_base_url: str = os.getenv("KALSHI_BASE_URL", "https://external-api.kalshi.com/trade-api/v2")
    kalshi_api_key_id: str | None = os.getenv("KALSHI_API_KEY_ID")
    kalshi_private_key_path: str | None = os.getenv("KALSHI_PRIVATE_KEY_PATH")

    polymarket_us_base_url: str = os.getenv("POLYMARKET_US_BASE_URL", "https://polymarket.us")
    polymarket_us_api_key: str | None = os.getenv("POLYMARKET_US_API_KEY")

    execution_latency_seconds: float = float(os.getenv("WEATHER_EXECUTION_LATENCY_SECONDS", "0"))
    slippage_dollars: float = float(os.getenv("WEATHER_SLIPPAGE", "0"))
    max_contracts_per_order: int = int(os.getenv("WEATHER_MAX_CONTRACTS_PER_ORDER", "1"))

    kalshi_live_trading_enabled: bool = env_bool("WEATHER_KALSHI_LIVE_TRADING")
    polymarket_live_trading_enabled: bool = env_bool("WEATHER_POLYMARKET_LIVE_TRADING")

    @property
    def kalshi_authenticated(self) -> bool:
        return bool(self.kalshi_api_key_id and self.kalshi_private_key_path)

    @property
    def polymarket_authenticated(self) -> bool:
        return bool(self.polymarket_us_api_key)
