from datetime import date

from weather_alpha.markets.contracts import ContractShape
from weather_alpha.markets.polymarket_us import PolymarketUSClient


def test_normalizes_polymarket_us_temperature_bucket():
    event = {
        "id": 1,
        "slug": "chicago-high-temp-march-26",
        "title": "Highest temperature in Chicago on March 26, 2026",
        "description": "Daily high temperature market.",
        "startTime": "2026-03-26T00:00:00Z",
        "markets": [{
            "id": 10,
            "slug": "chi-70-71",
            "title": "70-71°F",
            "outcome": "70-71°F",
            "active": True,
            "closed": False,
            "volume": 1000,
            "liquidity": 500,
        }],
    }
    rows = PolymarketUSClient.normalize_events([event])
    assert len(rows) == 1
    row = rows[0]
    assert row.station == "KMDW"
    assert row.settlement_date == date(2026, 3, 26)
    assert row.shape == ContractShape.BUCKET
    assert row.lower_f == 70
    assert row.upper_f == 71
    assert row.weather_event_id == "KMDW_2026-03-26_DAILY_HIGH"


def test_rejects_non_weather_high_word():
    event = {
        "id": 2,
        "slug": "cpi-high",
        "title": "How high will CPI get?",
        "markets": [{"id": 20, "slug": "cpi-45", "title": "4.5 or above", "outcome": "4.5 or above"}],
    }
    assert PolymarketUSClient.normalize_events([event]) == []


def test_rejects_unknown_city_until_station_is_verified():
    event = {
        "id": 3,
        "slug": "phoenix-high-temp",
        "title": "Highest temperature in Phoenix on March 26, 2026",
        "markets": [{"id": 30, "slug": "phx-90-91", "title": "90-91°F", "outcome": "90-91°F"}],
    }
    assert PolymarketUSClient.normalize_events([event]) == []
