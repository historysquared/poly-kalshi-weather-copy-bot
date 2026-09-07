from weather_alpha.backtest.tournament import (
    ContractSnapshot, adjacent_yes_basket, blind_no, late_elimination_no, model_no, summarize
)


def snap(**kw):
    base = dict(venue="poly", event_id="e1", market_id="m1", timestamp_ms=1,
                lower_f=84.0, upper_f=85.0, yes_ask=0.08, no_ask=0.93,
                fair_yes=0.03, resolved_yes=False, observed_high_f=86.0)
    base.update(kw)
    return ContractSnapshot(**base)


def test_blind_no_can_have_bad_ev_despite_high_win_rate():
    t = blind_no(snap(no_ask=0.95), min_no_price=0.90, max_no_price=0.99)
    assert t is not None and t.pnl_per_share == 0.05


def test_model_no_requires_edge():
    assert model_no(snap(no_ask=0.95, fair_yes=0.01), min_edge=0.03) is not None
    assert model_no(snap(no_ask=0.95, fair_yes=0.04), min_edge=0.03) is None


def test_late_elimination_requires_observed_high_above_bucket():
    assert late_elimination_no(snap(observed_high_f=86.0, upper_f=85.0, no_ask=0.98)) is not None
    assert late_elimination_no(snap(observed_high_f=85.0, upper_f=85.0, no_ask=0.98)) is None


def test_adjacent_basket_finds_contiguous_edge():
    rows = [
        snap(market_id="a", lower_f=82, upper_f=83, yes_ask=0.10, fair_yes=0.14, resolved_yes=False),
        snap(market_id="b", lower_f=84, upper_f=85, yes_ask=0.25, fair_yes=0.40, resolved_yes=True),
        snap(market_id="c", lower_f=86, upper_f=87, yes_ask=0.30, fair_yes=0.34, resolved_yes=False),
    ]
    result = adjacent_yes_basket(rows, min_edge=0.05)
    assert result is not None
    trades, edge = result
    assert edge > 0.20
    assert any(t.resolved_win for t in trades)


def test_summary_groups_strategy():
    trades = [blind_no(snap(no_ask=0.95)), blind_no(snap(market_id="m2", no_ask=0.96))]
    rows = summarize([t for t in trades if t])
    assert rows[0].trades == 2
    assert rows[0].wins == 2
