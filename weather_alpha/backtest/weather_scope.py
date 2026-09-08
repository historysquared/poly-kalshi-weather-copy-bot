from __future__ import annotations

import re

SITE_PARAM_RE = re.compile(r"(?:[?&](?:site|station)=)([A-Z0-9]{4,5})(?:&|$)", re.I)
WU_PATH_RE = re.compile(r"wunderground\.com/history/daily/[^\s)]+/([A-Z0-9]{4,5})(?:[\s).]|$)", re.I)
ICAO_RULE_RE = re.compile(r"\b(?:airport|weather|asos)\s+station\s+(?:at\s+)?\(?([A-Z][A-Z0-9]{3})\)?\b", re.I)


def settlement_station(market) -> str:
    """Extract an explicit settlement station only; never infer it from city name."""
    text = "\n".join(x for x in (market.resolution_source, market.description, market.question) if x)
    for regex in (SITE_PARAM_RE, WU_PATH_RE, ICAO_RULE_RE):
        found = regex.search(text)
        if found:
            return found.group(1).upper()
    return "UNKNOWN"


def is_us_icao(station: str) -> bool:
    return len(station) == 4 and station.startswith("K") and station.isalnum()
