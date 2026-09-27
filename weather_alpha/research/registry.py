from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResearchStage(StrEnum):
    IDEA = "IDEA"
    HISTORICAL = "HISTORICAL"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    CANDIDATE = "CANDIDATE"
    PAUSED = "PAUSED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class FeatureDefinition:
    feature_id: str
    family: str
    source: str
    description: str
    unit: str | None = None
    lookback: str | None = None
    causal: bool = True
    missing_policy: str = "FAIL_CLOSED"
    enabled: bool = True


@dataclass(frozen=True)
class SignalDefinition:
    signal_id: str
    family: str
    description: str
    feature_ids: tuple[str, ...]
    stage: ResearchStage = ResearchStage.HISTORICAL
    venues: tuple[str, ...] = ("kalshi",)
    enabled: bool = True
    live_order_enabled: bool = False
    master_ids: tuple[str, ...] = ()


class SignalRegistry:
    def __init__(self) -> None:
        self._features: dict[str, FeatureDefinition] = {}
        self._signals: dict[str, SignalDefinition] = {}

    def register_feature(self, definition: FeatureDefinition) -> FeatureDefinition:
        if definition.feature_id in self._features:
            raise ValueError(f"duplicate feature_id: {definition.feature_id}")
        self._features[definition.feature_id] = definition
        return definition

    def register_signal(self, definition: SignalDefinition) -> SignalDefinition:
        if definition.signal_id in self._signals:
            raise ValueError(f"duplicate signal_id: {definition.signal_id}")
        missing = [feature_id for feature_id in definition.feature_ids if feature_id not in self._features]
        if missing:
            raise ValueError(f"signal {definition.signal_id} references unknown features: {missing}")
        self._signals[definition.signal_id] = definition
        return definition

    def feature(self, feature_id: str) -> FeatureDefinition:
        return self._features[feature_id]

    def signal(self, signal_id: str) -> SignalDefinition:
        return self._signals[signal_id]

    def features(self, *, enabled_only: bool = False) -> tuple[FeatureDefinition, ...]:
        rows = self._features.values()
        if enabled_only:
            rows = (row for row in rows if row.enabled)
        return tuple(sorted(rows, key=lambda row: row.feature_id))

    def signals(self, *, enabled_only: bool = False) -> tuple[SignalDefinition, ...]:
        rows = self._signals.values()
        if enabled_only:
            rows = (row for row in rows if row.enabled)
        return tuple(sorted(rows, key=lambda row: row.signal_id))

    def validate_feature_payload(self, signal_id: str, features: dict[str, object]) -> tuple[str, ...]:
        definition = self.signal(signal_id)
        missing: list[str] = []
        for feature_id in definition.feature_ids:
            feature = self.feature(feature_id)
            if feature_id not in features:
                missing.append(feature_id)
            elif features[feature_id] is None and feature.missing_policy == "FAIL_CLOSED":
                missing.append(feature_id)
        return tuple(missing)


