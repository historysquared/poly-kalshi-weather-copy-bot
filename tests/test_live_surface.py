from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weather_alpha.providers import live_surface
from weather_alpha.providers.live_surface import LiveTemperatureObservation


def obs(station: str, when: datetime, temp: float, source: str = "TEST") -> LiveTemperatureObservation:
    return LiveTemperatureObservation(station, when, temp, source, {})


def test_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHER_LIVE_SURFACE_CACHE_DIR", str(tmp_path))
    now = datetime(2026, 9, 26, 5, 0, tzinfo=timezone.utc)
    rows = [obs("KMIA", now - timedelta(minutes=10), 88.0)]
    live_surface._write_cache("KMIA", rows)
    got = live_surface._read_cache("KMIA", now - timedelta(hours=1), now)
    assert len(got) == 1
    assert got[0].temperature_f == 88.0
    assert got[0].source == "CACHE:TEST"

@pytest.mark.asyncio
async def test_fresh_cache_avoids_iem(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHER_LIVE_SURFACE_CACHE_DIR", str(tmp_path))
    now = datetime(2026, 9, 26, 5, 0, tzinfo=timezone.utc)
    live_surface._write_cache("KNYC", [obs("KNYC", now - timedelta(minutes=2), 64.0)])

    async def empty_awc(*args, **kwargs):
        return []

    async def forbidden_iem(*args, **kwargs):
        raise AssertionError("fresh cache should prevent IEM request")

    monkeypatch.setattr(live_surface, "fetch_awc_metar", empty_awc)
    monkeypatch.setattr(live_surface, "fetch_iem_hourly", forbidden_iem)
    got = await live_surface.fetch_live_temperature_series(
        "KNYC", now - timedelta(hours=2), now, max_age_minutes=100
    )
    assert got[-1].source.startswith("CACHE:")


@pytest.mark.asyncio
async def test_provider_success_refreshes_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHER_LIVE_SURFACE_CACHE_DIR", str(tmp_path))
    now = datetime(2026, 9, 26, 5, 0, tzinfo=timezone.utc)
    async def awc(*args, **kwargs):
        return [obs("KMIA", now - timedelta(minutes=1), 89.0, "AWC_TEST")]

    monkeypatch.setattr(live_surface, "fetch_awc_metar", awc)
    got = await live_surface.fetch_live_temperature_series(
        "KMIA", now - timedelta(hours=2), now, max_age_minutes=100
    )
    assert got[-1].source == "AWC_TEST"
    cached = live_surface._read_cache("KMIA", now - timedelta(hours=2), now)
    assert cached[-1].source == "CACHE:AWC_TEST"
