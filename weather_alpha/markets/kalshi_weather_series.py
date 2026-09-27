from __future__ import annotations

# Canonical recurring Kalshi daily-high weather series.
# Keep this exact: KXHIGHINFLATION and other KXHIGH* series are not weather.
SERIES_BY_CITY: dict[str, str] = {
    "new_york_city": "KXHIGHNY",
    "chicago": "KXHIGHCHI",
    "miami": "KXHIGHMIA",
    "los_angeles": "KXHIGHLAX",
    "san_francisco": "KXHIGHTSFO",
    "denver": "KXHIGHDEN",
    "boston": "KXHIGHTBOS",
    "austin": "KXHIGHAUS",
    "seattle": "KXHIGHTSEA",
    "atlanta": "KXHIGHTATL",
    "las_vegas": "KXHIGHTLV",
    "minneapolis": "KXHIGHTMIN",
    "new_orleans": "KXHIGHTNOLA",
    "washington_dc": "KXHIGHTDC",
    "philadelphia": "KXHIGHPHIL",
    "san_antonio": "KXHIGHTSATX",
    "dallas": "KXHIGHTDAL",
    "oklahoma_city": "KXHIGHTOKC",
    "phoenix": "KXHIGHTPHX",
    "houston": "KXHIGHTHOU",
}

STATION_BY_CITY: dict[str, str] = {
    "new_york_city": "KNYC", "chicago": "KMDW", "miami": "KMIA",
    "los_angeles": "KLAX", "san_francisco": "KSFO", "denver": "KDEN",
    "boston": "KBOS", "austin": "KAUS", "seattle": "KSEA",
    "atlanta": "KATL", "las_vegas": "KLAS", "minneapolis": "KMSP",
    "new_orleans": "KMSY", "washington_dc": "KDCA", "philadelphia": "KPHL",
    "san_antonio": "KSAT", "dallas": "KDFW", "oklahoma_city": "KOKC",
    "phoenix": "KPHX", "houston": "KIAH",
}

ALL_HIGH_SERIES: tuple[str, ...] = tuple(SERIES_BY_CITY.values())
CITY_BY_SERIES: dict[str, str] = {series: city for city, series in SERIES_BY_CITY.items()}
STATION_BY_SERIES: dict[str, str] = {
    SERIES_BY_CITY[city]: station for city, station in STATION_BY_CITY.items()
}
