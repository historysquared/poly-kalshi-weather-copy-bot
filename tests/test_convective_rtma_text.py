from datetime import datetime, timezone

from weather_alpha.providers.convective import convective_cooling_score, haversine_km
from weather_alpha.providers.rtma_ru import RtmaRuProvider
from weather_alpha.providers.text_alpha import score_forecaster_intent


def test_haversine_zero():
    assert haversine_km(41.0, -87.0, 41.0, -87.0) == 0.0


def test_convective_score_increases_for_close_upwind_storm():
    score, upwind = convective_cooling_score(
        precip_core_distance_km=5.0,
        precip_core_bearing_deg=270.0,
        surface_wind_from_deg=265.0,
        max_reflectivity_dbz=50.0,
        lightning_flashes_10km_5m=8,
        mrms_precip_rate_mm_h=12.0,
    )
    assert upwind is True
    assert score > 0.5


def test_rtma_url_uses_quarter_hour_cycle():
    obj = RtmaRuProvider.object_for(datetime(2026, 8, 13, 0, 37, tzinfo=timezone.utc))
    assert "rtma2p5_ru.20260813" in obj.grib_url
    assert "t0030z.2dvaranl_ndfd.grb2" in obj.grib_url


def test_afd_intent_detects_cooler_forecaster_change():
    intent = score_forecaster_intent(
        "LOT",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        "We lowered afternoon highs due to persistent stratus and delayed clearing.",
    )
    assert intent.temperature_bias < 0
    assert intent.cloud_bias < 0
    assert intent.confidence > 0
