from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx


@dataclass(frozen=True)
class ForecastIntent:
    office: str
    issued_at: datetime
    text: str
    temperature_bias: float
    cloud_bias: float
    confidence: float
    matched_phrases: tuple[str, ...]


_NEG_TEMP = (
    r"lower(?:ed|ing)?\s+(?:afternoon\s+)?highs?",
    r"cooler\s+than\s+(?:previously\s+)?forecast",
    r"temperatures?\s+(?:will\s+)?struggle",
    r"persistent\s+(?:stratus|clouds?|cloud\s+cover)",
    r"slower\s+(?:burn[- ]?off|clearing)",
)
_POS_TEMP = (
    r"raise(?:d|ing)?\s+(?:afternoon\s+)?highs?",
    r"warmer\s+than\s+(?:previously\s+)?forecast",
    r"faster\s+(?:burn[- ]?off|clearing)",
    r"more\s+sun(?:shine)?\s+than\s+expected",
)
_CLOUDIER = (r"cloudier\s+than\s+expected", r"persistent\s+stratus", r"delayed\s+clearing")
_CLEARER = (r"clear(?:ing|ed)?\s+faster", r"less\s+cloud\s+cover", r"earlier\s+burn[- ]?off")


class NwsAfdProvider:
    """Fetch Area Forecast Discussions through the public NWS products API."""

    base = "https://api.weather.gov"

    async def latest(self, office: str) -> ForecastIntent:
        office = office.upper()
        headers = {"User-Agent": "weather-alpha-lab research contact"}
        async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
            listing = await client.get(f"{self.base}/products/types/AFD/locations/{office}")
            listing.raise_for_status()
            payload = listing.json()
            items = payload.get("@graph") or payload.get("products") or []
            if not items:
                raise RuntimeError(f"No AFD products found for {office}")
            product_id = items[0].get("id") or items[0].get("@id", "").rstrip("/").split("/")[-1]
            if not product_id:
                raise RuntimeError(f"AFD product id missing for {office}")
            product = await client.get(f"{self.base}/products/{product_id}")
            product.raise_for_status()
            doc = product.json()
        text = doc.get("productText") or ""
        issued = doc.get("issuanceTime")
        issued_at = datetime.fromisoformat(issued.replace("Z", "+00:00")) if issued else datetime.now(timezone.utc)
        return score_forecaster_intent(office, issued_at, text)


def score_forecaster_intent(office: str, issued_at: datetime, text: str) -> ForecastIntent:
    lower = text.lower()
    matched: list[str] = []
    temp = 0.0
    cloud = 0.0
    for pattern in _NEG_TEMP:
        if re.search(pattern, lower):
            temp -= 0.20
            matched.append(pattern)
    for pattern in _POS_TEMP:
        if re.search(pattern, lower):
            temp += 0.20
            matched.append(pattern)
    for pattern in _CLOUDIER:
        if re.search(pattern, lower):
            cloud -= 0.20
            matched.append(pattern)
    for pattern in _CLEARER:
        if re.search(pattern, lower):
            cloud += 0.20
            matched.append(pattern)
    # Phrase rules are deliberately conservative until calibrated on historical AFD vintages.
    temp = max(-1.0, min(1.0, temp))
    cloud = max(-1.0, min(1.0, cloud))
    confidence = min(1.0, len(matched) / 4.0)
    return ForecastIntent(
        office=office,
        issued_at=issued_at,
        text=text,
        temperature_bias=temp,
        cloud_bias=cloud,
        confidence=confidence,
        matched_phrases=tuple(matched),
    )
