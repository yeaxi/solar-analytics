"""Config and options flow for the reusable Solar Analytics integration.

The config flow lets a user bind Solar Analytics to any Forecast.Solar config
entry and any pair of PV power/energy sensors, with sensible auto-detection
from the Home Assistant Energy Dashboard when the user leaves a selector
blank. Every input has a UI description that explains what the field means
and what safety/runtime effect changing it has (see :mod:`strings.json`).

Solar Analytics is a read-only integration; the config flow itself performs
no service calls, no provider requests, and no refreshes. Validation only
inspects Home Assistant state and the Energy Dashboard configuration.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    ConfigEntrySelector,
    ConfigEntrySelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ACTUAL_ENERGY_TODAY,
    CONF_ACTUAL_POWER,
    CONF_DAY_AHEAD_HOUR,
    CONF_FORECAST_ENTITY_ID,
    CONF_FORECAST_SOURCE_TYPE,
    CONF_MORNING_HOUR,
    CONF_NATIVE_FORECAST_ENTRY_ID,
    CONF_TIME_ZONE,
    DEFAULT_DAY_AHEAD_HOUR,
    DEFAULT_MORNING_HOUR,
    DOMAIN,
    FORECAST_SOURCE_ENERGY_ENTRY,
    FORECAST_SOURCE_ENTITY,
    NAME,
)

_LOGGER = logging.getLogger(__name__)

_POWER_UNITS = frozenset({"W", "kW"})
_ENERGY_UNITS = frozenset({"Wh", "kWh"})


def _user_schema(hass: HomeAssistant, defaults: Mapping[str, Any] | None) -> vol.Schema:
    """Build the shared schema used by both the user and reconfigure steps.

    All selectors are optional. Leaving the two entity selectors and the
    Forecast.Solar entry selector blank triggers auto-detection from the
    Energy Dashboard; providing them locks Solar Analytics to specific
    inputs even if the Energy Dashboard changes later.
    """

    defaults = dict(defaults or {})
    default_tz = defaults.get(CONF_TIME_ZONE) or hass.config.time_zone or "UTC"
    default_morning = int(defaults.get(CONF_MORNING_HOUR, DEFAULT_MORNING_HOUR))
    default_day_ahead = int(defaults.get(CONF_DAY_AHEAD_HOUR, DEFAULT_DAY_AHEAD_HOUR))

    schema: dict[Any, Any] = {}
    if default_actual_power := defaults.get(CONF_ACTUAL_POWER):
        schema[vol.Optional(CONF_ACTUAL_POWER, default=default_actual_power)] = EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="power")
        )
    else:
        schema[vol.Optional(CONF_ACTUAL_POWER)] = EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="power")
        )

    if default_actual_energy := defaults.get(CONF_ACTUAL_ENERGY_TODAY):
        schema[vol.Optional(CONF_ACTUAL_ENERGY_TODAY, default=default_actual_energy)] = (
            EntitySelector(EntitySelectorConfig(domain="sensor", device_class="energy"))
        )
    else:
        schema[vol.Optional(CONF_ACTUAL_ENERGY_TODAY)] = EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="energy")
        )

    source_type_default = defaults.get(CONF_FORECAST_SOURCE_TYPE) or FORECAST_SOURCE_ENERGY_ENTRY
    schema[vol.Optional(CONF_FORECAST_SOURCE_TYPE, default=source_type_default)] = SelectSelector(
        SelectSelectorConfig(
            mode=SelectSelectorMode.DROPDOWN,
            options=[
                SelectOptionDict(
                    value=FORECAST_SOURCE_ENERGY_ENTRY,
                    label="Energy Dashboard solar-forecast integration",
                ),
                SelectOptionDict(value=FORECAST_SOURCE_ENTITY, label="Forecast entity"),
            ],
        )
    )

    if default_native_entry := defaults.get(CONF_NATIVE_FORECAST_ENTRY_ID):
        schema[vol.Optional(CONF_NATIVE_FORECAST_ENTRY_ID, default=default_native_entry)] = (
            ConfigEntrySelector(ConfigEntrySelectorConfig(integration="forecast_solar"))
        )
    else:
        schema[vol.Optional(CONF_NATIVE_FORECAST_ENTRY_ID)] = ConfigEntrySelector(
            ConfigEntrySelectorConfig(integration="forecast_solar")
        )

    if default_forecast_entity := defaults.get(CONF_FORECAST_ENTITY_ID):
        schema[vol.Optional(CONF_FORECAST_ENTITY_ID, default=default_forecast_entity)] = (
            EntitySelector(EntitySelectorConfig(domain="sensor"))
        )
    else:
        schema[vol.Optional(CONF_FORECAST_ENTITY_ID)] = EntitySelector(
            EntitySelectorConfig(domain="sensor")
        )

    schema[vol.Optional(CONF_TIME_ZONE, default=default_tz)] = TextSelector(
        TextSelectorConfig(type=TextSelectorType.TEXT)
    )
    schema[vol.Optional(CONF_MORNING_HOUR, default=default_morning)] = NumberSelector(
        NumberSelectorConfig(min=0, max=23, step=1, mode=NumberSelectorMode.BOX)
    )
    schema[vol.Optional(CONF_DAY_AHEAD_HOUR, default=default_day_ahead)] = NumberSelector(
        NumberSelectorConfig(min=0, max=23, step=1, mode=NumberSelectorMode.BOX)
    )
    return vol.Schema(schema)


def _validate_time_zone(value: Any) -> str | None:
    """Return a validated IANA timezone name or ``None`` if invalid."""

    if not isinstance(value, str) or not value:
        return None
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError:
        return None
    return value


def _validate_snapshot_hour(value: Any) -> int | None:
    """Return an integer 0-23, or ``None`` if the input is out of range."""

    try:
        hour = int(value)
    except TypeError, ValueError:
        return None
    return hour if 0 <= hour <= 23 else None


def _validate_power_entity(hass: HomeAssistant, entity_id: str | None) -> bool:
    """Return ``True`` iff the sensor looks like a PV power sensor."""

    if not entity_id:
        return True  # blank means "auto-detect"; validated at runtime by the adapter
    state = hass.states.get(entity_id)
    if state is None:
        return False
    attrs = state.attributes or {}
    if attrs.get("device_class") != "power":
        return False
    return attrs.get("unit_of_measurement") in _POWER_UNITS


def _validate_energy_entity(hass: HomeAssistant, entity_id: str | None) -> bool:
    """Return ``True`` iff the sensor looks like a PV energy counter."""

    if not entity_id:
        return True
    state = hass.states.get(entity_id)
    if state is None:
        return False
    attrs = state.attributes or {}
    if attrs.get("device_class") != "energy":
        return False
    return attrs.get("unit_of_measurement") in _ENERGY_UNITS


def _validate_native_entry(hass: HomeAssistant, entry_id: str | None) -> bool:
    """Return ``True`` iff the forecast config entry exists.

    Any integration that feeds the Energy Dashboard solar forecast is accepted
    (Forecast.Solar, Solcast, ...); the runtime adapter resolves the provider
    from the entry's own domain. Blank means auto-detect from the Energy
    Dashboard.
    """

    if not entry_id:
        return True
    return hass.config_entries.async_get_entry(entry_id) is not None


def _validate_forecast_entity(hass: HomeAssistant, entity_id: str | None) -> bool:
    """Return ``True`` iff the forecast entity exists.

    Required when the source type is ``forecast_entity``; the attribute-schema
    check happens fail-closed at runtime, since a freshly added entity may not
    have published its forecast attributes yet.
    """

    if not entity_id:
        return False
    return hass.states.get(entity_id) is not None


async def _validate_user_input(
    hass: HomeAssistant, user_input: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate a user submission from either the user or reconfigure step."""

    errors: dict[str, str] = {}
    cleaned: dict[str, Any] = {}

    actual_power = user_input.get(CONF_ACTUAL_POWER) or None
    if not _validate_power_entity(hass, actual_power):
        errors[CONF_ACTUAL_POWER] = "invalid_actual_power_entity"
    elif actual_power:
        cleaned[CONF_ACTUAL_POWER] = actual_power

    actual_energy = user_input.get(CONF_ACTUAL_ENERGY_TODAY) or None
    if not _validate_energy_entity(hass, actual_energy):
        errors[CONF_ACTUAL_ENERGY_TODAY] = "invalid_actual_energy_entity"
    elif actual_energy:
        cleaned[CONF_ACTUAL_ENERGY_TODAY] = actual_energy

    native_entry = user_input.get(CONF_NATIVE_FORECAST_ENTRY_ID) or None
    if not _validate_native_entry(hass, native_entry):
        errors[CONF_NATIVE_FORECAST_ENTRY_ID] = "invalid_native_forecast_entry"
    elif native_entry:
        cleaned[CONF_NATIVE_FORECAST_ENTRY_ID] = native_entry

    source_type = user_input.get(CONF_FORECAST_SOURCE_TYPE) or FORECAST_SOURCE_ENERGY_ENTRY
    if source_type not in (FORECAST_SOURCE_ENERGY_ENTRY, FORECAST_SOURCE_ENTITY):
        errors[CONF_FORECAST_SOURCE_TYPE] = "invalid_forecast_source_type"
    else:
        cleaned[CONF_FORECAST_SOURCE_TYPE] = source_type

    forecast_entity = user_input.get(CONF_FORECAST_ENTITY_ID) or None
    if source_type == FORECAST_SOURCE_ENTITY:
        if not _validate_forecast_entity(hass, forecast_entity):
            errors[CONF_FORECAST_ENTITY_ID] = "invalid_forecast_entity"
        elif forecast_entity:
            cleaned[CONF_FORECAST_ENTITY_ID] = forecast_entity
    elif forecast_entity:
        cleaned[CONF_FORECAST_ENTITY_ID] = forecast_entity

    tz = _validate_time_zone(user_input.get(CONF_TIME_ZONE))
    if tz is None:
        errors[CONF_TIME_ZONE] = "invalid_time_zone"
    else:
        cleaned[CONF_TIME_ZONE] = tz

    morning = _validate_snapshot_hour(user_input.get(CONF_MORNING_HOUR, DEFAULT_MORNING_HOUR))
    if morning is None:
        errors[CONF_MORNING_HOUR] = "invalid_snapshot_hour"
    else:
        cleaned[CONF_MORNING_HOUR] = morning

    day_ahead = _validate_snapshot_hour(user_input.get(CONF_DAY_AHEAD_HOUR, DEFAULT_DAY_AHEAD_HOUR))
    if day_ahead is None:
        errors[CONF_DAY_AHEAD_HOUR] = "invalid_snapshot_hour"
    else:
        cleaned[CONF_DAY_AHEAD_HOUR] = day_ahead

    return cleaned, errors


class SolarAnalyticsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Reusable, single-instance config flow for Solar Analytics."""

    VERSION = 6

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial user step."""

        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors = await _validate_user_input(self.hass, user_input)
            if not errors:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=NAME, data=cleaned)
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(self.hass, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Allow the user to change any config-flow field after setup."""

        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors = await _validate_user_input(self.hass, user_input)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data=cleaned, reason="reconfigure_successful"
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_user_schema(self.hass, user_input or entry.data),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the (deliberately empty) options flow."""

        return SolarAnalyticsOptionsFlow()


class SolarAnalyticsOptionsFlow(config_entries.OptionsFlow):
    """Options are intentionally empty; any real change creates a new lineage."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="init", data_schema=vol.Schema({}))
