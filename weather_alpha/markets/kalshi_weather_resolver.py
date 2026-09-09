from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from .weather_catalog import CatalogStatus, WeatherCatalogRecord

_STATION = re.compile(r"(?<![A-Z0-9])K[A-Z]{3}(?![A-Z0-9])", re.I)
_ISO_DATE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")
_NATURAL_DATE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b",
    re.I,
)
_EVENT_DATE = re.compile(r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{1,2})(?:-|$)", re.I)
_MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
_MONTH_NAMES = {
    "JANUARY": 1, "JAN": 1, "FEBRUARY": 2, "FEB": 2, "MARCH": 3, "MAR": 3,
    "APRIL": 4, "APR": 4, "MAY": 5, "JUNE": 6, "JUN": 6, "JULY": 7, "JUL": 7,
    "AUGUST": 8, "AUG": 8, "SEPTEMBER": 9, "SEPT": 9, "SEP": 9, "OCTOBER": 10, "OCT": 10,
    "NOVEMBER": 11, "NOV": 11, "DECEMBER": 12, "DEC": 12,
}
_CLI = re.compile(r"(?:\bCLI\b|Daily Climate Report|Climatological Report|National Weather Service|\bNWS\b)", re.I)

# These aliases are normalization rules for explicit official source text only.
# They are never used merely because a ticker/city implies a station.
_STATION_ALIASES: dict[str, tuple[str, ...]] = {
    "KNYC": ("central park", "new york central park", "issuedby=nyc"),
    "KMDW": ("chicago midway", "midway airport", "midway, il", "issuedby=mdw"),
    "KMIA": ("miami international", "miami intl", "issuedby=mia"),
    "KLAX": ("los angeles international", "los angeles intl", "issuedby=lax"),
    "KDEN": ("denver international", "denver intl", "issuedby=den"),
}


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
    series_drift_risk: bool = False
    series_last_updated: datetime | None = None


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


def _dates_in_text(text: str) -> list[date]:
    out: list[date] = []
    for m in _ISO_DATE.finditer(text):
        try:
            out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    for m in _NATURAL_DATE.finditer(text):
        try:
            out.append(date(int(m.group(3)), _MONTH_NAMES[m.group(1).upper().rstrip(".")], int(m.group(2))))
        except (KeyError, ValueError):
            pass
    return out


def _dates_from_pairs(pairs: Iterable[tuple[str, str]]) -> list[tuple[date, str]]:
    found: list[tuple[date, str]] = []
    for path, text in pairs:
        low = path.lower()
        leaf = re.sub(r"\[\d+\]", "", low.rsplit(".", 1)[-1])
        semantic = (
            leaf in {"settlement_date", "event_date", "strike_date", "observation_date", "date"}
            or "rule" in low
            or "term" in low
            or "title" in low
            or "subtitle" in low
        )
        if not semantic:
            continue
        for parsed in _dates_in_text(text):
            found.append((parsed, f"{path}={text}"))
    return found


