"""Solar Analytics Home Assistant integration entry point.

Solar Analytics is a read-only custom integration. It never registers
services, opens sockets, or triggers refreshes on other integrations. The
entry point wires up config-entry migration, one coordinator per config
entry (there is only ever one), and platform forwarding.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import SolarAnalyticsCoordinator
from .migration import migrate_entry_data

# Type alias for the ConfigEntry with its runtime_data payload. Modern HA
# encourages this pattern (>= 2024.10) and it makes type-checkers happy
# wherever we accept ``entry.runtime_data``.
type SolarAnalyticsConfigEntry = ConfigEntry[SolarAnalyticsCoordinator]

PLATFORMS: tuple[str, ...] = ("sensor", "binary_sensor")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the read-only integration. No services, no notifications."""

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate entry data forward to the current schema version."""

    version, data = migrate_entry_data(entry.version, dict(entry.data))
    if version != entry.version or data != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=data, version=version)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: SolarAnalyticsConfigEntry
) -> bool:
    """Set up one Solar Analytics config entry."""

    coordinator = SolarAnalyticsCoordinator(hass, entry)
    entry.runtime_data = coordinator
    try:
        await coordinator.async_initialize()
        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.async_unload()
        raise
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SolarAnalyticsConfigEntry
) -> bool:
    """Tear down one Solar Analytics config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator: SolarAnalyticsCoordinator | None = getattr(entry, "runtime_data", None)
    if coordinator is not None:
        await coordinator.async_unload()
        await coordinator.async_shutdown()
    return unload_ok
