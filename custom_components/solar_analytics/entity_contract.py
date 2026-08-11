"""Stable entity object IDs required by the existing Solar Analytics dashboard."""

from __future__ import annotations

DASHBOARD_ENTITY_OBJECT_IDS: dict[str, str] = {
    "insight_json": "solar_analytics_solar_insight_json",
    "future_profile": "solar_analytics_solar_future_profile",
    "daily_comparison": "solar_analytics_solar_daily_comparison",
    "accuracy": "solar_analytics_solar_forecast_accuracy",
    "heatmap": "solar_analytics_solar_performance_heatmap",
}

# Existing registry unique IDs are deliberately shorter than their object IDs.
# Changing these would create duplicate `_2` entities during migration.
DASHBOARD_ENTITY_UNIQUE_IDS: dict[str, str] = {
    "insight_json": "solar_analytics_insight_json",
    "future_profile": "solar_analytics_future_profile",
    "daily_comparison": "solar_analytics_daily_comparison",
    "accuracy": "solar_analytics_accuracy",
    "heatmap": "solar_analytics_heatmap",
}
