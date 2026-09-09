from weather_alpha.markets.contracts import ContractShape
from weather_alpha.markets.weather_catalog import (
    CatalogStatus,
    WeatherMeasure,
    classify_kalshi_market,
    looks_weather_like,
)


def test_rejects_non_weather_market():
    rec = classify_kalshi_market({"ticker":"HOUSEPA7-26-D","title":"Who will win Pennsylvania House district 7?"})
    assert rec.status == CatalogStatus.REJECT


def test_weather_ticker_is_candidate_but_not_exact_without_official_rules():
    assert looks_weather_like({"ticker":"KXHIGH-KNYC-2026-06-10-B85"})
    rec = classify_kalshi_market({"ticker":"KXHIGH-KNYC-2026-06-10-B85","title":"Daily high temperature"})
    assert rec.status in {CatalogStatus.PROBABLE, CatalogStatus.REVIEW}
    assert rec.status != CatalogStatus.EXACT


def test_exact_daily_high_requires_station_date_source_and_bounds():
    rec = classify_kalshi_market({
        "ticker":"KXHIGHNY-26JUN10-B85.5",
        "event_ticker":"KXHIGHNY-26JUN10",
        "title":"Daily high temperature in New York",
        "yes_sub_title":"85.5 to 86.5",
        "floor_strike":85.5,
        "cap_strike":86.5,
        "settlement_date":"2026-06-10",
        "rules_primary":"Resolves using the National Weather Service Daily Climate Report for station KNYC.",
    })
    assert rec.status == CatalogStatus.EXACT
    assert rec.measurement == WeatherMeasure.DAILY_HIGH
    assert rec.station == "KNYC"
    assert rec.shape == ContractShape.BUCKET
    assert rec.weather_event_id == "KNYC_2026-06-10_DAILY_HIGH"


def test_multiple_station_codes_fail_closed():
    rec = classify_kalshi_market({
        "ticker":"KXHIGHNY-26JUN10-B85.5",
        "title":"Daily high temperature",
        "floor_strike":85.5,
        "cap_strike":86.5,
        "settlement_date":"2026-06-10",
        "rules_primary":"NWS Daily Climate Report KNYC compared with KJFK",
    })
    assert rec.status != CatalogStatus.EXACT
    assert rec.station is None
