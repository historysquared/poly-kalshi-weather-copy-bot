from __future__ import annotations

from dataclasses import dataclass
from math import exp


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


@dataclass(frozen=True)
class DeltaWeights:
    solar_mismatch: float = 0.38
    temperature_velocity: float = 0.24
    temperature_error: float = 0.23
    cloud_structure: float = 0.10
    pressure_tendency: float = 0.05

    def normalized(self) -> "DeltaWeights":
        total = self.solar_mismatch + self.temperature_velocity + self.temperature_error + self.cloud_structure + self.pressure_tendency
        if total <= 0:
            raise ValueError("delta weights must have positive sum")
        return DeltaWeights(*(v / total for v in (
            self.solar_mismatch,
            self.temperature_velocity,
            self.temperature_error,
            self.cloud_structure,
            self.pressure_tendency,
        )))


@dataclass(frozen=True)
class DeltaInputs:
    # Satellite-derived transmission: 1=clear/high transmission, 0=opaque cloud.
    observed_solar_transmission: float | None
    # HRRR model cloud cover, 0..100. Converted to a coarse expected transmission.
    hrrr_total_cloud_cover_pct: float | None
    observed_temperature_f: float | None
    hrrr_temperature_f: float | None
    temperature_velocity_f_per_minute: float | None
    band13_cold_cloud_fraction: float | None
    pressure_velocity_mb_per_minute: float | None
    hours_to_expected_peak: float
    data_age_minutes: float = 0.0


@dataclass(frozen=True)
class AlphaDelta:
    value: float
    confidence: float
    solar_component: float
    velocity_component: float
    temperature_error_component: float
    cloud_structure_component: float
    pressure_component: float
    peak_opportunity: float
    interpretation: str


class AlphaDeltaEngine:
    """Transparent model-vs-physical-reality weather alpha score.

    Sign convention:
      +1 -> observations imply hotter / higher daily maximum than HRRR baseline.
      -1 -> observations imply cooler / lower daily maximum than HRRR baseline.

    This score is *not* itself a probability. It is an explanatory feature to be
    calibrated historically into changes in the daily-high distribution.
    """

    def __init__(self, weights: DeltaWeights | None = None) -> None:
        self.weights = (weights or DeltaWeights()).normalized()

    @staticmethod
    def _peak_opportunity(hours_to_peak: float) -> float:
        # Cloud/heating mismatch has greatest relevance in the several hours before
        # the expected daily peak. After the peak, reduce but do not zero the signal.
        h = float(hours_to_peak)
        if h >= 0:
            return _clip(0.35 + 0.65 * (1.0 - exp(-h / 2.0)), 0.35, 1.0)
        return _clip(exp(h / 2.0), 0.10, 1.0)

    @staticmethod
    def _model_transmission(tcdc_pct: float) -> float:
        # Coarse mapping only. Historical calibration should replace/tune it.
        c = _clip(tcdc_pct / 100.0, 0.0, 1.0)
        return _clip(1.0 - 0.75 * c, 0.05, 1.0)

    def compute(self, x: DeltaInputs) -> AlphaDelta:
        available = 0
        solar = velocity = temp_error = cloud = pressure = 0.0
        peak = self._peak_opportunity(x.hours_to_expected_peak)

        if x.observed_solar_transmission is not None and x.hrrr_total_cloud_cover_pct is not None:
            # Positive if satellite is clearer than modeled; negative if cloudier.
            model_t = self._model_transmission(x.hrrr_total_cloud_cover_pct)
            solar = _clip((float(x.observed_solar_transmission) - model_t) / 0.35) * peak
            available += 1

        if x.temperature_velocity_f_per_minute is not None:
            # +/-0.20 F/min (~3 F per 15 min) saturates the feature.
            velocity = _clip(float(x.temperature_velocity_f_per_minute) / 0.20)
            available += 1

        if x.observed_temperature_f is not None and x.hrrr_temperature_f is not None:
            temp_error = _clip((float(x.observed_temperature_f) - float(x.hrrr_temperature_f)) / 4.0)
            available += 1

        if x.band13_cold_cloud_fraction is not None:
            # Deep/cold cloud suppresses heating; this is intentionally asymmetric.
            cloud = -_clip(float(x.band13_cold_cloud_fraction) / 0.50, 0.0, 1.0) * peak
            available += 1

        if x.pressure_velocity_mb_per_minute is not None:
            # A rapid pressure rise is weak evidence of post-frontal cooling/peak completion.
            pressure = -_clip(float(x.pressure_velocity_mb_per_minute) / 0.08)
            available += 1

        w = self.weights
        raw = (
            w.solar_mismatch * solar
            + w.temperature_velocity * velocity
            + w.temperature_error * temp_error
            + w.cloud_structure * cloud
            + w.pressure_tendency * pressure
        )
        value = _clip(raw)

        completeness = available / 5.0
        freshness = exp(-max(0.0, float(x.data_age_minutes)) / 15.0)
        confidence = _clip(completeness * freshness, 0.0, 1.0)

        if value <= -0.35:
            label = "material_downside_to_model_high"
        elif value >= 0.35:
            label = "material_upside_to_model_high"
        elif value < -0.10:
            label = "mild_downside_to_model_high"
        elif value > 0.10:
            label = "mild_upside_to_model_high"
        else:
            label = "near_model_baseline"

        return AlphaDelta(
            value=value,
            confidence=confidence,
            solar_component=solar,
            velocity_component=velocity,
            temperature_error_component=temp_error,
            cloud_structure_component=cloud,
            pressure_component=pressure,
            peak_opportunity=peak,
            interpretation=label,
        )
