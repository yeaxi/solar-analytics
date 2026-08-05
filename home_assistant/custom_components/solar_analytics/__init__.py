"""Solar Analytics Home Assistant integration entry point."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import SolarAnalyticsCoordinator
from .migration import migrate_entry_data

PLATFORMS: tuple[str, ...] = ("sensor", "binary_sensor")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the read-only integration; no services or notifications are registered."""

    hass.data.setdefault(DOMAIN, {})
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate entry metadata to the supported native contract."""
    version, data = migrate_entry_data(entry.version, dict(entry.data))
    if version != entry.version or data != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=data, version=version)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SolarAnalyticsCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    try:
        await coordinator.async_initialize()
        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        await coordinator.async_unload()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if coordinator is not None:
        await coordinator.async_unload()
        await coordinator.async_shutdown()
    return unload_ok
