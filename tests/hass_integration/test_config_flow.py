"""End-to-end config-flow tests against a real Home Assistant."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType


@pytest.mark.asyncio
async def test_user_step_creates_entry_from_full_selection(
    hass: HomeAssistant, forecast_solar_entry, energy_manager_installed, actual_pv_states
) -> None:
    """Filling every field explicitly (no auto-detect) creates the config entry."""

    result = await hass.config_entries.flow.async_init(
        "solar_analytics", context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "actual_power_entity": "sensor.example_pv_power",
            "actual_energy_today_entity": "sensor.example_pv_energy",
            "native_forecast_entry_id": forecast_solar_entry.entry_id,
            "time_zone": "Europe/Berlin",
            "morning_snapshot_hour": 6,
            "day_ahead_snapshot_hour": 23,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Solar Analytics"
    entry_data = result["data"]
    assert entry_data["actual_power_entity"] == "sensor.example_pv_power"
    assert entry_data["actual_energy_today_entity"] == "sensor.example_pv_energy"
    assert entry_data["native_forecast_entry_id"] == forecast_solar_entry.entry_id
    assert entry_data["time_zone"] == "Europe/Berlin"
    assert entry_data["morning_snapshot_hour"] == 6
    assert entry_data["day_ahead_snapshot_hour"] == 23


@pytest.mark.asyncio
async def test_user_step_rejects_invalid_timezone(
    hass: HomeAssistant, forecast_solar_entry, energy_manager_installed, actual_pv_states
) -> None:
    """A non-IANA timezone must fail closed with a structured error key."""

    result = await hass.config_entries.flow.async_init(
        "solar_analytics", context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "actual_power_entity": "sensor.example_pv_power",
            "actual_energy_today_entity": "sensor.example_pv_energy",
            "native_forecast_entry_id": forecast_solar_entry.entry_id,
            "time_zone": "Not/A_Zone",
            "morning_snapshot_hour": 6,
            "day_ahead_snapshot_hour": 23,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"time_zone": "invalid_time_zone"}


@pytest.mark.asyncio
async def test_second_user_flow_is_aborted_by_unique_id(
    hass: HomeAssistant, sa_config_entry
) -> None:
    """Solar Analytics is single-instance; a second user flow must abort."""

    sa_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        "solar_analytics", context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "actual_power_entity": "sensor.example_pv_power",
            "actual_energy_today_entity": "sensor.example_pv_energy",
            "native_forecast_entry_id": sa_config_entry.data["native_forecast_entry_id"],
            "time_zone": "Europe/Berlin",
            "morning_snapshot_hour": 6,
            "day_ahead_snapshot_hour": 23,
        },
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
