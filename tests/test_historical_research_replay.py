from datetime import datetime, timedelta, timezone

import pytest

from weather_alpha.engine.backtest import TakerExecution
from weather_alpha.engine.models import MarketSnapshot, Settlement, Side
from weather_alpha.engine.recorder import StateStore
from weather_alpha.research.historical import HistoricalResearchCase, HistoricalResearchReplay, kalshi_taker_fee_ceil_cent
from weather_alpha.research.nbm import NbmBucketStrategy
from weather_alpha.research.registry import default_registry


def test_historical_replay_uses_post_latency_touch_and_reconciles(tmp_path):
    t0 = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
    meta = {'strike_type': 'between', 'floor_strike': 80, 'cap_strike': 81,
            'station': 'KAAA', 'settlement_date': '2026-09-01'}
    decision = MarketSnapshot(t0, 'kalshi', 'C', 'E', yes_bid=0.18, yes_ask=0.20, no_bid=0.78, no_ask=0.82, metadata=meta)
    later = MarketSnapshot(t0 + timedelta(seconds=5), 'kalshi', 'C', 'E', yes_bid=0.21, yes_ask=0.22, no_bid=0.77, no_ask=0.79, metadata=meta)
    settle = Settlement('kalshi', 'C', 'E', Side.YES, t0 + timedelta(hours=12), official_settlement_value=81)
    case = HistoricalResearchCase(
        decision,
        {'forecast.nbm_max_f': 80.5, 'forecast.nbm_uncertainty_f': 1.0},
        (decision, later), settle,
    )
    db = tmp_path / 'replay.sqlite'
    replay = HistoricalResearchReplay(default_registry(), StateStore(db), TakerExecution(latency_seconds=5),
                                      lambda price, n: kalshi_taker_fee_ceil_cent(price, n))
    strategy = NbmBucketStrategy(name='nbm_bucket_baseline_v1', min_edge=0.05)
    result = replay.run(strategy, [case])
    assert result.signals == result.fills == result.reconciled_fills == 1
    card = result.scorecards[0]
    assert card.settled_fills == 1 and card.wins == 1
    assert card.capital_at_risk == pytest.approx(0.24)  # 0.22 touch + 0.02 rounded fee
    assert card.net_pnl == pytest.approx(0.76)


def test_kalshi_fee_rounds_up_to_cent():
    assert kalshi_taker_fee_ceil_cent(0.5, 1) == pytest.approx(0.02)
