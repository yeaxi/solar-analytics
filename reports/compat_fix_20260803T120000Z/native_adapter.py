"""Fail-closed adapter for the pinned native Forecast.Solar Energy contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import importlib
import inspect
import logging
from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACTUAL_ENERGY_TODAY,
    CONF_ACTUAL_POWER,
    CONF_NATIVE_FORECAST_ENTRY_ID,
    DEFAULT_ENTITIES,
)
from .native import (
    NATIVE_ADAPTER_VERSION,
    NATIVE_CONTRACT_VERSION,
    NativeProfile,
    build_native_model_fingerprint,
    normalize_native_wh_hours,
)

_LOGGER = logging.getLogger(__name__)
UTC = timezone.utc
TARGET_CORE_VERSION = "2026.7.4"
MAX_OBSERVATION_AGE = timedelta(hours=2)


@dataclass(frozen=True)
class NativeBinding:
    """Exact Energy Dashboard binding and its validation result."""

    status: str
    native_entry_id: str | None = None
    actual_energy_entity: str | None = None
    actual_power_entity: str | None = None
    reason: str | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ok" and bool(self.native_entry_id)


@dataclass(frozen=True)
class NativeModel:
    """Non-secret native model identity."""

    status: str
    values: dict[str, Any]
    fingerprint: str | None
    reason: str | None = None


@dataclass(frozen=True)
class NativeObservation:
    """One adapter-observed successful native coordinator update."""

    profile: NativeProfile
    observed_at_utc: datetime
    native_updated_at_utc: datetime
    observation_sequence: int
    payload_sha256: str
    model: NativeModel


@dataclass(frozen=True)
class NativeRead:
    """Read result exposed to the coordinator without raising for source faults."""

    status: str
    binding: NativeBinding
    model: NativeModel | None = None
    observation: NativeObservation | None = None
    reason: str | None = None


class ForecastSolarNativeAdapter:
    """Observe, validate and normalize the native Energy Dashboard profile.

    This class never polls Forecast.Solar directly and never asks the native
    coordinator to refresh. The only detailed profile call is the pinned HA helper
    used by the Energy Dashboard itself.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.binding = NativeBinding("uninitialized")
        self._helper: Any | None = None
        self._native_listener_remove: Any | None = None
        self._last_marker: tuple[str, str] | None = None
        self._observation: NativeObservation | None = None
        self._sequence = 0
        self._capture_task: Any | None = None

    async def async_initialize(self) -> NativeBinding:
        """Resolve the exact Energy Dashboard source and attach observation listener."""

        self.binding = await self.async_resolve_binding()
        if self.binding.ready:
            stored = self.entry.data.get(CONF_NATIVE_FORECAST_ENTRY_ID)
            if not stored:
                # Initial binding is persisted only after exactly one Energy
                # Dashboard source has been verified. A later changed binding is
                # rejected rather than silently rebound.
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    data={**self.entry.data, CONF_NATIVE_FORECAST_ENTRY_ID: self.binding.native_entry_id},
                )
            self._attach_native_listener()
        return self.binding

    async def async_resolve_binding(self) -> NativeBinding:
        """Read Energy preferences and enforce one exact Forecast.Solar entry."""

        try:
            energy_data = importlib.import_module("homeassistant.components.energy.data")
            manager = await energy_data.async_get_manager(self.hass)
        except Exception as err:  # noqa: BLE001 - capability boundary is fail-closed
            return NativeBinding("unsupported_native_contract", reason=f"energy_manager_unavailable:{type(err).__name__}")

        preferences = getattr(manager, "data", None)
        sources = preferences.get("energy_sources") if isinstance(preferences, Mapping) else None
        if not isinstance(sources, list):
            return NativeBinding("binding_unavailable", reason="energy_sources_missing")
        solar_sources = [source for source in sources if isinstance(source, Mapping) and source.get("type") == "solar"]
        if len(solar_sources) != 1:
            return NativeBinding("binding_ambiguous", reason=f"solar_source_count:{len(solar_sources)}")
        source = solar_sources[0]
        entry_ids = source.get("config_entry_solar_forecast")
        if not isinstance(entry_ids, list) or len(entry_ids) != 1 or not isinstance(entry_ids[0], str):
            return NativeBinding("binding_ambiguous", reason="config_entry_solar_forecast_not_exactly_one")
        native_entry_id = entry_ids[0]
        stored = self.entry.data.get(CONF_NATIVE_FORECAST_ENTRY_ID)
        if stored and stored != native_entry_id:
            return NativeBinding("binding_changed", native_entry_id=native_entry_id, reason="stored_binding_mismatch")
        if source.get("stat_energy_from") != DEFAULT_ENTITIES[CONF_ACTUAL_ENERGY_TODAY]:
            return NativeBinding("canonical_actual_mismatch", native_entry_id=native_entry_id, reason="energy_entity_mismatch")
        if source.get("stat_rate") != DEFAULT_ENTITIES[CONF_ACTUAL_POWER]:
            return NativeBinding("canonical_actual_mismatch", native_entry_id=native_entry_id, reason="power_entity_mismatch")
        config_entry = self.hass.config_entries.async_get_entry(native_entry_id)
        if config_entry is None or getattr(config_entry, "domain", None) != "forecast_solar":
            return NativeBinding("native_entry_unavailable", native_entry_id=native_entry_id, reason="entry_missing_or_wrong_domain")
        return NativeBinding(
            "ok",
            native_entry_id=native_entry_id,
            actual_energy_entity=DEFAULT_ENTITIES[CONF_ACTUAL_ENERGY_TODAY],
            actual_power_entity=DEFAULT_ENTITIES[CONF_ACTUAL_POWER],
        )

    def _attach_native_listener(self) -> None:
        """Observe native coordinator updates without initiating one."""

        if not self.binding.native_entry_id:
            return
        native_entry = self.hass.config_entries.async_get_entry(self.binding.native_entry_id)
        runtime = getattr(native_entry, "runtime_data", None) if native_entry else None
        add_listener = getattr(runtime, "async_add_listener", None)
        if not callable(add_listener):
            return

        def listener(*_args: Any) -> None:
            if self._capture_task is None or self._capture_task.done():
                self._capture_task = self.hass.async_create_task(self.async_capture())

        self._native_listener_remove = add_listener(listener)
        if self._native_listener_remove is not None:
            self.entry.async_on_unload(self._native_listener_remove)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            text = value.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)

    def _core_version_supported(self) -> bool:
        try:
            constants = importlib.import_module("homeassistant.const")
            version = str(getattr(constants, "__version__", ""))
        except Exception:  # pragma: no cover - import boundary
            return False
        return version == TARGET_CORE_VERSION

    def _get_helper(self) -> Any | None:
        if not self._core_version_supported():
            return None
        if self._helper is not None:
            return self._helper
        try:
            module = importlib.import_module("homeassistant.components.forecast_solar.energy")
            helper = getattr(module, "async_get_solar_forecast")
            signature = inspect.signature(helper)
            parameters = list(signature.parameters.values())
            if len(parameters) != 2 or [parameter.name for parameter in parameters] != ["hass", "config_entry_id"]:
                return None
            if any(parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD} for parameter in parameters):
                return None
        except (ImportError, AttributeError, TypeError, ValueError):
            return None
        self._helper = helper
        return helper

    def _native_entry_and_runtime(self) -> tuple[Any | None, Any | None]:
        if not self.binding.native_entry_id:
            return None, None
        native_entry = self.hass.config_entries.async_get_entry(self.binding.native_entry_id)
        if native_entry is None or getattr(native_entry, "domain", None) != "forecast_solar":
            return None, None
        runtime = getattr(native_entry, "runtime_data", None)
        return native_entry, runtime

    @staticmethod
    def _model_from_entry(native_entry: Any) -> NativeModel:
        try:
            data = dict(getattr(native_entry, "data", {}) or {})
            options = dict(getattr(native_entry, "options", {}) or {})
            get_planes = getattr(native_entry, "get_subentries_of_type", None)
            planes = list(get_planes("plane")) if callable(get_planes) else []
            if len(planes) != 1:
                return NativeModel("unsupported_native_contract", {}, None, f"plane_count:{len(planes)}")
            plane = planes[0]
            plane_data = dict(getattr(plane, "data", {}) or {})
            values: dict[str, Any] = {
                "status": "ok",
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "declination": plane_data.get("declination"),
                "azimuth": plane_data.get("azimuth"),
                "modules_power_w": plane_data.get("modules_power"),
                "inverter_size_w": options.get("inverter_size"),
                "morning_damping": options.get("damping_morning", 0.0),
                "evening_damping": options.get("damping_evening", 0.0),
                "plane_id": getattr(plane, "subentry_id", None),
                # Only presence is observed; credential values never enter data.
                "auth_mode": "authenticated" if "api_key" in options or "api_key" in data else "public",
            }
            fingerprint = build_native_model_fingerprint(values)
            if fingerprint is None:
                return NativeModel("unsupported_native_contract", values, None, "invalid_model_values")
            values["model_fingerprint_sha256"] = fingerprint
            return NativeModel("ok", values, fingerprint)
        except (AttributeError, TypeError, ValueError):
            return NativeModel("unsupported_native_contract", {}, None, "native_entry_shape_invalid")

    async def async_capture(self) -> NativeRead:
        """Capture a successful native observation already held by the coordinator."""

        self.binding = await self.async_resolve_binding()
        if not self.binding.ready:
            return NativeRead(self.binding.status, self.binding, reason=self.binding.reason)
        helper = self._get_helper()
        if helper is None:
            return NativeRead("unsupported_native_contract", self.binding, reason="helper_import_or_signature")
        native_entry, runtime = self._native_entry_and_runtime()
        if native_entry is None or runtime is None:
            return NativeRead("native_source_unavailable", self.binding, reason="entry_unloaded")
        runtime_data = getattr(runtime, "data", None)
        wh_period = getattr(runtime_data, "wh_period", None)
        if not isinstance(wh_period, Mapping):
            return NativeRead("unsupported_native_contract", self.binding, reason="runtime_wh_period_missing")
        if getattr(runtime, "last_update_success", False) is not True:
            return NativeRead("native_source_unavailable", self.binding, reason="last_update_not_successful")
        native_updated_at = self._parse_datetime(getattr(runtime, "last_update_success_time", None))
        if native_updated_at is None:
            return NativeRead("native_source_unavailable", self.binding, reason="native_success_time_missing")
        now = datetime.now(UTC)
        age = (now - native_updated_at).total_seconds()
        if age < -300 or age > MAX_OBSERVATION_AGE.total_seconds():
            return NativeRead("native_source_stale", self.binding, reason=f"native_update_age_seconds:{age:.1f}")
        try:
            payload = await helper(self.hass, self.binding.native_entry_id)
        except Exception as err:  # noqa: BLE001 - source boundary is diagnostic
            _LOGGER.debug("Native Forecast.Solar helper failed: %s", err)
            return NativeRead("native_source_unavailable", self.binding, reason=f"helper_error:{type(err).__name__}")
        if not isinstance(payload, Mapping):
            return NativeRead("unsupported_native_contract", self.binding, reason="helper_payload_not_mapping")
        profile = normalize_native_wh_hours(payload)
        if profile.status != "complete" or profile.payload_sha256 is None:
            return NativeRead("unsupported_native_contract", self.binding, reason="wh_hours_validation_failed")
        model = self._model_from_entry(native_entry)
        if model.status != "ok":
            return NativeRead(model.status, self.binding, model=model, reason=model.reason)
        marker = (native_updated_at.isoformat(), profile.payload_sha256)
        if marker == self._last_marker and self._observation is not None:
            return NativeRead("ok", self.binding, model=model, observation=self._observation)
        self._sequence += 1
        observation = NativeObservation(
            profile=profile,
            observed_at_utc=now,
            native_updated_at_utc=native_updated_at,
            observation_sequence=self._sequence,
            payload_sha256=profile.payload_sha256,
            model=model,
        )
        self._last_marker = marker
        self._observation = observation
        return NativeRead("ok", self.binding, model=model, observation=observation)

    async def async_unload(self) -> None:
        """Cancel adapter-owned resources."""

        if self._capture_task is not None and not self._capture_task.done():
            self._capture_task.cancel()
        if self._native_listener_remove is not None:
            self._native_listener_remove()
            self._native_listener_remove = None
