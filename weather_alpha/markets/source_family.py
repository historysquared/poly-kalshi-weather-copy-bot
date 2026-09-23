from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SettlementSourceDetection:
    family: str
    evidence: tuple[str, ...]


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


def detect_settlement_source_family(
    market: dict[str, Any],
    event: dict[str, Any] | None,
    series: dict[str, Any] | None,
) -> SettlementSourceDetection:
    """Classify the official settlement source from Kalshi payload text.

    This is intentionally descriptive, not permissive: detecting The Weather
    Company does not make an NWS/CLI strategy eligible. Callers must still gate
    strategy logic on the source family they were validated against.
    """
    pairs: list[tuple[str, str]] = []
    for root, obj in (("market", market), ("event", event or {}), ("series", series or {})):
        pairs.extend((f"{root}.{path}", text) for path, text in _flatten(obj))

    cli: list[str] = []
    twc: list[str] = []
    for path, text in pairs:
        low_path = path.lower()
        low = text.lower()
        relevant = any(k in low_path for k in ("settle", "rule", "source", "product", "term", "title", "subtitle"))
        if not relevant:
            continue
        if any(k in low for k in ("national weather service", "climatological report", "daily climate report")) or low.strip() == "nws":
            cli.append(f"{path}={text}")
        if any(k in low for k in ("the weather company", "weather.com/kalshi", "globaltemperature")):
            twc.append(f"{path}={text}")

    if twc and cli:
        return SettlementSourceDetection("MIXED_OR_TRANSITIONAL", tuple(dict.fromkeys(twc + cli)))
    if twc:
        return SettlementSourceDetection("WEATHER_COMPANY", tuple(dict.fromkeys(twc)))
    if cli:
        return SettlementSourceDetection("NWS_CLI", tuple(dict.fromkeys(cli)))
    return SettlementSourceDetection("UNKNOWN", ())
