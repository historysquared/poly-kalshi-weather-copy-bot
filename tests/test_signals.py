from weather_alpha.models import MarketQuote
from weather_alpha.signals import signal_from_probability

def market(**kw):
    base = dict(ticker="X", event_ticker="E", title="T", subtitle="S", floor_strike=90, cap_strike=None,
                yes_bid=.49, yes_ask=.51, no_bid=.49, no_ask=.51, volume=100, close_time=None, updated_time=None)
    base.update(kw)
    return MarketQuote(**base)

def test_yes_signal():
    s = signal_from_probability(market(), .70, min_edge=.05)
    assert s and s.side == "yes" and s.edge > .18

def test_no_signal_when_edge_small():
    assert signal_from_probability(market(), .52, min_edge=.05) is None