def _station_hits_from_text(path: str, text: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    lower = text.lower()
    for station in _STATION.findall(text.upper()):
        hits.setdefault(station, []).append(f"{path}={text}")

    # NWS CLI URLs commonly identify the issuing station via `issuedby=MDW`.
    if "http" in lower:
        try:
            parsed = urlparse(text)
            issued = (parse_qs(parsed.query).get("issuedby") or [""])[0].upper()
            if len(issued) == 3 and issued.isalpha():
                station = "K" + issued
                if station in _STATION_ALIASES:
                    hits.setdefault(station, []).append(f"{path}={text}")
        except ValueError:
            pass

    for station, aliases in _STATION_ALIASES.items():
        if any(alias in lower for alias in aliases):
            hits.setdefault(station, []).append(f"{path}={text}")
    return hits


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_weather_rules(market: dict[str, Any], event: dict[str, Any] | None, series: dict[str, Any] | None) -> ResolutionEvidence:
    """Resolve station/date/source from official Kalshi payloads and fail closed.

    The event ticker is a consistency check, never the sole date source. Natural
    dates in official titles/rules are accepted. Station-name aliases are only
    accepted when the corresponding text actually appears in official rule or
    settlement-source payloads, and the triggering evidence is persisted.

    Current recurring-series metadata can drift over time. If the series was
    updated after a historical event, series-only evidence cannot certify that
    event as EXACT; market/event evidence must independently support station and
    source.
    """
    objects = (("market", market), ("event", event or {}), ("series", series or {}))
    pairs: list[tuple[str, str]] = []
    for name, obj in objects:
        pairs.extend((f"{name}.{path}", text) for path, text in _flatten(obj))

    station_hits: dict[str, list[str]] = {}
    source_hits: list[str] = []
    nonseries_station_hits: dict[str, list[str]] = {}
    nonseries_source_hits: list[str] = []

    for path, text in pairs:
        relevant = any(token in path.lower() for token in ("settle", "rule", "source", "product", "term", "title", "subtitle"))
        if not relevant:
            continue
        for station, evidence in _station_hits_from_text(path, text).items():
            station_hits.setdefault(station, []).extend(evidence)
            if not path.startswith("series."):
                nonseries_station_hits.setdefault(station, []).extend(evidence)
        if _CLI.search(text) and any(token in path.lower() for token in ("settle", "rule", "source", "term", "product")):
            evidence = f"{path}={text}"
            source_hits.append(evidence)
            if not path.startswith("series."):
                nonseries_source_hits.append(evidence)

    stations = sorted(station_hits)
    station = stations[0] if len(stations) == 1 else None

    date_hits = _dates_from_pairs(pairs)
    unique_dates = sorted({d for d, _ in date_hits})
    event_ticker = str(market.get("event_ticker") or (event or {}).get("event_ticker") or (event or {}).get("ticker") or "")
    ticker_date = ticker_event_date(event_ticker)

    settle_date: date | None = None
    if ticker_date is not None and ticker_date in unique_dates:
        settle_date = ticker_date
    elif len(unique_dates) == 1:
        settle_date = unique_dates[0]
    ticker_match = None if ticker_date is None or settle_date is None else ticker_date == settle_date

    series_updated = _parse_timestamp((series or {}).get("last_updated_ts"))
    series_drift_risk = bool(series_updated and ticker_date and series_updated.date() > ticker_date)
    historical_evidence_ok = True
    if series_drift_risk:
        historical_evidence_ok = bool(nonseries_station_hits.get(station or "")) and bool(nonseries_source_hits)

    reasons: list[str] = []
    if station is None:
        reasons.append("official rules do not identify one unique settlement station")
    if settle_date is None:
        reasons.append("official market/event text does not identify the event settlement date")
    if not source_hits:
        reasons.append("official rules do not identify an NWS/CLI-like settlement source")
    if ticker_match is False:
        reasons.append("official settlement date conflicts with ticker date")
    if series_drift_risk and not historical_evidence_ok:
        reasons.append("current recurring-series metadata post-dates event; historical market/event rules must independently prove station/source")

    exact = (
        station is not None
        and settle_date is not None
        and bool(source_hits)
        and ticker_match is not False
        and historical_evidence_ok
    )
    preferred_source_hits = nonseries_source_hits or source_hits
    preferred_station_hits = nonseries_station_hits or station_hits
    return ResolutionEvidence(
        station=station,
        settlement_date=settle_date,
        settlement_source=preferred_source_hits[0] if preferred_source_hits else None,
        station_evidence=tuple(preferred_station_hits.get(station, ())) if station else (),
        date_evidence=tuple(e for d, e in date_hits if d == settle_date) if settle_date else (),
        source_evidence=tuple(preferred_source_hits),
        ticker_date_check=ticker_date,
        ticker_date_matches=ticker_match,
        exact=exact,
        reasons=tuple(reasons),
        series_drift_risk=series_drift_risk,
        series_last_updated=series_updated,
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
