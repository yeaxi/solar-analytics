"""Deterministic, dependency-free Solar Analytics primitives."""

from .analytics import (
    AccuracyMetrics,
    DailyMetric,
    ForecastPoint,
    IntervalMetric,
    Recommendation,
    ValidityContext,
    aggregate_power_samples,
    build_consensus,
    compute_accuracy,
    compute_baseline,
    detect_anomalies,
    evaluate_validity,
    generate_recommendations,
    normalize_forecast_result,
    resample_forecast,
)
from .storage import SolarAnalyticsStore

__all__ = [
    "AccuracyMetrics",
    "DailyMetric",
    "ForecastPoint",
    "IntervalMetric",
    "Recommendation",
    "SolarAnalyticsStore",
    "ValidityContext",
    "aggregate_power_samples",
    "build_consensus",
    "compute_accuracy",
    "compute_baseline",
    "detect_anomalies",
    "evaluate_validity",
    "generate_recommendations",
    "normalize_forecast_result",
    "resample_forecast",
]
