"""Minimal, non-divergent config flow for Solar Analytics v2."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_TIME_ZONE, DEFAULT_TIME_ZONE, DOMAIN


class SolarAnalyticsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Bind automatically to the one Energy Dashboard native Forecast.Solar source."""

    VERSION = 4

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            await self.async_set_unique_id("solar_analytics")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Solar Analytics",
                data={CONF_TIME_ZONE: DEFAULT_TIME_ZONE},
            )
        # There are deliberately no selectable entity fields: v2 uses the exact
        # Energy Dashboard binding and canonical actual IDs, so a user form cannot
        # create a second or divergent provider path. The translated form
        # description explains the automatic binding and fail-closed behaviour.
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return SolarAnalyticsOptionsFlow(config_entry)


class SolarAnalyticsOptionsFlow(config_entries.OptionsFlow):
    """Options are intentionally empty; model/threshold drift creates a lineage."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="init", data_schema=vol.Schema({}))
