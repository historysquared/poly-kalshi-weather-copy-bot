from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from weather_alpha.engine.backtest import SettledTrade, TakerExecution, summarize_settled
from weather_alpha.engine.costs import FeeSchedule, FeeScheduleRouter, expected_value
from weather_alpha.engine.execution import GuardedExecutor, LiveTradingDisabled, LiveTradingSettings, OrderIntent, UnsafeOrder
from weather_alpha.engine.models import ExecutionQuality, MarketSnapshot, Side, Signal
from weather_alpha.engine.recorder import StateStore


def _signal(ts: datetime) -> Signal:
    return Signal(
        timestamp=ts,
        strategy="test",
        venue="kalshi",
        contract_id="KXHIGHNY-TEST",
        weather_event_id="KNYC_2026-06-10_DAILY_HIGH",
        side=Side.YES,
        model_probability=0.70,
        market_probability=0.50,
        executable_price=0.52,
        raw_edge=0.20,
        executable_edge=0.18,
        expected_value_per_contract=0.18,
        execution_quality=ExecutionQuality.L2_EXECUTION,
    )


def test_taker_execution_respects_latency_and_touch():
    t0 = datetime(2026, 6, 10, 19, 0, tzinfo=timezone.utc)
    signal = _signal(t0)
    observations = [
        MarketSnapshot(t0, "kalshi", signal.contract_id, signal.weather_event_id, yes_ask=0.52),
        MarketSnapshot(t0 + timedelta(seconds=30), "kalshi", signal.contract_id, signal.weather_event_id, yes_ask=0.55),
    ]
    fill = TakerExecution(latency_seconds=30, stress_ticks=1, tick_size=0.01).fill(signal, observations)
    assert fill is not None
    assert fill.timestamp == t0 + timedelta(seconds=30)
    assert fill.executable_price == pytest.approx(0.56)


def test_backtest_never_uses_other_contract_or_venue():
    t0 = datetime(2026, 6, 10, 19, 0, tzinfo=timezone.utc)
    signal = _signal(t0)
    observations = [
        MarketSnapshot(t0 + timedelta(seconds=10), "polymarket", signal.contract_id, signal.weather_event_id, yes_ask=0.40),
        MarketSnapshot(t0 + timedelta(seconds=10), "kalshi", "OTHER", signal.weather_event_id, yes_ask=0.40),
    ]
    assert TakerExecution().fill(signal, observations) is None


def test_fee_router_fails_closed_until_verified():
    router = FeeScheduleRouter()
    router.register(FeeSchedule("kalshi", taker_rate=0.07, source="candidate", verified=False))
    with pytest.raises(RuntimeError):
        router.resolve("kalshi")
    schedule = router.resolve("kalshi", allow_unverified=True)
    assert schedule.fee(price=0.5, contracts=1, maker=False) == pytest.approx(0.0175)
    assert expected_value(model_probability=0.7, executable_price=0.5, fee_per_contract=0.0175) == pytest.approx(0.1825)


def test_live_executor_requires_both_gates_and_validates_risk():
    class Client:
        def __init__(self): self.orders = []
        def create_order(self, order): self.orders.append(order); return order
        def cancel_order(self, order_id, **kwargs): return {"cancelled": order_id}

    client = Client()
    settings = LiveTradingSettings(enabled=True, max_contracts_per_order=2)
    executor = GuardedExecutor(client, venue="kalshi", settings=settings, cli_live=False)
    intent = OrderIntent("kalshi", "KXHIGHNY-TEST", Side.YES, 1, 0.40, post_only=True)
    with pytest.raises(LiveTradingDisabled):
        executor.submit(intent)

    live = GuardedExecutor(client, venue="kalshi", settings=settings, cli_live=True)
    result = live.submit(intent)
    assert result["post_only"] is True
    assert result["side"] == "yes"
    with pytest.raises(UnsafeOrder):
        live.submit(OrderIntent("kalshi", "KXHIGHNY-TEST", Side.YES, 3, 0.40))


def test_state_store_reconciles_paper(tmp_path: Path):
    t0 = datetime(2026, 6, 10, 19, 0, tzinfo=timezone.utc)
    signal = _signal(t0)
    store = StateStore(tmp_path / "state.sqlite")
    store.signal(signal)
    fill = TakerExecution().fill(
        signal,
        [MarketSnapshot(t0, "kalshi", signal.contract_id, signal.weather_event_id, yes_ask=0.50)],
        fee=0.01,
    )
    assert fill is not None
    fill_id = store.fill(fill)
    from weather_alpha.engine.models import Settlement
    store.settlement(Settlement("kalshi", signal.contract_id, signal.weather_event_id, Side.YES, t0 + timedelta(hours=1), 82.0))
    assert store.reconcile_paper("kalshi", signal.contract_id) == 1
    pnl = store.db.execute("SELECT pnl FROM paper_pnl WHERE fill_id=?", (fill_id,)).fetchone()[0]
    assert pnl == pytest.approx(0.49)


def test_settled_metrics():
    t0 = datetime(2026, 6, 10, 19, 0, tzinfo=timezone.utc)
    signal = _signal(t0)
    fill = TakerExecution().fill(signal, [MarketSnapshot(t0, "kalshi", signal.contract_id, signal.weather_event_id, yes_ask=0.5)])
    assert fill is not None
    summary = summarize_settled([SettledTrade(signal, fill, True)])
    assert summary["trades"] == 1
    assert summary["wins"] == 1
    assert summary["net_pnl"] == pytest.approx(0.5)
