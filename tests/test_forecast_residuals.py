from datetime import datetime, timezone

import pytest

from weather_alpha.engine.models import MarketSnapshot, Side
from weather_alpha.research.forecast_residuals import build_prior_only_residual_calibration
from weather_alpha.research.nbm import EmpiricalNbmResidualStrategy, NbmBucketStrategy, exact_bucket_probability


def test_residual_calibration_is_prior_only_and_uses_city_series():
    rows = [
        {'location_key': 'a', 'station': 'KAAA', 'date': f'2026-01-{d:02d}', 'nbm_max_f': 70, 'actual_high_f': 71}
        for d in range(1, 6)
    ] + [
        {'location_key': 'b', 'station': 'KBBB', 'date': f'2026-01-{d:02d}', 'nbm_max_f': 80, 'actual_high_f': 75}
        for d in range(1, 6)
    ]
    out = build_prior_only_residual_calibration(rows, lookback_days=30, min_history=3)
    a = [r for r in out if r.location_key == 'a']
    assert [r.history_n for r in a] == [0, 1, 2, 3, 4]
    assert a[3].bias_median_f == pytest.approx(1.0)
    assert a[3].adjusted_max_f == pytest.approx(71.0)
    assert a[0].adjusted_max_f is None


def test_exact_bucket_probability_respects_kalshi_integer_boundaries():
    between = {'strike_type': 'between', 'floor_strike': 80, 'cap_strike': 81}
    less = {'strike_type': 'less', 'cap_strike': 80}
    greater = {'strike_type': 'greater', 'floor_strike': 81}
    assert exact_bucket_probability(between, 80.5, 1.0) > 0.6
    assert exact_bucket_probability(less, 79.0, 1.0) > 0.6
    assert exact_bucket_probability(greater, 83.0, 1.0) > 0.6


def test_nbm_bias_strategy_emits_only_with_prior_history_and_edge():
    now = datetime.now(timezone.utc)
    market = MarketSnapshot(now, 'kalshi', 'C', 'E', yes_ask=0.20, no_ask=0.82,
                            metadata={'strike_type': 'between', 'floor_strike': 80, 'cap_strike': 81,
                                      'station': 'KAAA', 'settlement_date': '2026-09-24'})
    features = {
        'forecast.nbm_max_f': 81.0, 'forecast.nbm_uncertainty_f': 1.0,
        'forecast.nbm_residual_history_n': 40, 'forecast.nbm_residual_median_f': -0.5,
        'forecast.nbm_residual_mae_f': 1.2, 'forecast.nbm_adjusted_max_f': 80.5,
    }
    strategy = NbmBucketStrategy(name='nbm_station_bias_v1', min_edge=0.05, min_history=30, use_station_bias=True)
    signals, evaluation = strategy.evaluate(market, features)
    assert evaluation.eligible and signals
    assert signals[0].side == Side.YES
    features['forecast.nbm_residual_history_n'] = 10
    signals, evaluation = strategy.evaluate(market, features)
    assert signals == [] and not evaluation.eligible
    assert 'INSUFFICIENT_PRIOR_HISTORY' in str(evaluation.reason)



def test_empirical_nbm_residual_strategy_uses_prior_samples_directly():
    now = datetime.now(timezone.utc)
    market = MarketSnapshot(now, 'kalshi', 'C', 'E', yes_ask=0.20, no_ask=0.82,
                            metadata={'strike_type': 'between', 'floor_strike': 80, 'cap_strike': 81,
                                      'station': 'KAAA', 'settlement_date': '2026-09-24'})
    features = {
        'forecast.nbm_max_f': 80.0,
        'forecast.nbm_residual_history_n': 40,
        'forecast.nbm_residual_samples_f': [0.0, 1.0] * 20,
    }
    strategy = EmpiricalNbmResidualStrategy(min_history=30, min_edge=0.05)
    signals, evaluation = strategy.evaluate(market, features)
    assert evaluation.eligible and signals
    assert signals[0].side == Side.YES
    assert signals[0].model_probability == pytest.approx(1.0)
