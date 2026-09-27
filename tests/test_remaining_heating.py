from datetime import datetime, timezone

from weather_alpha.engine.models import MarketSnapshot
from weather_alpha.research.remaining_heating import EmpiricalRemainingHeatingModel, EmpiricalRemainingHeatingStrategy, contract_contains_value, empirical_bucket_probabilities


def test_remaining_heating_model_is_strictly_prior_only_and_station_specific():
    history = []
    for day in range(1, 41):
        history.append({'station': 'KAAA', 'settlement_date': f'2026-01-{day:02d}' if day <= 31 else f'2026-02-{day-31:02d}',
                        'snapshot_hour_lst': 15, 'high_so_far_f': 80.0, 'official_cli_high_f': 81.0,
                        'minutes_since_high': 30.0, 'drop_from_high_f': 0.5, 'slope_15m_f_per_min': 0.0,
                        'dewpoint_depression_f': 10.0})
        history.append({'station': 'KBBB', 'settlement_date': f'2026-01-{day:02d}' if day <= 31 else f'2026-02-{day-31:02d}',
                        'snapshot_hour_lst': 15, 'high_so_far_f': 90.0, 'official_cli_high_f': 85.0,
                        'minutes_since_high': 30.0, 'drop_from_high_f': 0.5, 'slope_15m_f_per_min': 0.0,
                        'dewpoint_depression_f': 10.0})
    model = EmpiricalRemainingHeatingModel(history, k=30, min_samples=20)
    current = {'station': 'KAAA', 'settlement_date': '2026-03-01', 'snapshot_hour_lst': 15,
               'high_so_far_f': 80.0,
               'minutes_since_high': 30.0, 'drop_from_high_f': 0.5,
               'slope_15m_f_per_min': 0.0, 'dewpoint_depression_f': 10.0}
    pred = model.predict(current)
    assert pred.sample_count == 30
    assert pred.median_residual_f == 1.0
    assert pred.terminal_probability == 0.0


def test_empirical_bucket_probabilities_use_exact_contract_semantics():
    contracts = [
        {'ticker': 'LOW', 'strike_type': 'less', 'cap_strike': 80},
        {'ticker': 'MID', 'strike_type': 'between', 'floor_strike': 80, 'cap_strike': 81},
        {'ticker': 'HIGH', 'strike_type': 'greater', 'floor_strike': 81},
    ]
    probs = empirical_bucket_probabilities(contracts, [79, 80, 81, 82])
    assert probs == {'LOW': 0.25, 'MID': 0.5, 'HIGH': 0.25}
    assert contract_contains_value(contracts[1], 81)



def test_empirical_remaining_heating_strategy_fails_closed_on_wrong_settlement_source():
    market = MarketSnapshot(datetime.now(timezone.utc), 'kalshi', 'C', 'E', yes_ask=0.2, no_ask=0.8,
                            metadata={'strike_type': 'between', 'floor_strike': 80, 'cap_strike': 81})
    features = {
        'forecast.remaining_heating_final_high_samples_f': [80.0] * 40,
        'forecast.remaining_heating_history_n': 40,
        'forecast.remaining_heating_terminal_probability': 0.8,
        'market.settlement_source_family': 'WEATHER_COMPANY',
    }
    signals, evaluation = EmpiricalRemainingHeatingStrategy().evaluate(market, features)
    assert signals == []
    assert 'INCOMPATIBLE_SETTLEMENT_SOURCE:WEATHER_COMPANY' in str(evaluation.reason)
