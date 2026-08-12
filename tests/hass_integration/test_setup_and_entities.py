"""End-to-end setup + entity-registry snapshot tests.

Sets up Solar Analytics through the real Home Assistant lifecycle and
snapshots the resulting entity registry. A change to any entity's
unique_id, entity_id, translation_key, device_class, state_class,
entity_category, or enabled-by-default flag fails the snapshot instead of
silently drifting.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from syrupy.assertion import SnapshotAssertion


async def _install_and_await_setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_async_setup_entry_registers_expected_entity_counts(
    hass: HomeAssistant, sa_config_entry
) -> None:
    """After setup, the correct number of sensor + binary_sensor entities exist."""

    await _install_and_await_setup(hass, sa_config_entry)

    registry = er.async_get(hass)
    entries = [
        entry
        for entry in registry.entities.values()
        if entry.config_entry_id == sa_config_entry.entry_id
    ]

    sensors = [entry for entry in entries if entry.entity_id.startswith("sensor.")]
    binary_sensors = [entry for entry in entries if entry.entity_id.startswith("binary_sensor.")]
    assert len(sensors) == 18, "Solar Analytics ships 18 sensor entities"
    assert len(binary_sensors) == 6, "Solar Analytics ships 6 binary_sensor entities"

    # Legacy dashboard-facing unique_ids must survive setup so existing
    # installations keep their entity_ids on upgrade.
    unique_ids = {entry.unique_id for entry in entries}
    for legacy in (
        "solar_analytics_insight_json",
        "solar_analytics_future_profile",
        "solar_analytics_daily_comparison",
        "solar_analytics_accuracy",
        "solar_analytics_heatmap",
    ):
        assert legacy in unique_ids, f"legacy unique_id {legacy!r} must not change"


async def test_entity_registry_snapshot_pins_platinum_attributes(
    hass: HomeAssistant, sa_config_entry, snapshot: SnapshotAssertion
) -> None:
    """Snapshot the entity registry so platinum-tier attributes cannot drift silently."""

    await _install_and_await_setup(hass, sa_config_entry)

    registry = er.async_get(hass)
    entries = sorted(
        (
            entry
            for entry in registry.entities.values()
            if entry.config_entry_id == sa_config_entry.entry_id
        ),
        key=lambda entry: entry.entity_id,
    )

    # Only capture the fields users and HA-Core reviewers actually care about
    # (not the whole registry serialisation which includes volatile IDs).
    shape = [
        {
            "entity_id": entry.entity_id,
            "unique_id": entry.unique_id,
            "platform": entry.platform,
            "translation_key": entry.translation_key,
            "device_class": entry.device_class or entry.original_device_class,
            "entity_category": entry.entity_category,
            "disabled_by": entry.disabled_by,
            "options": entry.capabilities.get("options") if entry.capabilities else None,
            "state_class": (entry.capabilities.get("state_class") if entry.capabilities else None),
        }
        for entry in entries
    ]

    assert shape == snapshot


async def test_diagnostics_snapshot_covers_binding_and_config(
    hass: HomeAssistant, sa_config_entry, snapshot: SnapshotAssertion
) -> None:
    """Snapshot the diagnostics endpoint output shape."""

    from custom_components.solar_analytics.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    await _install_and_await_setup(hass, sa_config_entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, sa_config_entry)

    # Coordinator-derived fields are timing-sensitive; assert their shape and
    # then snapshot only the stable parts.
    assert set(diagnostics.keys()) == {"config_entry", "coordinator", "binding", "payload"}
    stable = {
        "config_entry": {
            key: value for key, value in diagnostics["config_entry"].items() if key != "entry_id"
        },
        "coordinator": {
            key: value
            for key, value in diagnostics["coordinator"].items()
            if key not in {"last_update_success"}
        },
        "binding": diagnostics["binding"],
        "payload_keys": (
            sorted(diagnostics["payload"].keys())
            if isinstance(diagnostics["payload"], dict)
            else None
        ),
    }
    assert stable == snapshot


@pytest.mark.parametrize(
    "expected_translation_key",
    [
        "actual_pv_power",
        "forecast_solar_power",
        "native_modules_power",
        "analysis_status",
        "native_source_status",
        "accuracy",
        "last_updated",
        "pv_performance_analysis_valid",
        "data_quality_problem",
    ],
)
async def test_expected_translation_keys_exist_after_setup(
    hass: HomeAssistant, sa_config_entry, expected_translation_key: str
) -> None:
    """The user-visible entities documented in the README must actually be registered.

    We assert on translation_key rather than entity_id because HA composes the
    entity_id from ``has_entity_name`` + device name + entity name; the
    translation_key is what the source declares and what strings.json binds to.
    """

    await _install_and_await_setup(hass, sa_config_entry)

    registry = er.async_get(hass)
    keys = {
        entry.translation_key
        for entry in registry.entities.values()
        if entry.config_entry_id == sa_config_entry.entry_id
    }
    assert expected_translation_key in keys, (
        f"expected translation_key {expected_translation_key!r} missing after setup; "
        f"registry has {sorted(keys)}"
    )
