from datetime import datetime, timezone

import pytest

from weather_alpha.engine.models import ExecutionQuality, ForwardMark, MarketSnapshot, ModelEvaluation, Side, Signal
from weather_alpha.engine.recorder import StateStore
from weather_alpha.research import (
    FeatureDefinition,
    ResearchRunner,
    SignalDefinition,
    SignalRegistry,
    WeatherCompanyResearchBridge,
    build_scorecards,
    default_registry,
)


def test_default_registry_tracks_current_real_signal_families():
    registry = default_registry()
    ids = {row.signal_id for row in registry.signals()}
    assert "settlement_basis_causal_v1" in ids
    assert "weather_company_terminal_high_v1" in ids
    assert "nbm_bucket_baseline_v1" in ids
    assert "nbm_station_bias_v1" in ids
    assert "contrail_q90_prior_evening_v1" in ids
    assert all(not row.live_order_enabled for row in registry.signals())
    assert registry.signal("settlement_basis_causal_v1").master_ids == ("S01", "S02", "S04", "S07", "S08", "S16")
    assert registry.signal("nbm_station_bias_v1").master_ids == ("F04",)


def test_registry_rejects_unknown_feature_dependency():
    registry = SignalRegistry()
    registry.register_feature(FeatureDefinition("known", "test", "unit", "known feature"))
    with pytest.raises(ValueError):
        registry.register_signal(SignalDefinition("bad", "test", "bad", ("missing",)))


def test_research_runner_fails_closed_on_missing_registered_feature(tmp_path):
    registry = SignalRegistry()
    registry.register_feature(FeatureDefinition("needed", "test", "unit", "needed"))
    registry.register_signal(SignalDefinition("demo", "test", "demo", ("needed",)))
    store = StateStore(tmp_path / "state.sqlite")
    runner = ResearchRunner(registry, store)
    market = MarketSnapshot(datetime.now(timezone.utc), "kalshi", "C", "E", yes_ask=0.5)

    class Demo:
        name = "demo"
        def evaluate(self, market, features):  # pragma: no cover - must not be called
            raise AssertionError("missing feature must fail closed before strategy evaluation")

    assert runner.evaluate(Demo(), market, {"needed": None}) == []
    payload = store.db.execute("SELECT payload FROM model_evaluations").fetchone()[0]
    assert "MISSING_REGISTERED_FEATURES:needed" in payload


def test_research_runner_records_canonical_signal(tmp_path):
    registry = SignalRegistry()
    registry.register_feature(FeatureDefinition("needed", "test", "unit", "needed"))
    registry.register_signal(SignalDefinition("demo", "test", "demo", ("needed",)))
    store = StateStore(tmp_path / "state.sqlite")
    runner = ResearchRunner(registry, store)
    now = datetime.now(timezone.utc)
    market = MarketSnapshot(now, "kalshi", "C", "E", yes_ask=0.5)

    class Demo:
        name = "demo"
        def evaluate(self, market, features):
            evaluation = ModelEvaluation(now, "demo", "kalshi", "C", "E", True, True, side=Side.YES, model_probability=0.7)
            signal = Signal(now, "demo", "kalshi", "C", "E", Side.YES, 0.7, 0.5, 0.5, 0.2, 0.2, 0.2, ExecutionQuality.TOUCH_EXECUTION)
            return [signal], evaluation

    rows = runner.evaluate(Demo(), market, {"needed": 1})
    assert len(rows) == 1
    assert store.db.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM model_evaluations").fetchone()[0] == 1


def test_weather_company_bridge_and_economic_scorecard(tmp_path):
    db = tmp_path / "research.sqlite"
    bridge = WeatherCompanyResearchBridge(db)
    row = {
        "event_id": "KXHIGHDEN-26SEP23",
        "ticker": "KXHIGHDEN-26SEP23-T80",
        "station": "KDEN",
        "settlement_date": "2026-09-23",
        "side": "YES",
        "signal_time": "2026-09-23T18:00:00+00:00",
        "signal_entry_ask": "0.40",
        "provisional_probability_side": "0.70",
        "gross_edge": "0.30",
        "high_so_far_f": "80",
        "latest_temp_f": "78",
        "minutes_since_high": "75",
        "drop_from_high_f": "2",
        "slope_15m_f_per_min": "-0.03",
        "price_floor": "0.15",
        "minimum_edge": "0.08",
        "source_family": "WEATHER_COMPANY",
        "model_status": "TEST",
    }
    bridge.record_evaluation(row, emitted=True, reason="PAPER_TRADE_ELIGIBLE")
    signal_id = bridge.record_signal(row, estimated_fee_per_contract=0.01)
    fill = dict(row, engine_signal_id=signal_id, fill_price="0.42", contracts=1,
                estimated_taker_fee="0.01", fill_time="2026-09-23T18:05:00+00:00", fill_model="TEST")
    bridge.record_fill(fill)
    bridge.store.mark(ForwardMark(signal_id, 60, datetime(2026, 9, 23, 18, 6, tzinfo=timezone.utc),
                                  "kalshi", row["ticker"], 0.49, 0.50, 0.50, 0.51, 0.49))
    settled = dict(fill, market_result="YES", settlement_ts="2026-09-24T02:00:00+00:00", fill_key="x")
    assert bridge.record_settlement(settled, {"status": "settled", "expiration_value": 80}) == 1

    cards = build_scorecards(db)
    assert len(cards) == 1
    card = cards[0]
    assert card.strategy == "weather_company_terminal_high_v1"
    assert card.signals == card.fills == card.settled_fills == 1
    assert card.wins == 1 and card.losses == 0
    assert card.independent_dates == 1 and card.stations == 1
    assert card.net_pnl == pytest.approx(0.57)
    assert card.mean_markout_by_horizon[60] == pytest.approx(0.07)
