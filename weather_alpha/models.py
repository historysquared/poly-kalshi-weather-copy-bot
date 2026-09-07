from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Side = Literal["yes", "no"]

@dataclass(frozen=True)
class Observation:
    station: str
    observed_at: datetime
    temp_f: float
    dewpoint_f: float | None = None
    wind_mph: float | None = None
    wind_dir_deg: float | None = None
    pressure_hpa: float | None = None
    provider: str = "unknown"

@dataclass(frozen=True)
class ForecastDistribution:
    station: str
    valid_date: str
    source: str
    mean_f: float
    sigma_f: float
    members_f: tuple[float, ...] = ()
    issued_at: datetime | None = None

@dataclass(frozen=True)
class MarketQuote:
    ticker: str
    event_ticker: str
    title: str
    subtitle: str
    floor_strike: float | None
    cap_strike: float | None
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    volume: float
    close_time: datetime | None
    updated_time: datetime | None

@dataclass(frozen=True)
class Signal:
    ticker: str
    side: Side
    model_probability: float
    entry_price: float
    edge: float
    expected_value_per_contract: float
    reason: str
