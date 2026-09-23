from __future__ import annotations

from .reconstruction import StationClock

# Fixed Local Standard Time offsets are settlement semantics, not current civil
# UTC offsets. These stay fixed through DST and are paired with the IANA civil
# timezone only for diagnostics/display.
_STATION_CLOCKS: dict[str, StationClock] = {
    "KDEN": StationClock("KDEN", "America/Denver", -7),
    "KLAX": StationClock("KLAX", "America/Los_Angeles", -8),
    "KMDW": StationClock("KMDW", "America/Chicago", -6),
    "KMIA": StationClock("KMIA", "America/New_York", -5),
    "KNYC": StationClock("KNYC", "America/New_York", -5),
}


def station_clock(station: str) -> StationClock:
    key = station.upper().strip()
    try:
        return _STATION_CLOCKS[key]
    except KeyError as exc:
        raise KeyError(f"no verified settlement clock registered for {key}") from exc


def registered_station_clocks() -> dict[str, StationClock]:
    return dict(_STATION_CLOCKS)
