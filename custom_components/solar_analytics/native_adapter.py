"""Fail-closed adapter for the pinned native Forecast.Solar Energy contract."""

from __future__ import annotations

import importlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACTUAL_ENERGY_TODAY,
    CONF_ACTUAL_POWER,
    CONF_NATIVE_FORECAST_ENTRY_ID,
)
from .native import (
    NativeProfile,
    build_generic_model_fingerprint,
    build_native_model_fingerprint,
    normalize_native_wh_hours,
)

_LOGGER = logging.getLogger(__name__)
# Minimum supported Home Assistant Core version. The native adapter also
# feature-detects the Forecast.Solar helper signature and the presence of
# ``wh_period`` on the coordinator runtime, so a patch-level bump within the
# supported line does not require a new integration release.
TARGET_CORE_MIN_VERSION: tuple[int, ...] = (2026, 7)
MAX_OBSERVATION_AGE = timedelta(hours=2)


def _version_tuple(raw: str) -> tuple[int, ...]:
    """Best-effort ``"YYYY.M[.P][suffix]"`` -> ``(YYYY, M, P)`` conversion.

    Returns ``(0,)`` for anything unparseable, which makes the caller treat
    the running HA as older than any supported minimum.
    """

    if not raw:
        return (0,)
    stripped = raw.split("+", 1)[0].split("-", 1)[0]
    parts: list[int] = []
    for token in stripped.split("."):
        digits = ""
        for character in token:
            if character.isdigit():
                digits += character
            else:
                break
        if digits:
            parts.append(int(digits))
        else:
            break
    return tuple(parts) if parts else (0,)


@dataclass(frozen=True)
class NativeBinding:
    """Resolved forecast-source binding and its validation result.

    ``native_entry_id`` identifies an Energy Dashboard forecast config entry;
    ``forecast_entity_id`` identifies a forecast entity. Exactly one is set for
    a ready binding, depending on the configured source type.
    """

    status: str
    native_entry_id: str | None = None
    actual_energy_entity: str | None = None
    actual_power_entity: str | None = None
    reason: str | None = None
    forecast_entity_id: str | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ok" and bool(self.native_entry_id or self.forecast_entity_id)


@dataclass(frozen=True)
class NativeModel:
    """Non-secret native model identity."""

    status: str
    values: dict[str, Any]
    fingerprint: str | None
    reason: str | None = None


@dataclass(frozen=True)
class NativeObservation:
    """One adapter-observed successful native coordinator update.

    ``native_updated_at_utc`` is the local timestamp when the native
    coordinator listener fired. Forecast.Solar 2026.7.4 does not expose a
    provider-issued update timestamp.
    """

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


@runtime_checkable
class ForecastProfileProvider(Protocol):
    """Provider-neutral contract the coordinator consumes.

    Every forecast source (the Energy Dashboard adapter below, or the forecast
    entity adapter in :mod:`forecast_source`) resolves a binding, observes a
    profile without ever provoking the source, and reports a lineage
    ``source_kind`` so the store keeps sources separate. Liveness is provider
    defined: an Energy provider watches its coordinator, an entity provider
    watches the entity's ``last_updated``.
    """

    source_kind: str
    binding: NativeBinding

    async def async_initialize(self) -> NativeBinding: ...

    async def async_capture(self) -> NativeRead: ...

    async def async_unload(self) -> None: ...