def default_registry() -> SignalRegistry:
    registry = SignalRegistry()
    for feature in (
        FeatureDefinition("surface.high_so_far_f", "surface", "ASOS/METAR", "Settlement-window high observed so far", "F"),
        FeatureDefinition("surface.latest_temp_f", "surface", "ASOS/METAR", "Latest causal station temperature", "F"),
        FeatureDefinition("surface.minutes_since_high", "surface", "ASOS/METAR", "Minutes since latest occurrence of high so far", "minutes"),
        FeatureDefinition("surface.drop_from_high_f", "surface", "ASOS/METAR", "Current temperature drop from high so far", "F"),
        FeatureDefinition("surface.slope_15m_f_per_min", "surface", "ASOS/METAR", "Causal 15-minute temperature slope", "F/min", "15m"),
        FeatureDefinition("forecast.nbm_max_f", "forecast", "NOAA NBM", "Pre-decision NBM daily maximum forecast", "F"),
        FeatureDefinition("forecast.nbm_uncertainty_f", "forecast", "NOAA NBM", "NBM uncertainty/spread proxy used by current baseline", "F"),
        FeatureDefinition("forecast.nbm_residual_history_n", "forecast", "prior resolved history", "Prior-only station residual sample count", "count", "rolling"),
        FeatureDefinition("forecast.nbm_residual_median_f", "forecast", "prior resolved history", "Prior-only rolling median actual-minus-NBM residual", "F", "rolling"),
        FeatureDefinition("forecast.nbm_residual_mae_f", "forecast", "prior resolved history", "Prior-only rolling NBM residual MAE", "F", "rolling"),
        FeatureDefinition("forecast.nbm_residual_samples_f", "forecast", "prior resolved history", "Prior-only station residual samples for empirical bucket distribution", "F", "rolling"),
        FeatureDefinition("forecast.nbm_adjusted_max_f", "forecast", "NOAA NBM + prior residuals", "NBM maximum after prior-only station residual adjustment", "F"),
        FeatureDefinition("forecast.remaining_heating_history_n", "forecast", "prior ASOS/CLI history", "Prior-only empirical remaining-heating sample count", "count", "rolling"),
        FeatureDefinition("forecast.remaining_heating_terminal_probability", "forecast", "prior ASOS/CLI history", "Prior-only probability no higher official high remains", "probability", "rolling"),
        FeatureDefinition("forecast.remaining_heating_final_high_samples_f", "forecast", "prior ASOS/CLI history", "Empirical final-high samples implied by causal current surface state", "F", "rolling"),
        FeatureDefinition("contrail.prior_evening_count", "contrail", "Google Contrails", "Prior local-day 18:00-21:00 detection count", "detections", "3h"),
        FeatureDefinition("contrail.city_rolling_q90", "contrail", "Google Contrails", "Rolling 90th percentile from that city only", "detections"),
        FeatureDefinition("market.executable_ask", "market", "venue order book", "Executable ask for the candidate side", "probability"),
        FeatureDefinition("market.settlement_semantics_verified", "market", "venue rule audit", "Exact station/source/window/bucket semantics verified", "bool"),
        FeatureDefinition("market.settlement_source_family", "market", "venue rule audit", "Detected official settlement source family", "category"),
        FeatureDefinition("settlement.high_so_far_f", "settlement", "ASOS/CLI reconstruction", "Causal high so far in the exact settlement window", "F"),
        FeatureDefinition("settlement.empirical_basis_f", "settlement", "historical ASOS/CLI", "Prior-only empirical CLI-minus-public-feed basis samples", "F"),
    ):
        registry.register_feature(feature)

    registry.register_signal(SignalDefinition(
        "weather_company_terminal_high_v1",
        "terminal_high",
        "Existing Weather Company forward terminal-high paper heuristic",
        (
            "surface.high_so_far_f", "surface.latest_temp_f", "surface.minutes_since_high",
            "surface.drop_from_high_f", "surface.slope_15m_f_per_min", "market.executable_ask",
        ),
        stage=ResearchStage.PAPER,
        master_ids=("F14", "F15", "M01"),
    ))
    for signal_id, description, stage in (
        ("weather_company_terminal_high_A_control_v1", "Frozen A control forward-paper terminal-high track", ResearchStage.PAPER),
        ("weather_company_terminal_high_B_moderate_v1", "B moderate forward-paper terminal-high track", ResearchStage.PAPER),
        ("weather_company_terminal_high_C_exploratory_v1", "C exploratory forward-paper terminal-high track", ResearchStage.PAPER),
        ("weather_company_terminal_high_D_diagnostic_v1", "Diagnostic no-lock terminal-high track; never production eligible", ResearchStage.PAPER),
    ):
        registry.register_signal(SignalDefinition(
            signal_id,
            "terminal_high",
            description,
            (
                "surface.high_so_far_f", "surface.latest_temp_f", "surface.minutes_since_high",
                "surface.drop_from_high_f", "surface.slope_15m_f_per_min", "market.executable_ask",
            ),
            stage=stage,
            master_ids=("F14", "F15", "M01"),
        ))

    registry.register_signal(SignalDefinition(
        "settlement_basis_causal_v1",
        "settlement",
        "Causal settlement-basis boundary strategy using prior-only CLI/public-feed reconstruction",
        ("settlement.high_so_far_f", "settlement.empirical_basis_f", "market.executable_ask"),
        stage=ResearchStage.HISTORICAL,
        master_ids=("S01", "S02", "S04", "S07", "S08", "S16"),
    ))
    registry.register_signal(SignalDefinition(
        "settlement_eliminated_bucket_no_v1",
        "settlement",
        "Buy NO only after causal high-so-far has physically eliminated the YES bucket",
        ("settlement.high_so_far_f", "market.settlement_semantics_verified"),
        stage=ResearchStage.HISTORICAL,
        master_ids=("S16",),
    ))
    registry.register_signal(SignalDefinition(
        "nbm_bucket_baseline_v1",
        "forecast",
        "NBM daily-high baseline mapped to exact venue buckets",
        ("forecast.nbm_max_f", "forecast.nbm_uncertainty_f"),
        stage=ResearchStage.HISTORICAL,
        master_ids=("F01",),
    ))
    registry.register_signal(SignalDefinition(
        "nbm_empirical_residual_v1",
        "forecast",
        "NBM exact-bucket distribution from prior-only empirical station residual samples",
        (
            "forecast.nbm_max_f", "forecast.nbm_residual_history_n",
            "forecast.nbm_residual_samples_f",
        ),
        stage=ResearchStage.HISTORICAL,
        master_ids=("F02",),
    ))
    registry.register_signal(SignalDefinition(
        "nbm_station_bias_v1",
        "forecast",
        "NBM daily-high bucket model with prior-only rolling station residual correction",
        (
            "forecast.nbm_max_f", "forecast.nbm_uncertainty_f",
            "forecast.nbm_residual_history_n", "forecast.nbm_residual_median_f",
            "forecast.nbm_residual_mae_f", "forecast.nbm_adjusted_max_f",
        ),
        stage=ResearchStage.HISTORICAL,
        master_ids=("F04",),
    ))
    registry.register_signal(SignalDefinition(
        "terminal_high_empirical_v2",
        "terminal_high",
        "Prior-only empirical remaining-heating distribution mapped to exact temperature buckets",
        (
            "surface.high_so_far_f", "surface.latest_temp_f", "surface.minutes_since_high",
            "surface.drop_from_high_f", "surface.slope_15m_f_per_min",
            "forecast.remaining_heating_history_n", "forecast.remaining_heating_terminal_probability",
            "forecast.remaining_heating_final_high_samples_f", "market.settlement_source_family",
        ),
        stage=ResearchStage.HISTORICAL,
        master_ids=("F14", "F15", "M01", "M07"),
    ))
    registry.register_signal(SignalDefinition(
        "contrail_q90_prior_evening_v1",
        "contrail",
        "City-specific rolling-q90 prior-evening contrail research signal",
        (
            "forecast.nbm_max_f", "forecast.nbm_uncertainty_f",
            "contrail.prior_evening_count", "contrail.city_rolling_q90",
        ),
        stage=ResearchStage.HISTORICAL,
        master_ids=("M23",),
    ))
    return registry
