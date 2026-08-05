from solar_analytics.entity_contract import DASHBOARD_ENTITY_OBJECT_IDS, DASHBOARD_ENTITY_UNIQUE_IDS


def test_dashboard_compatibility_object_ids_are_stable() -> None:
    assert DASHBOARD_ENTITY_OBJECT_IDS == {
        "insight_json": "solar_analytics_solar_insight_json",
        "future_profile": "solar_analytics_solar_future_profile",
        "daily_comparison": "solar_analytics_solar_daily_comparison",
        "accuracy": "solar_analytics_solar_forecast_accuracy",
        "heatmap": "solar_analytics_solar_performance_heatmap",
    }


def test_dashboard_compatibility_unique_ids_match_live_registry() -> None:
    assert DASHBOARD_ENTITY_UNIQUE_IDS == {
        "insight_json": "solar_analytics_insight_json",
        "future_profile": "solar_analytics_future_profile",
        "daily_comparison": "solar_analytics_daily_comparison",
        "accuracy": "solar_analytics_accuracy",
        "heatmap": "solar_analytics_heatmap",
    }
    values = list(DASHBOARD_ENTITY_OBJECT_IDS.values())
    assert len(values) == len(set(values))