class ForecastSolarNativeAdapter:
    """Observe, validate and normalize an Energy Dashboard solar-forecast profile.

    Works with any Home Assistant integration that provides the Energy
    Dashboard solar-forecast platform (``<domain>/energy.py`` exposing
    ``async_get_solar_forecast``). Forecast.Solar is one such integration;
    Solcast is another. This class never polls the provider directly and never
    asks its coordinator to refresh. The only detailed profile call is the
    pinned HA helper the Energy Dashboard itself uses.

    Forecast.Solar keeps its exact model fingerprint and ``wh_period`` liveness
    gate so existing installs never start a new lineage. Other providers use a
    generic config fingerprint and rely on the coordinator's own
    ``last_update_success`` and listener signal.
    """

    source_kind = "native"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.binding = NativeBinding("uninitialized")
        self._helper: Any | None = None
        self._helper_domain: str | None = None
        self._native_listener_remove: Any | None = None
        self._native_listener_runtime: Any | None = None
        self._last_marker: tuple[str, str] | None = None
        self._observation: NativeObservation | None = None
        self._sequence = 0
        self._capture_task: Any | None = None
        # Forecast.Solar 2026.7.4 uses plain DataUpdateCoordinator and has no
        # native success timestamp. This is deliberately local evidence only.
        self._native_listener_observed_at_utc: datetime | None = None

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
                    data={
                        **self.entry.data,
                        CONF_NATIVE_FORECAST_ENTRY_ID: self.binding.native_entry_id,
                    },
                )
            self._attach_native_listener()
        return self.binding

    async def async_resolve_binding(self) -> NativeBinding:
        """Resolve which Forecast.Solar entry and actual-PV sensors to bind to.

        Precedence:

        1. Values chosen by the user in the config flow (``entry.data`` keys
           ``native_forecast_entry_id``, ``actual_power_entity``,
           ``actual_energy_today_entity``).
        2. The single solar source configured in the Home Assistant Energy
           Dashboard: its ``config_entry_solar_forecast[0]`` becomes the
           Forecast.Solar entry, its ``stat_rate`` becomes the actual power
           sensor, its ``stat_energy_from`` becomes the actual energy sensor.

        The user-supplied entities are treated as canonical: they do not need
        to match the Energy Dashboard. When both a user override and the
        Energy Dashboard suggest a Forecast.Solar entry and they disagree, we
        prefer the user's choice (the config flow is the deliberate answer).
        """

        entry_data = self.entry.data or {}
        user_native_entry = entry_data.get(CONF_NATIVE_FORECAST_ENTRY_ID) or None
        user_actual_power = entry_data.get(CONF_ACTUAL_POWER) or None
        user_actual_energy = entry_data.get(CONF_ACTUAL_ENERGY_TODAY) or None

        native_entry_id: str | None = user_native_entry
        actual_power_entity: str | None = user_actual_power
        actual_energy_entity: str | None = user_actual_energy

        need_energy_lookup = (
            native_entry_id is None or actual_power_entity is None or actual_energy_entity is None
        )

        if need_energy_lookup:
            try:
                energy_data = await self.hass.async_add_executor_job(
                    importlib.import_module,
                    "homeassistant.components.energy.data",
                )
                manager = await energy_data.async_get_manager(self.hass)
            except Exception as err:
                return NativeBinding(
                    "unsupported_native_contract",
                    reason=f"energy_manager_unavailable:{type(err).__name__}",
                )

            preferences = getattr(manager, "data", None)
            sources = (
                preferences.get("energy_sources") if isinstance(preferences, Mapping) else None
            )
            if not isinstance(sources, list):
                return NativeBinding("binding_unavailable", reason="energy_sources_missing")
            solar_sources = [
                source
                for source in sources
                if isinstance(source, Mapping) and source.get("type") == "solar"
            ]
            if len(solar_sources) != 1:
                return NativeBinding(
                    "binding_ambiguous", reason=f"solar_source_count:{len(solar_sources)}"
                )
            source = solar_sources[0]
            entry_ids = source.get("config_entry_solar_forecast")
            if (
                not isinstance(entry_ids, list)
                or len(entry_ids) != 1
                or not isinstance(entry_ids[0], str)
            ):
                return NativeBinding(
                    "binding_ambiguous",
                    reason="config_entry_solar_forecast_not_exactly_one",
                )
            energy_dashboard_native_entry_id = entry_ids[0]
            if native_entry_id is None:
                native_entry_id = energy_dashboard_native_entry_id
            if actual_energy_entity is None:
                actual_energy_entity = source.get("stat_energy_from")
            if actual_power_entity is None:
                actual_power_entity = source.get("stat_rate")

        if not native_entry_id or not isinstance(native_entry_id, str):
            return NativeBinding("binding_unavailable", reason="native_entry_id_missing")
        if not actual_energy_entity or not isinstance(actual_energy_entity, str):
            return NativeBinding(
                "canonical_actual_mismatch",
                native_entry_id=native_entry_id,
                reason="actual_energy_entity_missing",
            )
        if not actual_power_entity or not isinstance(actual_power_entity, str):
            return NativeBinding(
                "canonical_actual_mismatch",
                native_entry_id=native_entry_id,
                reason="actual_power_entity_missing",
            )

        config_entry = self.hass.config_entries.async_get_entry(native_entry_id)
        if config_entry is None:
            return NativeBinding(
                "native_entry_unavailable",
                native_entry_id=native_entry_id,
                reason="entry_missing",
            )
        return NativeBinding(
            "ok",
            native_entry_id=native_entry_id,
            actual_energy_entity=actual_energy_entity,
            actual_power_entity=actual_power_entity,
        )

    def _attach_native_listener(self) -> None:
        """Observe native coordinator updates without initiating one."""

        if not self.binding.native_entry_id:
            return
        native_entry = self.hass.config_entries.async_get_entry(self.binding.native_entry_id)
        runtime = getattr(native_entry, "runtime_data", None) if native_entry else None
        if runtime is None:
            return
        if self._native_listener_runtime is runtime and self._native_listener_remove is not None:
            return
        if self._native_listener_remove is not None:
            self._native_listener_remove()
            self._native_listener_remove = None
            self._native_listener_runtime = None
            self._native_listener_observed_at_utc = None
            self._last_marker = None
            self._observation = None
        add_listener = getattr(runtime, "async_add_listener", None)
        if not callable(add_listener):
            return

        def listener(*_args: Any) -> None:
            if (
                self._native_listener_runtime is not runtime
                or getattr(runtime, "last_update_success", False) is not True
            ):
                return
            self._native_listener_observed_at_utc = datetime.now(UTC)
            if self._capture_task is None or self._capture_task.done():
                self._capture_task = self.hass.async_create_task(self.async_capture())

        self._native_listener_remove = add_listener(listener)
        if self._native_listener_remove is not None:
            self._native_listener_runtime = runtime
            remove_listener = self._native_listener_remove
            removed = False

            def remove_once() -> None:
                nonlocal removed
                if removed:
                    return
                removed = True
                remove_listener()

            self._native_listener_remove = remove_once
            self.entry.async_on_unload(remove_once)

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
        """Return ``True`` iff running on a supported HA Core minor line.

        We compare the running ``homeassistant.const.__version__`` against a
        minimum (``TARGET_CORE_MIN_VERSION``, currently ``2026.7``). Every
        Forecast.Solar internal we depend on is also feature-detected at
        capture time, so patch releases within the supported minor line do
        not require a new Solar Analytics release.
        """

        try:
            constants = importlib.import_module("homeassistant.const")
            version = str(getattr(constants, "__version__", ""))
        except Exception:  # pragma: no cover - import boundary
            return False
        return _version_tuple(version) >= TARGET_CORE_MIN_VERSION

    async def _async_get_helper(self, domain: str) -> Any | None:
        """Return the provider's Energy solar-forecast helper, or ``None``.

        Resolve the helper the same way the Energy Dashboard itself does, via
        ``energy.websocket_api.async_get_energy_platforms``. That registry is
        built through Home Assistant's integration-platform loader, so it covers
        both core integrations (``forecast_solar``) and custom components
        (``solcast_solar`` and any future HACS provider). A domain-templated
        ``homeassistant.components.<domain>.energy`` import would only ever see
        core integrations and silently exclude every custom-component provider.
        """

        if not self._core_version_supported():
            return None
        if not domain:
            return None
        if self._helper is not None and self._helper_domain == domain:
            return self._helper
        try:
            websocket_api = await self.hass.async_add_executor_job(
                importlib.import_module,
                "homeassistant.components.energy.websocket_api",
            )
            get_platforms = websocket_api.async_get_energy_platforms
        except ImportError, AttributeError:
            return None
        try:
            platforms = await get_platforms(self.hass)
        except Exception as err:  # pragma: no cover - platform discovery boundary
            _LOGGER.debug("Energy platform discovery failed: %s", err)
            return None
        helper = platforms.get(domain) if isinstance(platforms, Mapping) else None
        if not callable(helper):
            return None
        self._helper = helper
        self._helper_domain = domain
        return helper

    def _native_entry_and_runtime(self) -> tuple[Any | None, Any | None]:
        if not self.binding.native_entry_id:
            return None, None
        native_entry = self.hass.config_entries.async_get_entry(self.binding.native_entry_id)
        if native_entry is None:
            return None, None
        runtime = getattr(native_entry, "runtime_data", None)
        return native_entry, runtime

    @classmethod
    def _model_from_entry(cls, native_entry: Any) -> NativeModel:
        """Build the model identity for the bound provider entry.

        Forecast.Solar keeps its exact plane-geometry fingerprint so existing
        lineages are preserved byte-for-byte. Any other provider gets a generic
        fingerprint over its non-secret config so a genuinely different source
        starts its own lineage.
        """

        if getattr(native_entry, "domain", None) == "forecast_solar":
            return cls._forecast_solar_model(native_entry)
        return cls._generic_model(native_entry)

    @staticmethod
    def _generic_model(native_entry: Any) -> NativeModel:
        """Identity for a non-Forecast.Solar Energy provider entry.

        The model identity is the bound source itself: its domain and config
        entry id. Provider config values are deliberately never copied here.
        A denylist over arbitrary third-party config is the wrong direction
        (it leaks any credential key it does not recognize into published state,
        diagnostics, and the lineage row), and copying the whole options bag
        would reset the accuracy lineage every time an unrelated display or
        debug knob is toggled. Domain + entry id is a stable identity: a
        genuinely different bound source has a different entry id and starts its
        own lineage, while a rotated token or toggled option does not.
        """

        try:
            values: dict[str, Any] = {
                "status": "ok",
                "provider_domain": getattr(native_entry, "domain", None),
                "provider_entry_id": getattr(native_entry, "entry_id", None),
            }
            fingerprint = build_generic_model_fingerprint(values)
            if fingerprint is None:
                return NativeModel(
                    "unsupported_native_contract", values, None, "generic_model_invalid"
                )
            values["model_fingerprint_sha256"] = fingerprint
            return NativeModel("ok", values, fingerprint)
        except AttributeError, TypeError, ValueError:
            return NativeModel(
                "unsupported_native_contract", {}, None, "native_entry_shape_invalid"
            )

    @staticmethod
    def _forecast_solar_model(native_entry: Any) -> NativeModel:
        try:
            data = dict(getattr(native_entry, "data", {}) or {})
            options = dict(getattr(native_entry, "options", {}) or {})
            get_planes = getattr(native_entry, "get_subentries_of_type", None)
            planes = list(get_planes("plane")) if callable(get_planes) else []
            if len(planes) != 1:
                return NativeModel(
                    "unsupported_native_contract", {}, None, f"plane_count:{len(planes)}"
                )
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
                "auth_mode": "authenticated"
                if "api_key" in options or "api_key" in data
                else "public",
            }
            fingerprint = build_native_model_fingerprint(values)
            if fingerprint is None:
                return NativeModel(
                    "unsupported_native_contract", values, None, "invalid_model_values"
                )
            values["model_fingerprint_sha256"] = fingerprint
            return NativeModel("ok", values, fingerprint)
        except AttributeError, TypeError, ValueError:
            return NativeModel(
                "unsupported_native_contract", {}, None, "native_entry_shape_invalid"
            )

    async def async_capture(self) -> NativeRead:
        """Capture a successful native observation already held by the coordinator."""

        self.binding = await self.async_resolve_binding()
        if not self.binding.ready:
            return NativeRead(self.binding.status, self.binding, reason=self.binding.reason)
        native_entry, runtime = self._native_entry_and_runtime()
        if native_entry is None or runtime is None:
            return NativeRead("native_source_unavailable", self.binding, reason="entry_unloaded")
        domain = str(getattr(native_entry, "domain", "") or "")
        helper = await self._async_get_helper(domain)
        if helper is None:
            return NativeRead(
                "unsupported_native_contract", self.binding, reason="helper_import_or_signature"
            )
        # The native config entry may finish setup after this integration due
        # to config-entry ordering. Retry listener attachment on every capture,
        # but never initiate a native refresh here.
        self._attach_native_listener()
        runtime_data = getattr(runtime, "data", None)
        # Forecast.Solar's coordinator exposes ``wh_period`` on its runtime; we
        # keep that strict shape gate for it. Other providers do not share that
        # internal, so their liveness rests on ``last_update_success`` and the
        # observed listener callback below.
        if domain == "forecast_solar":
            wh_period = getattr(runtime_data, "wh_period", None)
            if not isinstance(wh_period, Mapping):
                return NativeRead(
                    "unsupported_native_contract",
                    self.binding,
                    reason="runtime_wh_period_missing",
                )
        if getattr(runtime, "last_update_success", False) is not True:
            return NativeRead(
                "native_source_unavailable", self.binding, reason="last_update_not_successful"
            )
        # Forecast.Solar's pinned coordinator is a plain DataUpdateCoordinator;
        # last_update_success_time is not part of its contract. A retained
        # runtime payload is not admissible until a native listener callback has
        # been observed by this adapter after setup.
        native_updated_at = self._native_listener_observed_at_utc
        if native_updated_at is None:
            return NativeRead(
                "native_source_unavailable", self.binding, reason="native_update_not_observed"
            )
        now = datetime.now(UTC)
        age = (now - native_updated_at).total_seconds()
        if age < -300 or age > MAX_OBSERVATION_AGE.total_seconds():
            return NativeRead(
                "native_source_stale", self.binding, reason=f"native_update_age_seconds:{age:.1f}"
            )
        try:
            payload = await helper(self.hass, self.binding.native_entry_id)
        except Exception as err:
            _LOGGER.debug("Native Forecast.Solar helper failed: %s", err)
            return NativeRead(
                "native_source_unavailable",
                self.binding,
                reason=f"helper_error:{type(err).__name__}",
            )
        if not isinstance(payload, Mapping):
            return NativeRead(
                "unsupported_native_contract", self.binding, reason="helper_payload_not_mapping"
            )
        profile = normalize_native_wh_hours(payload)
        if profile.status != "complete" or profile.payload_sha256 is None:
            reason_names = sorted(
                {
                    period.exclusion_reason
                    for period in profile.periods
                    if (
                        not period.valid
                        and period.exclusion_reason
                        and period.exclusion_reason != "missing_previous_boundary"
                    )
                }
            )
            reason_summary = ",".join(reason_names) or "none"
            return NativeRead(
                "unsupported_native_contract",
                self.binding,
                reason=(
                    "wh_hours_validation_failed:"
                    f"raw_count={profile.raw_count}:"
                    f"invalid_count={profile.invalid_count}:"
                    f"reasons={reason_summary}"
                ),
            )
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
            self._native_listener_runtime = None


