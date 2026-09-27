from datetime import datetime, timezone

from weather_alpha.engine.models import MarketSnapshot, Side
from weather_alpha.research.settlement import EliminatedBucketNoStrategy, yes_eliminated_by_high_so_far


def test_high_so_far_eliminates_between_and_less_not_greater():
    assert yes_eliminated_by_high_so_far({'strike_type': 'between', 'floor_strike': 80, 'cap_strike': 81}, 82)[0]
    assert yes_eliminated_by_high_so_far({'strike_type': 'less', 'cap_strike': 80}, 80)[0]
    assert not yes_eliminated_by_high_so_far({'strike_type': 'greater', 'floor_strike': 81}, 80)[0]


def test_eliminated_bucket_no_requires_verified_semantics():
    now = datetime.now(timezone.utc)
    market = MarketSnapshot(now, 'kalshi', 'C', 'E', no_ask=0.90,
                            metadata={'strike_type': 'between', 'floor_strike': 80, 'cap_strike': 81,
                                      'station': 'KAAA', 'settlement_date': '2026-09-24'})
    strategy = EliminatedBucketNoStrategy(min_edge=0.02)
    features = {'settlement.high_so_far_f': 82.0, 'market.settlement_semantics_verified': False}
    signals, evaluation = strategy.evaluate(market, features)
    assert signals == [] and evaluation.reason == 'UNVERIFIED_SETTLEMENT_SEMANTICS'
    features['market.settlement_semantics_verified'] = True
    signals, evaluation = strategy.evaluate(market, features)
    assert evaluation.eligible and len(signals) == 1
    assert signals[0].side == Side.NO and signals[0].model_probability == 1.0
