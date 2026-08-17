"""Forecast-entity provider for Solar Analytics.

The second :class:`~custom_components.solar_analytics.native_adapter.ForecastProfileProvider`
implementation. Where the Energy Dashboard adapter observes an integration's
solar-forecast coordinator, this provider reads a user-selected forecast
*entity* and extracts a timestamped Wh-per-period profile from its state
attributes.

It is read-only: it only calls ``hass.states.get`` and never a service, a
provider HTTP endpoint, or another integration's refresh. It fails closed. An
entity that exposes no timestamped profile yields
``unsupported_forecast_entity_contract`` rather than a fabricated scalar.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_ACTUAL_ENERGY_TODAY,
    CONF_ACTUAL_POWER,
    CONF_FORECAST_ENTITY_ID,
)
from .native import (
    build_generic_model_fingerprint,
    extract_forecast_entity_wh_hours,
    normalize_native_wh_hours,
)
from .native_adapter import (
    NativeBinding,
    NativeModel,
    NativeObservation,
    NativeRead,
    async_single_solar_source,
)

_LOGGER = logging.getLogger(__name__)

# The map values are consumed as Wh per period. A missing unit is accepted
# (unspecified, treated as Wh); any explicit non-Wh unit is rejected fail-closed
# rather than converted, since the profile keys carry no unit of their own.
_ACCEPTED_WH_UNITS = frozenset({"Wh"})


class EntityForecastProvider:
    """Observe a forecast entity and normalize its timestamped profile."""

    source_kind = "forecast_entity"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.binding = NativeBinding("uninitialized")
        self._sequence = 0
        self._last_marker: tuple[str, str] | None = None
        self._observation: NativeObservation | None = None
        self._listener_remove: Any | None = None
        self._capture_task: Any | None = None

    async def async_initialize(self) -> NativeBinding:
        self.binding = await self.async_resolve_binding()
        if self.binding.ready and self.binding.forecast_entity_id:
            self._attach_state_listener(self.binding.forecast_entity_id)
        return self.binding

    def _attach_state_listener(self, entity_id: str) -> None:
        """Observe the forecast entity's own state changes (read-only).

        A day-ahead forecast entity publishes its profile at an arbitrary time
        (often overnight) and then stays quiet. Capturing on the state change
        records the observation at the instant the profile actually appeared, so
        a later scheduled morning snapshot can reuse that earlier observation and
        keep ``observed_at_utc <= scheduled_at_utc``. This never refreshes the
        entity or calls a service; it only reacts to changes Home Assistant
        already emitted.
        """

        if self._listener_remove is not None:
            return
        self._listener_remove = async_track_state_change_event(
            self.hass, [entity_id], self._handle_state_event
        )

    def _handle_state_event(self, _event: Any) -> None:
        if self._capture_task is None or self._capture_task.done():
            self._capture_task = self.hass.async_create_task(self.async_capture())

    async def async_resolve_binding(self) -> NativeBinding:
        """Resolve the forecast entity plus the canonical actual-PV sensors.

        The forecast entity is always user-chosen (it is not part of the Energy
        Dashboard). The actual PV sensors follow the same precedence as the
        Energy adapter: user overrides first, else the Energy Dashboard's single
        solar source.
        """

        data = self.entry.data or {}
        forecast_entity_id = data.get(CONF_FORECAST_ENTITY_ID) or None
        if not forecast_entity_id:
            return NativeBinding("binding_unavailable", reason="forecast_entity_id_missing")

        actual_power_entity = data.get(CONF_ACTUAL_POWER) or None
        actual_energy_entity = data.get(CONF_ACTUAL_ENERGY_TODAY) or None
        if actual_power_entity is None or actual_energy_entity is None:
            source, error = await async_single_solar_source(self.hass)
            if error is not None:
                return NativeBinding(
                    error.status,
                    forecast_entity_id=forecast_entity_id,
                    reason=error.reason,
                )
            if actual_power_entity is None:
                actual_power_entity = source.get("stat_rate") if source else None
            if actual_energy_entity is None:
                actual_energy_entity = source.get("stat_energy_from") if source else None

        if not actual_energy_entity or not isinstance(actual_energy_entity, str):
            return NativeBinding(
                "canonical_actual_mismatch",
                forecast_entity_id=forecast_entity_id,
                reason="actual_energy_entity_missing",
            )
        if not actual_power_entity or not isinstance(actual_power_entity, str):
            return NativeBinding(
                "canonical_actual_mismatch",
                forecast_entity_id=forecast_entity_id,
                reason="actual_power_entity_missing",
            )
        return NativeBinding(
            "ok",
            forecast_entity_id=forecast_entity_id,
            actual_energy_entity=actual_energy_entity,
            actual_power_entity=actual_power_entity,
        )

    async def async_capture(self) -> NativeRead:
        self.binding = await self.async_resolve_binding()
        if not self.binding.ready:
            return NativeRead(self.binding.status, self.binding, reason=self.binding.reason)
        entity_id = self.binding.forecast_entity_id or ""
        state = self.hass.states.get(entity_id)
        if state is None:
            return NativeRead(
                "native_source_unavailable", self.binding, reason="forecast_entity_missing"
            )
        state_value = getattr(state, "state", None)
        if state_value in (None, "", "unknown", "unavailable"):
            return NativeRead(
                "native_source_unavailable", self.binding, reason="forecast_entity_unavailable"
            )
        attributes = dict(getattr(state, "attributes", {}) or {})
        # A restored state (recorder-rehydrated after a Home Assistant restart)
        # is not live evidence: its last_updated is refreshed to boot time while
        # the profile attribute may be hours old. The actual-PV path rejects
        # restored states for the same reason; the forecast path must too.
        if attributes.get("restored") is True:
            return NativeRead(
                "native_source_unavailable", self.binding, reason="forecast_entity_restored"
            )
        payload = extract_forecast_entity_wh_hours(attributes)
        if payload is None:
            return NativeRead(
                "unsupported_forecast_entity_contract",
                self.binding,
                reason="no_timestamped_profile",
            )
        # The accuracy pipeline treats every map value as Wh for the period.
        # A kWh/kW/W entity would silently corrupt accuracy (a kWh map is 1000x
        # too small), so fail closed unless the unit is Wh or unspecified. This
        # never converts: without a verified unit we do not guess.
        unit = attributes.get("unit_of_measurement")
        if unit is not None and unit not in _ACCEPTED_WH_UNITS:
            return NativeRead(
                "unsupported_forecast_entity_contract",
                self.binding,
                reason=f"non_wh_unit:{unit}",
            )
        profile = normalize_native_wh_hours(payload)
        if profile.status != "complete" or profile.payload_sha256 is None:
            return NativeRead(
                "unsupported_forecast_entity_contract",
                self.binding,
                reason=f"profile_validation_failed:invalid_count={profile.invalid_count}",
            )
        now = datetime.now(UTC)
        # Freshness is a property of the profile horizon, not the entity's
        # last_updated. A forecast that still covers a future instant is live
        # even if the entity has not changed for hours (a day-ahead profile is
        # published once and then quiet); conversely a scalar-churning entity
        # does not become fresh just because its state ticked. Admit only while
        # the profile still reaches beyond now.
        horizon_end = max((period.end_utc for period in profile.valid_periods), default=None)
        if horizon_end is None or horizon_end <= now:
            ended = (now - horizon_end).total_seconds() if horizon_end is not None else -1.0
            return NativeRead(
                "native_source_stale",
                self.binding,
                reason=f"forecast_horizon_ended_seconds:{ended:.1f}",
            )
        # The entity's last_updated is retained only as the observation's update
        # timestamp (best available); it is no longer an admission gate.
        updated_at = self._entity_updated_at(state) or now
        model = self._model(attributes)
        if model.status != "ok":
            return NativeRead(model.status, self.binding, model=model, reason=model.reason)
        # Dedupe on the payload digest so scalar churn (a fast-moving state with
        # a slow-moving profile) does not mint a new observation each poll.
        marker = ("payload", profile.payload_sha256)
        if marker == self._last_marker and self._observation is not None:
            return NativeRead("ok", self.binding, model=model, observation=self._observation)
        self._sequence += 1
        observation = NativeObservation(
            profile=profile,
            observed_at_utc=now,
            native_updated_at_utc=updated_at,
            observation_sequence=self._sequence,
            payload_sha256=profile.payload_sha256,
            model=model,
        )
        self._last_marker = marker
        self._observation = observation
        return NativeRead("ok", self.binding, model=model, observation=observation)

    def _model(self, attributes: dict[str, Any]) -> NativeModel:
        values: dict[str, Any] = {
            "status": "ok",
            "forecast_entity_id": self.binding.forecast_entity_id,
            "unit_of_measurement": attributes.get("unit_of_measurement"),
        }
        fingerprint = build_generic_model_fingerprint(values)
        if fingerprint is None:
            return NativeModel(
                "unsupported_forecast_entity_contract", values, None, "model_invalid"
            )
        values["model_fingerprint_sha256"] = fingerprint
        return NativeModel("ok", values, fingerprint)

    @staticmethod
    def _entity_updated_at(state: Any) -> datetime | None:
        for attr in ("last_updated", "last_changed"):
            value = getattr(state, attr, None)
            if isinstance(value, datetime) and value.tzinfo is not None:
                return value.astimezone(UTC)
        return None

    async def async_unload(self) -> None:
        if self._capture_task is not None and not self._capture_task.done():
            self._capture_task.cancel()
        if self._listener_remove is not None:
            self._listener_remove()
            self._listener_remove = None
