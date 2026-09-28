from scripts.record_polymarket_us_weather import compact_events


def test_compact_weather_events_preserves_market_quote_fields():
    events = [{
        "id": "e1", "slug": "temp-ny", "title": "Highest temperature in NYC?", "category": "climate",
        "markets": [{
            "id": 10, "slug": "ny-70-71", "title": "70 to 71",
            "status": "active", "active": True, "closed": False,
            "bestBidQuote": {"value": "0.41"}, "bestAskQuote": {"value": "0.44"},
            "lastTradePrice": "0.43", "orderPriceMinTickSize": "0.01",
            "feeCoefficient": "0.02", "minimumTradeQty": "1",
        }],
    }]
    rows = compact_events(events)
    assert len(rows) == 1
    assert rows[0]["event_id"] == "e1"
    assert rows[0]["market_slug"] == "ny-70-71"
    assert rows[0]["best_bid"] == 0.41
    assert rows[0]["best_ask"] == 0.44
    assert rows[0]["last_price"] == 0.43