# Provider-neutral alias. The Energy Dashboard adapter is one
# ``ForecastProfileProvider`` implementation; the entity adapter in
# ``forecast_source`` is the other. New code references ``EnergyForecastProvider``.
EnergyForecastProvider = ForecastSolarNativeAdapter


async def async_single_solar_source(
    hass: HomeAssistant,
) -> tuple[Mapping[str, Any] | None, NativeBinding | None]:
    """Return the Energy Dashboard's single solar source, or an error binding.

    Shared by the forecast-entity provider to auto-detect the actual PV
    sensors when the user leaves them blank. Read-only: it imports the energy
    data module off the event loop and inspects preferences only.
    """

    try:
        energy_data = await hass.async_add_executor_job(
            importlib.import_module,
            "homeassistant.components.energy.data",
        )
        manager = await energy_data.async_get_manager(hass)
    except Exception as err:
        return None, NativeBinding(
            "unsupported_native_contract",
            reason=f"energy_manager_unavailable:{type(err).__name__}",
        )
    preferences = getattr(manager, "data", None)
    sources = preferences.get("energy_sources") if isinstance(preferences, Mapping) else None
    if not isinstance(sources, list):
        return None, NativeBinding("binding_unavailable", reason="energy_sources_missing")
    solar_sources = [
        source
        for source in sources
        if isinstance(source, Mapping) and source.get("type") == "solar"
    ]
    if len(solar_sources) != 1:
        return None, NativeBinding(
            "binding_ambiguous", reason=f"solar_source_count:{len(solar_sources)}"
        )
    return solar_sources[0], None
