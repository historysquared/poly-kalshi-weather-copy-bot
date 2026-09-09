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
