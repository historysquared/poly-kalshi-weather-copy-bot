from datetime import date

from weather_alpha.markets.kalshi_weather_resolver import resolve_weather_rules, ticker_event_date


def test_ticker_date_is_only_a_consistency_check():
    assert ticker_event_date("KXHIGHCHI-26JUN11") == date(2026, 6, 11)
    ev = resolve_weather_rules(
        {"ticker": "KXHIGHCHI-26JUN11-T86", "event_ticker": "KXHIGHCHI-26JUN11", "rules_primary": "Temperature contract"},
        {"event_ticker": "KXHIGHCHI-26JUN11"},
        {"ticker": "KXHIGHCHI", "settlement_sources": [{"name": "National Weather Service CLI at KORD"}]},
    )
    assert ev.station == "KORD"
    assert ev.settlement_date is None
    assert not ev.exact


def test_exact_requires_official_date_station_source_and_ticker_consistency():
    ev = resolve_weather_rules(
        {"ticker": "KXHIGHCHI-26JUN11-T86", "event_ticker": "KXHIGHCHI-26JUN11"},
        {"event_ticker": "KXHIGHCHI-26JUN11", "settlement_date": "2026-06-11"},
        {"ticker": "KXHIGHCHI", "settlement_sources": [{"name": "National Weather Service Daily Climate Report (CLI) KORD"}]},
    )
    assert ev.exact
    assert ev.station == "KORD"
    assert ev.settlement_date == date(2026, 6, 11)
    assert ev.ticker_date_matches is True


def test_conflicting_official_and_ticker_date_fails_closed():
    ev = resolve_weather_rules(
        {"ticker": "KXHIGHCHI-26JUN11-T86", "event_ticker": "KXHIGHCHI-26JUN11"},
        {"event_ticker": "KXHIGHCHI-26JUN11", "settlement_date": "2026-06-12"},
        {"ticker": "KXHIGHCHI", "settlement_sources": ["NWS CLI KORD"]},
    )
    assert not ev.exact
    assert ev.ticker_date_matches is False


def test_natural_language_rule_date_and_station_name_are_authoritative_evidence():
    market = {
        "ticker": "KXHIGHCHI-26JUN11-B86.5",
        "event_ticker": "KXHIGHCHI-26JUN11",
        "rules_primary": (
            "If the highest temperature recorded at Chicago Midway, IL for June 11, 2026, "
            "is between 86-87 degrees according to the National Weather Service's "
            "Climatological Report (Daily), then the market resolves to Yes."
        ),
    }
    ev = resolve_weather_rules(market, {"event_ticker": "KXHIGHCHI-26JUN11"}, {"ticker": "KXHIGHCHI"})
    assert ev.exact
    assert ev.station == "KMDW"
    assert ev.settlement_date == date(2026, 6, 11)
    assert ev.ticker_date_matches is True
    assert any("Chicago Midway" in x for x in ev.station_evidence)


def test_nws_cli_source_url_issuedby_resolves_station():
    ev = resolve_weather_rules(
        {
            "ticker": "KXHIGHCHI-26JUN11-T86",
            "event_ticker": "KXHIGHCHI-26JUN11",
            "rules_primary": "Highest temperature for June 11, 2026 according to NWS Climatological Report (Daily).",
            "settlement_sources": [{"name": "NWS Climatological Report Chicago Midway", "url": "https://forecast.weather.gov/product.php?site=LOT&product=CLI&issuedby=MDW"}],
        },
        {"event_ticker": "KXHIGHCHI-26JUN11"},
        {"ticker": "KXHIGHCHI"},
    )
    assert ev.exact
    assert ev.station == "KMDW"


def test_current_series_drift_cannot_rewrite_historical_event_semantics():
    ev = resolve_weather_rules(
        {
            "ticker": "KXHIGHCHI-26JUN11-T86",
            "event_ticker": "KXHIGHCHI-26JUN11",
            "title": "Highest temperature in Chicago on June 11, 2026?",
        },
        {"event_ticker": "KXHIGHCHI-26JUN11"},
        {
            "ticker": "KXHIGHCHI",
            "last_updated_ts": "2026-09-04T13:37:29Z",
            "settlement_sources": [{"name": "National Weather Service CLI Chicago Midway"}],
        },
    )
    assert ev.station == "KMDW"
    assert ev.settlement_date == date(2026, 6, 11)
    assert ev.series_drift_risk
    assert not ev.exact
    assert any("post-dates event" in reason for reason in ev.reasons)
