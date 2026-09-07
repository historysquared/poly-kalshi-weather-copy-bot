from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional


class ContractShape(str, Enum):
    BELOW = "below"
    ABOVE = "above"
    BUCKET = "bucket"


@dataclass(frozen=True)
class WeatherContract:
    venue: str
    market_id: str
    city: str
    station: str
    settlement_date: date
    shape: ContractShape
    lower_f: Optional[float] = None
    upper_f: Optional[float] = None
    settlement_source: str = "NWS Climatological Report (Daily)"

    def contains(self, temp_f: float) -> bool:
        if self.shape == ContractShape.BELOW:
            return temp_f <= float(self.upper_f)
        if self.shape == ContractShape.ABOVE:
            return temp_f >= float(self.lower_f)
        return float(self.lower_f) <= temp_f <= float(self.upper_f)


_BUCKET = re.compile(r"(?P<lo>-?\d+(?:\.\d+)?)\s*(?:to|–|-)\s*(?P<hi>-?\d+(?:\.\d+)?)", re.I)
_BELOW = re.compile(r"(?P<x>-?\d+(?:\.\d+)?)\s*(?:°?F)?\s*or\s*below|(?:less than or equal to|<=)\s*(?P<y>-?\d+(?:\.\d+)?)", re.I)
_ABOVE = re.compile(r"(?P<x>-?\d+(?:\.\d+)?)\s*(?:°?F)?\s*or\s*above|(?:greater than or equal to|>=)\s*(?P<y>-?\d+(?:\.\d+)?)", re.I)


def parse_temperature_outcome(label: str) -> tuple[ContractShape, Optional[float], Optional[float]]:
    """Normalize common Kalshi/Polymarket-US daily-high outcome labels."""
    if m := _BUCKET.search(label):
        return ContractShape.BUCKET, float(m.group("lo")), float(m.group("hi"))
    if m := _BELOW.search(label):
        value = float(m.group("x") or m.group("y"))
        return ContractShape.BELOW, None, value
    if m := _ABOVE.search(label):
        value = float(m.group("x") or m.group("y"))
        return ContractShape.ABOVE, value, None
    raise ValueError(f"Unsupported temperature outcome label: {label!r}")


# Verified settlement-station examples from current Polymarket US weather rules.
POLYMARKET_US_STATIONS = {
    "NYC": "KNYC",       # Central Park
    "CHICAGO": "KMDW",   # Chicago Midway
    "MIAMI": "KMIA",     # Miami International
    "LOS ANGELES": "KLAX",
    "SAN FRANCISCO": "KSFO",
}
