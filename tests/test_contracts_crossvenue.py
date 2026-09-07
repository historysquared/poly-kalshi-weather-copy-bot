from weather_alpha.markets.contracts import ContractShape, parse_temperature_outcome
from weather_alpha.markets.cross_venue import VenueQuote, locked_pair_edge, rank_venues


def test_temperature_outcomes():
    assert parse_temperature_outcome("78 to 79") == (ContractShape.BUCKET, 78.0, 79.0)
    assert parse_temperature_outcome("77 or below") == (ContractShape.BELOW, None, 77.0)
    assert parse_temperature_outcome("86 or above") == (ContractShape.ABOVE, 86.0, None)


def test_rank_uses_all_in_executable_price():
    quotes = [
        VenueQuote("kalshi", "k1", "YES", .51, .01, .01),
        VenueQuote("polymarket_us", "p1", "YES", .55, 0, 0),
    ]
    ranked = rank_venues(.65, quotes)
    assert ranked[0].quote.venue == "kalshi"
    assert round(ranked[0].edge, 2) == .12


def test_locked_pair_edge():
    yes = VenueQuote("kalshi", "k", "YES", .44, .01)
    no = VenueQuote("polymarket_us", "p", "NO", .48, .01)
    assert round(locked_pair_edge(yes, no), 2) == .06
