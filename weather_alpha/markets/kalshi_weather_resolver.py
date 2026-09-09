from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from .weather_catalog import CatalogStatus, WeatherCatalogRecord

_STATION = re.compile(r"(?<![A-Z0-9])K[A-Z]{3}(?![A-Z0-9])", re.I)
_ISO_DATE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")
_EVENT_DATE = re.compile(r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{1,2})(?:-|$)", re.I)
_MONTHS = {m: i for i, m in enumerate(("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"), 1)}
_CLI = re.compile(r"(?:\bCLI\b|Daily Climate Report|Climatological Report|National Weather Service|\bNWS\b)", re.I)


@dataclass(frozen=True)
class ResolutionEvidence:
    station: str | None
    settlement_date: date | None
    settlement_source: str | None
    station_evidence: tuple[str, ...]
    date_evidence: tuple[str, ...]
    source_evidence: tuple[str, ...]
    ticker_date_check: date | None
    ticker_date_matches: bool | None
    exact: bool
    reasons: tuple[str, ...]


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_flatten(item, path))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            out.extend(_flatten(item, f"{prefix}[{i}]"))
    elif value is not None:
        out.append((prefix, str(value)))
    return out


def ticker_event_date(event_ticker: str) -> date | None:
    m = _EVENT_DATE.search(event_ticker.upper())
    if not m:
        return None
    yy, mon, day = m.groups()
    try:
        return date(2000 + int(yy), _MONTHS[mon.upper()], int(day))
    except ValueError:
        return None


def _dates_from_pairs(pairs: Iterable[tuple[str, str]]) -> list[tuple[date, str]]:
    found: list[tuple[date, str]] = []
    for path, text in pairs:
        low = path.lower()
        leaf = re.sub(r"\[\d+\]", "", low.rsplit(".", 1)[-1])
        # A market close/expiration date is not settlement-event proof. Prefer
        # explicit settlement/event/strike date fields or dates stated in rules.
        semantic = leaf in {"settlement_date", "event_date", "strike_date", "observation_date", "date"} or "rule" in low or "term" in low
        if not semantic:
            continue
        for m in _ISO_DATE.finditer(text):
            try:
                found.append((date(int(m.group(1)), int(m.group(2)), int(m.group(3))), f"{path}={text}"))
            except ValueError:
                pass
    return found


def resolve_weather_rules(market: dict[str, Any], event: dict[str, Any] | None, series: dict[str, Any] | None) -> ResolutionEvidence:
    """Resolve station/date/source from official Kalshi payloads and fail closed.

    Ticker dates are only consistency checks. They never supply the settlement
    date by themselves. EXACT requires one unique station, one authoritative
    date from event/market rule fields, CLI/NWS-like settlement evidence, and a
    matching ticker date when the ticker encodes one.
    """
    objects = (("market", market), ("event", event or {}), ("series", series or {}))
    pairs: list[tuple[str, str]] = []
    for name, obj in objects:
        pairs.extend((f"{name}.{path}", text) for path, text in _flatten(obj))

    station_hits: dict[str, list[str]] = {}
    source_hits: list[str] = []
    for path, text in pairs:
        if any(token in path.lower() for token in ("settle", "rule", "source", "product", "term", "title", "subtitle")):
            for station in _STATION.findall(text.upper()):
                station_hits.setdefault(station, []).append(f"{path}={text}")
        if _CLI.search(text) and any(token in path.lower() for token in ("settle", "rule", "source", "term", "product")):
            source_hits.append(f"{path}={text}")

    stations = sorted(station_hits)
    station = stations[0] if len(stations) == 1 else None
    date_hits = _dates_from_pairs(pairs)
    unique_dates = sorted({d for d, _ in date_hits})
    settle_date = unique_dates[0] if len(unique_dates) == 1 else None
    event_ticker = str(market.get("event_ticker") or (event or {}).get("event_ticker") or (event or {}).get("ticker") or "")
    ticker_date = ticker_event_date(event_ticker)
    ticker_match = None if ticker_date is None or settle_date is None else ticker_date == settle_date

    reasons: list[str] = []
    if station is None:
        reasons.append("official rules do not identify one unique ICAO station")
    if settle_date is None:
        reasons.append("official event/rule payload does not identify one unique settlement date")
    if not source_hits:
        reasons.append("official rules do not identify an NWS/CLI-like settlement source")
    if ticker_match is False:
        reasons.append("official settlement date conflicts with ticker date")
    exact = station is not None and settle_date is not None and bool(source_hits) and ticker_match is not False
    return ResolutionEvidence(
        station=station,
        settlement_date=settle_date,
        settlement_source=source_hits[0] if source_hits else None,
        station_evidence=tuple(station_hits.get(station, ())) if station else (),
        date_evidence=tuple(e for d, e in date_hits if d == settle_date) if settle_date else (),
        source_evidence=tuple(source_hits),
        ticker_date_check=ticker_date,
        ticker_date_matches=ticker_match,
        exact=exact,
        reasons=tuple(reasons),
    )


def enrich_catalog_record(record: WeatherCatalogRecord, evidence: ResolutionEvidence) -> WeatherCatalogRecord:
    temperature_shape_ok = record.shape is not None if record.measurement.value in {"DAILY_HIGH", "DAILY_LOW", "TEMPERATURE"} else True
    exact = evidence.exact and temperature_shape_ok
    reasons = list(evidence.reasons)
    if not temperature_shape_ok:
        reasons.append("temperature contract bounds unavailable")
    return WeatherCatalogRecord(
        venue=record.venue,
        contract_id=record.contract_id,
        event_id=record.event_id,
        status=CatalogStatus.EXACT if exact else CatalogStatus.PROBABLE,
        measurement=record.measurement,
        station=evidence.station or record.station,
        settlement_date=evidence.settlement_date or record.settlement_date,
        shape=record.shape,
        lower=record.lower,
        upper=record.upper,
        settlement_source=evidence.settlement_source or record.settlement_source,
        title=record.title,
        subtitle=record.subtitle,
        close_time=record.close_time,
        reasons=tuple(reasons),
        raw=record.raw,
    )
