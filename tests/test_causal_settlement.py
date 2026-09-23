from datetime import datetime, timedelta, timezone
from decimal import Decimal

from weather_alpha.backtest.causal_settlement import (
    TimedTemperature,
    contract_contains,
    empirical_settlement_posterior,
    lock_gate,
    surface_state,
)


def test_surface_state_uses_only_observations_at_or_before_snapshot():
    t0 = datetime(2026, 6, 10, 18, 0, tzinfo=timezone.utc)
    obs = [
        TimedTemperature(t0, Decimal("80.1")),
        TimedTemperature(t0 + timedelta(minutes=30), Decimal("81.4")),
        TimedTemperature(t0 + timedelta(minutes=90), Decimal("79.9")),
        TimedTemperature(t0 + timedelta(minutes=120), Decimal("90.0")),  # future; must not leak
    ]
    state = surface_state(obs, as_of=t0 + timedelta(minutes=90))
    assert state.high_so_far_f == Decimal("81.4")
    assert state.latest_temp_f == Decimal("79.9")
    assert state.observations_used == 3


def test_lock_gate_requires_stale_high_and_drop():
    t0 = datetime(2026, 6, 10, 18, 0, tzinfo=timezone.utc)
    obs = [
        TimedTemperature(t0, Decimal("82.1")),
        TimedTemperature(t0 + timedelta(minutes=70), Decimal("80.8")),
    ]
    state = surface_state(obs, as_of=t0 + timedelta(minutes=70))
    ok, reasons = lock_gate(state)
    assert ok
    assert reasons == ()


def test_empirical_posterior_preserves_decimal_boundaries():
    samples = [Decimal("-0.4")] * 10 + [Decimal("0.0")] * 10 + [Decimal("0.6")] * 10
    post = empirical_settlement_posterior(
        high_so_far_f=Decimal("81.6"),
        prior_basis_samples_f=samples,
        shape="bucket",
        lower=Decimal("81.5"),
        upper=Decimal("82.4"),
        minimum_samples=20,
    )
    assert post.sample_count == 30
    assert post.probability_yes == Decimal(20) / Decimal(30)


def test_contract_contains_exact_decimal_edges():
    assert contract_contains("bucket", Decimal("81.5"), Decimal("82.4"), Decimal("81.5"))
    assert contract_contains("bucket", Decimal("81.5"), Decimal("82.4"), Decimal("82.4"))
    assert not contract_contains("bucket", Decimal("81.5"), Decimal("82.4"), Decimal("82.4001"))
