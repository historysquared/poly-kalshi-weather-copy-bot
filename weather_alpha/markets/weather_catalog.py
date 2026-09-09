from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Iterable

from .contracts import ContractShape


class CatalogStatus(StrEnum):
    EXACT = "EXACT"
    PROBABLE = "PROBABLE"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


class WeatherMeasure(StrEnum):
    DAILY_HIGH = "DAILY_HIGH"
    DAILY_LOW = "DAILY_LOW"
    TEMPERATURE = "TEMPERATURE"
    RAIN = "RAIN"
    SNOW = "SNOW"
    OTHER = "OTHER"


@dataclass(frozen=True)
class WeatherCatalogRecord:
    venue: str
    contract_id: str
    event_id: str
    status: CatalogStatus
    measurement: WeatherMeasure
    station: str | None
    settlement_date: date | None
    shape: ContractShape | None
    lower: float | None
    upper: float | None
    settlement_source: str | None
    title: str
    subtitle: str
    close_time: datetime | None
    reasons: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def weather_event_id(self) -> str | None:
        if self.station is None or self.settlement_date is None:
            return None
        return f"{self.station}_{self.settlement_date.isoformat()}_{self.measurement.value}"


_WEATHER_TERMS = re.compile(
    r"\b(?:temperature|temp|daily high|daily low|high temperature|low temperature|rain|rainfall|precipitation|snow|snowfall|weather)\b",
    re.I,
)
_TICKER_HINT = re.compile(r"(?:^|[-_])(?:HIGH|LOW|TEMP|RAIN|SNOW|WX)(?:[-_]|$)", re.I)
_STATION = re.compile(r"\bK[A-Z]{3}\b")
_NWS_SOURCE = re.compile(r"(?:National Weather Service|\bNWS\b|Climatological Report|Daily Climate Report|CLI)", re.I)
_DATE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")


def _text(meta: dict[str, Any]) -> str:
    return " ".join(
        str(meta.get(key) or "")
        for key in ("ticker", "event_ticker", "title", "subtitle", "yes_sub_title", "rules_primary", "rules_secondary")
    )


def looks_weather_like(meta: dict[str, Any]) -> bool:
    text = _text(meta)
    return bool(_WEATHER_TERMS.search(text) or _TICKER_HINT.search(str(meta.get("ticker") or "")))


def infer_measurement(meta: dict[str, Any]) -> WeatherMeasure:
    text = _text(meta).lower()
    if "daily high" in text or "high temperature" in text or re.search(r"\bhigh\b", text):
        return WeatherMeasure.DAILY_HIGH
    if "daily low" in text or "low temperature" in text or re.search(r"\blow\b", text):
        return WeatherMeasure.DAILY_LOW
    if "snow" in text:
        return WeatherMeasure.SNOW
    if "rain" in text or "precip" in text:
        return WeatherMeasure.RAIN
    if "temperature" in text or "temp" in text:
        return WeatherMeasure.TEMPERATURE
    return WeatherMeasure.OTHER


def explicit_station(meta: dict[str, Any]) -> str | None:
    stations = sorted(set(_STATION.findall(_text(meta).upper())))
    return stations[0] if len(stations) == 1 else None


def explicit_settlement_date(meta: dict[str, Any]) -> date | None:
    value = meta.get("settlement_date")
    if value:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            pass
    match = _DATE.search(" ".join(str(meta.get(k) or "") for k in ("rules_primary", "rules_secondary", "title")))
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None


def _num(value: object | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def infer_shape(meta: dict[str, Any]) -> tuple[ContractShape | None, float | None, float | None]:
    floor = _num(meta.get("floor_strike"))
    cap = _num(meta.get("cap_strike"))
    if floor is not None and cap is not None:
        return ContractShape.BUCKET, floor, cap
    if floor is not None:
        return ContractShape.ABOVE, floor, None
    if cap is not None:
        return ContractShape.BELOW, None, cap
    return None, None, None


def classify_kalshi_market(meta: dict[str, Any]) -> WeatherCatalogRecord:
    ticker = str(meta.get("ticker") or "")
    event_ticker = str(meta.get("event_ticker") or "")
    title = str(meta.get("title") or "")
    subtitle = str(meta.get("yes_sub_title") or meta.get("subtitle") or "")
    reasons: list[str] = []

    if not ticker:
        return WeatherCatalogRecord("kalshi", "", event_ticker, CatalogStatus.REJECT, WeatherMeasure.OTHER,
                                    None, None, None, None, None, None, title, subtitle, None,
                                    ("missing ticker",), meta)
    if not looks_weather_like(meta):
        return WeatherCatalogRecord("kalshi", ticker, event_ticker, CatalogStatus.REJECT, WeatherMeasure.OTHER,
                                    None, None, None, None, None, None, title, subtitle, None,
                                    ("no weather evidence",), meta)

    measure = infer_measurement(meta)
    station = explicit_station(meta)
    settle_date = explicit_settlement_date(meta)
    shape, lower, upper = infer_shape(meta)
    source_text = " ".join(str(meta.get(k) or "") for k in ("rules_primary", "rules_secondary"))
    source = source_text.strip() or None
    source_verified = bool(_NWS_SOURCE.search(source_text))

    if station is None: reasons.append("station not explicit/unique in official metadata")
    if settle_date is None: reasons.append("settlement date not explicit in official metadata")
    if shape is None and measure in {WeatherMeasure.DAILY_HIGH, WeatherMeasure.DAILY_LOW, WeatherMeasure.TEMPERATURE}:
        reasons.append("temperature contract bounds unavailable")
    if not source_verified: reasons.append("settlement source not explicitly NWS/CLI-like")

    exact = station is not None and settle_date is not None and source_verified
    if measure in {WeatherMeasure.DAILY_HIGH, WeatherMeasure.DAILY_LOW, WeatherMeasure.TEMPERATURE}:
        exact = exact and shape is not None
    if exact:
        status = CatalogStatus.EXACT
    elif station is not None or source_verified:
        status = CatalogStatus.PROBABLE
    else:
        status = CatalogStatus.REVIEW

    close = meta.get("close_time")
    if isinstance(close, str) and close:
        try: close = datetime.fromisoformat(close.replace("Z", "+00:00"))
        except ValueError: close = None
    elif not isinstance(close, datetime):
        close = None

    return WeatherCatalogRecord("kalshi", ticker, event_ticker, status, measure, station, settle_date,
                                shape, lower, upper, source, title, subtitle, close, tuple(reasons), meta)


def classify_many(markets: Iterable[dict[str, Any]]) -> list[WeatherCatalogRecord]:
    return [classify_kalshi_market(m) for m in markets]
