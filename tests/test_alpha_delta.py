from weather_alpha.alpha_delta import AlphaDeltaEngine, DeltaInputs
from weather_alpha.providers.hrrr import HrrrAwsProvider
from weather_alpha.providers.surface import SurfaceObservation, SurfaceSeries
from datetime import datetime, timedelta, timezone


def test_cloudier_cooling_case_is_negative():
    result = AlphaDeltaEngine().compute(DeltaInputs(
        observed_solar_transmission=0.25,
        hrrr_total_cloud_cover_pct=10.0,
        observed_temperature_f=78.0,
        hrrr_temperature_f=82.0,
        temperature_velocity_f_per_minute=-0.12,
        band13_cold_cloud_fraction=0.40,
        pressure_velocity_mb_per_minute=0.05,
        hours_to_expected_peak=2.0,
        data_age_minutes=2.0,
    ))
    assert result.value < -0.35
    assert result.confidence > 0.5
    assert result.interpretation == "material_downside_to_model_high"


def test_clearer_warming_case_is_positive():
    result = AlphaDeltaEngine().compute(DeltaInputs(
        observed_solar_transmission=0.95,
        hrrr_total_cloud_cover_pct=75.0,
        observed_temperature_f=84.0,
        hrrr_temperature_f=80.0,
        temperature_velocity_f_per_minute=0.15,
        band13_cold_cloud_fraction=0.0,
        pressure_velocity_mb_per_minute=-0.02,
        hours_to_expected_peak=1.5,
        data_age_minutes=1.0,
    ))
    assert result.value > 0.35
    assert result.interpretation == "material_upside_to_model_high"


def test_surface_temperature_velocity():
    t0 = datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc)
    obs = [
        SurfaceObservation("KMDW", t0, 80.0, None, None, None, None, "test"),
        SurfaceObservation("KMDW", t0 + timedelta(minutes=10), 82.0, None, None, None, None, "test"),
    ]
    assert abs(SurfaceSeries.temperature_velocity_f_per_minute(obs, 15) - 0.2) < 1e-9


def test_hrrr_idx_parser_and_key():
    rows = HrrrAwsProvider.parse_idx("1:0:d=2026090718:TMP:2 m above ground:anl:\n2:1000:d=2026090718:TCDC:entire atmosphere:anl:\n")
    assert len(rows) == 2
    assert rows[0].start == 0
    key = HrrrAwsProvider.key(datetime(2026, 9, 7, 18, tzinfo=timezone.utc), 1)
    assert key == "hrrr.20260907/conus/hrrr.t18z.wrfsfcf01.grib2"
