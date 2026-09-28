from __future__ import annotations

from .reconstruction import StationClock

# Fixed Local Standard Time offsets are settlement semantics, not current civil
# UTC offsets. These stay fixed through DST and are paired with the IANA civil
# timezone only for diagnostics/display.
_STATION_CLOCKS: dict[str, StationClock] = {
    # Eastern Standard Time settlement clocks
    "KNYC": StationClock("KNYC", "America/New_York", -5),
    "KMIA": StationClock("KMIA", "America/New_York", -5),
    "KBOS": StationClock("KBOS", "America/New_York", -5),
    "KATL": StationClock("KATL", "America/New_York", -5),
    "KDCA": StationClock("KDCA", "America/New_York", -5),
    "KPHL": StationClock("KPHL", "America/New_York", -5),
    "KTTN": StationClock("KTTN", "America/New_York", -5),
    "KEWR": StationClock("KEWR", "America/New_York", -5),
    "KSDF": StationClock("KSDF", "America/Kentucky/Louisville", -5),
    # Central Standard Time settlement clocks
    "KMDW": StationClock("KMDW", "America/Chicago", -6),
    "KAUS": StationClock("KAUS", "America/Chicago", -6),
    "KMSP": StationClock("KMSP", "America/Chicago", -6),
    "KMSY": StationClock("KMSY", "America/Chicago", -6),
    "KSAT": StationClock("KSAT", "America/Chicago", -6),
    "KDFW": StationClock("KDFW", "America/Chicago", -6),
    "KOKC": StationClock("KOKC", "America/Chicago", -6),
    "KIAH": StationClock("KIAH", "America/Chicago", -6),
    # Mountain Standard Time settlement clocks
    "KDEN": StationClock("KDEN", "America/Denver", -7),
    "KPHX": StationClock("KPHX", "America/Phoenix", -7),
    # Pacific Standard Time settlement clocks
    "KLAX": StationClock("KLAX", "America/Los_Angeles", -8),
    "KSFO": StationClock("KSFO", "America/Los_Angeles", -8),
    "KSEA": StationClock("KSEA", "America/Los_Angeles", -8),
    "KLAS": StationClock("KLAS", "America/Los_Angeles", -8),
    "KSAN": StationClock("KSAN", "America/Los_Angeles", -8),
}



def station_clock(station: str) -> StationClock:
    key = station.upper().strip()
    try:
        return _STATION_CLOCKS[key]
    except KeyError as exc:
        raise KeyError(f"no verified settlement clock registered for {key}") from exc


def registered_station_clocks() -> dict[str, StationClock]:
    return dict(_STATION_CLOCKS)
