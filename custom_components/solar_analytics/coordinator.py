"""Read-only Solar Analytics coordinator.

Owns the update loop, the two scheduled snapshot timers (both fired in the
user-configured analytics timezone, not in ``hass.config.time_zone``), and
the executor boundary for the synchronous SQLite store. Never mutates
Home Assistant state, calls services, or triggers refreshes on any other
integration.
"""

from __future__ import annotations

import functools
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_DAY_AHEAD_HOUR,
    CONF_MORNING_HOUR,
    CONF_TIME_ZONE,
    DEFAULT_DAY_AHEAD_HOUR,
    DEFAULT_MORNING_HOUR,
    DOMAIN,
    NAME,
)
from .native import NATIVE_ADAPTER_VERSION, NATIVE_CONTRACT_VERSION
from .native_adapter import ForecastSolarNativeAdapter, NativeObservation, NativeRead
from .payload import build_payload
from .reconciliation import reconcile_energy_counter, reconcile_intervals, rollup_daily
from .storage_v2 import METRIC_VERSION, NORMALIZATION_VERSION, SolarAnalyticsV2Store, StorageError
from .v2_metrics import (
    ActualState,
    compute_accuracy,
    previous_slots_to_finalize,
    validate_actual_state,
)

_LOGGER = logging.getLogger(__name__)
UPDATE_INTERVAL = timedelta(minutes=5)

# Native binding statuses the coordinator surfaces as HA repair issues.
# ``canonical_actual_mismatch`` and ``binding_changed`` are fixable via the
# integration's reconfigure step; the rest are informational (non-fixable)
# and describe the situation for the user.
_ISSUE_FIXABLE = {"canonical_actual_mismatch", "binding_changed"}
_ISSUE_INFO = {
    "binding_unavailable",
    "binding_ambiguous",
    "native_entry_unavailable",
    "unsupported_native_contract",
}
_MANAGED_ISSUE_IDS = _ISSUE_FIXABLE | _ISSUE_INFO


def _default_time_zone(hass: HomeAssistant) -> str:
    """Return the timezone Solar Analytics should default to.

    Uses Home Assistant's own configured timezone; ``UTC`` if HA has none.
    """

    config = getattr(hass, "config", None)
    configured = getattr(config, "time_zone", None) if config is not None else None
    return str(configured) if configured else "UTC"


def _next_local_hour_utc(now_utc: datetime, tz: ZoneInfo, hour: int) -> datetime:
    """Return the next UTC instant matching ``hour:00`` local time in ``tz``.

    If the current local time is already past ``hour:00`` today, we advance
    to the same hour tomorrow. Uses whole-hour granularity to keep the
    scheduled instant identical to what the coordinator persists.
    """

    now_local = now_utc.astimezone(tz)
    candidate_local = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate_local <= now_local:
        candidate_local = candidate_local + timedelta(days=1)
    return candidate_local.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _state_mapping(state: Any, entity_id: str) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "entity_id": getattr(state, "entity_id", entity_id),
        "state": getattr(state, "state", None),
        "attributes": dict(getattr(state, "attributes", {}) or {}),
        "last_updated": getattr(state, "last_updated", None),
        "restored": getattr(state, "attributes", {}).get("restored")
        if getattr(state, "attributes", None)
        else False,
    }


class SolarAnalyticsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Own acquisition, normalization, persistence and explainable read-only metrics."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.time_zone = ZoneInfo(str(entry.data.get(CONF_TIME_ZONE) or _default_time_zone(hass)))
        self.morning_hour = int(entry.data.get(CONF_MORNING_HOUR, DEFAULT_MORNING_HOUR))
        self.day_ahead_hour = int(entry.data.get(CONF_DAY_AHEAD_HOUR, DEFAULT_DAY_AHEAD_HOUR))
        storage_path = Path(hass.config.path("solar_analytics", "solar_analytics.sqlite"))
        self.store = SolarAnalyticsV2Store(storage_path)
        self.native_adapter = ForecastSolarNativeAdapter(hass, entry)
        self._unsubscribers: list[Any] = []
        self._morning_unsub: Any | None = None
        self._day_ahead_unsub: Any | None = None
        self._last_native_read: NativeRead | None = None
        self._last_payload: dict[str, Any] | None = None
        self._initialized = False
        # Silver-tier "log when unavailable / log when recovered" state.
        # Holds the last reported native binding status so we log once per
        # transition instead of every 5-minute poll.
        self._logged_native_status: str | None = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=NAME,
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )

    async def async_initialize(self) -> None:
        """Initialize storage, bind native source and schedule immutable snapshot slots."""

        await self.hass.async_add_executor_job(self.store.initialize)
        await self.native_adapter.async_initialize()
        self._schedule_next_snapshot("morning", self.morning_hour)
        self._schedule_next_snapshot("day_ahead", self.day_ahead_hour)
        # A restart after a scheduled instant creates one terminal missed/blocked
        # slot; it never backfills with a later observation.
        await self._finalize_missed_slots()
        self._initialized = True

    def _maintain_repair_issues(self, binding_status: str, reason: str | None) -> None:
        """Create or clear HA repair issues for user-actionable binding failures."""

        for issue_id in _MANAGED_ISSUE_IDS:
            if issue_id == binding_status:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=issue_id in _ISSUE_FIXABLE,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=issue_id,
                    translation_placeholders={"reason": reason or issue_id},
                )
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _log_native_status_transition(self, binding_status: str, reason: str | None) -> None:
        """Log exactly once per binding-status transition.

        Prevents the 5-minute update loop from spamming the log while a
        recoverable failure (native_source_unavailable, native_source_stale)
        persists. Emits a matching info-level line when the binding recovers
        to 'ok'.
        """

        if binding_status == self._logged_native_status:
            return
        previous = self._logged_native_status
        self._logged_native_status = binding_status
        if binding_status == "ok":
            if previous is not None:
                _LOGGER.info(
                    "Solar Analytics native Forecast.Solar binding recovered from %s",
                    previous,
                )
        else:
            _LOGGER.warning(
                "Solar Analytics native Forecast.Solar binding unavailable: %s (%s)",
                binding_status,
                reason or "no_reason",
            )

    @property
    def actual_power_entity(self) -> str | None:
        """Return the currently-resolved actual PV power entity ID."""

        return self.native_adapter.binding.actual_power_entity

    @property
    def actual_energy_entity(self) -> str | None:
        """Return the currently-resolved actual PV energy entity ID."""

        return self.native_adapter.binding.actual_energy_entity

    async def _finalize_missed_slots(self) -> None:
        now = datetime.now(UTC)
        slots = previous_slots_to_finalize(
            now,
            tz=self.time_zone,
            morning_hour=self.morning_hour,
            day_ahead_hour=self.day_ahead_hour,
        )
        if not slots:
            return
        existing = await self.hass.async_add_executor_job(
            functools.partial(self.store.list_snapshot_slots, source_kind="native")
        )
        existing_keys = {
            (row.get("snapshot_type"), row.get("scheduled_at_utc")) for row in existing
        }
        for slot in slots:
            key = (slot.snapshot_type, slot.scheduled_at_utc.isoformat())
            if key in existing_keys:
                continue
            read = await self.native_adapter.async_capture()
            await self.hass.async_add_executor_job(self._write_snapshot_sync, slot, read, now)

    def _schedule_next_snapshot(self, snapshot_type: str, hour: int) -> None:
        """Schedule the next fire in the configured timezone, then re-schedule.

        Uses ``async_track_point_in_utc_time`` so the schedule respects
        ``self.time_zone`` rather than ``hass.config.time_zone``.
        """

        next_fire_utc = _next_local_hour_utc(datetime.now(UTC), self.time_zone, hour)

        @callback
        def _fire(_now: datetime) -> None:
            self.hass.async_create_task(self._capture_scheduled(snapshot_type, next_fire_utc))
            self._schedule_next_snapshot(snapshot_type, hour)

        remove = async_track_point_in_utc_time(self.hass, _fire, next_fire_utc)
        if snapshot_type == "morning":
            self._morning_unsub = remove
        elif snapshot_type == "day_ahead":
            self._day_ahead_unsub = remove

    async def _capture_scheduled(self, snapshot_type: str, scheduled_at_utc: datetime) -> None:
        scheduled_at_local = scheduled_at_utc.astimezone(self.time_zone)
        slot = {
            "snapshot_type": snapshot_type,
            "scheduled_at_local": scheduled_at_local,
            "scheduled_at_utc": scheduled_at_utc,
            "target_local_date": scheduled_at_local.date() + timedelta(days=1),
        }
        read = await self.native_adapter.async_capture()
        await self.hass.async_add_executor_job(
            self._write_snapshot_sync, slot, read, datetime.now(UTC)
        )

    def _write_snapshot_sync(self, slot: Any, read: NativeRead, now_utc: datetime) -> None:
        """Insert one immutable schedule slot and its child period rows."""

        snapshot_type = (
            slot.snapshot_type if hasattr(slot, "snapshot_type") else slot["snapshot_type"]
        )
        scheduled_at_utc = (
            slot.scheduled_at_utc if hasattr(slot, "scheduled_at_utc") else slot["scheduled_at_utc"]
        )
        target_local_date = (
            slot.target_local_date
            if hasattr(slot, "target_local_date")
            else slot["target_local_date"]
        )
        observation = read.observation if read is not None else None
        admissible = bool(
            read is not None
            and read.status == "ok"
            and observation is not None
            and observation.observed_at_utc <= scheduled_at_utc
        )
        lineage_id: str | None = None
        status = "missing"
        reason = read.reason if read is not None else "native_read_missing"
        if read is not None and read.status != "ok":
            status = "blocked"
            reason = read.reason or read.status
        if admissible and observation is not None:
            lineage_id = self._ensure_lineage_sync(observation, now_utc)
            status = "admissible"
            reason = None
        elif read is not None and read.status == "ok":
            reason = "observation_not_available_at_scheduled_instant"
        slot_id, created = self.store.ensure_snapshot_slot(
            lineage_id=lineage_id,
            source_kind="native",
            snapshot_type=snapshot_type,
            scheduled_at_utc=scheduled_at_utc,
            target_local_date=target_local_date,
            timezone_name=str(self.time_zone),
            observed_at_utc=observation.observed_at_utc if admissible and observation else None,
            native_updated_at_utc=observation.native_updated_at_utc
            if admissible and observation
            else None,
            observation_sequence=observation.observation_sequence
            if admissible and observation
            else None,
            payload_sha256=observation.payload_sha256 if admissible and observation else None,
            adapter_version=NATIVE_ADAPTER_VERSION,
            normalization_version=NORMALIZATION_VERSION,
            metric_version=METRIC_VERSION,
            status=status,
            admissible=admissible,
            exclusion_reason=reason,
        )
        if created and admissible and observation is not None:
            self.store.insert_snapshot_periods(slot_id, observation.profile.as_storage_rows())

    def _ensure_lineage_sync(self, observation: NativeObservation, now_utc: datetime) -> str:
        model = observation.model
        values = dict(model.values)
        binding = self.native_adapter.binding
        native_entry_id = binding.native_entry_id or ""
        actual_energy_entity = binding.actual_energy_entity or ""
        actual_power_entity = binding.actual_power_entity or ""
        contract_key = "|".join(
            (
                native_entry_id,
                str(model.fingerprint),
                NATIVE_CONTRACT_VERSION,
                NATIVE_ADAPTER_VERSION,
                actual_energy_entity,
                actual_power_entity,
            )
        )
        return self.store.ensure_lineage(
            contract_key=contract_key,
            metadata={
                "source_kind": "native",
                "native_entry_id": native_entry_id,
                "model_fingerprint": model.fingerprint,
                "model": values,
                "actual_energy_entity": actual_energy_entity,
                "actual_power_entity": actual_power_entity,
                "adapter_version": NATIVE_ADAPTER_VERSION,
                "native_contract_version": NATIVE_CONTRACT_VERSION,
                "normalization_version": NORMALIZATION_VERSION,
                "metric_version": METRIC_VERSION,
            },
            now=now_utc,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Read live states and execute all blocking SQLite work in the executor."""

        now = datetime.now(UTC)
        native_read = await self.native_adapter.async_capture()
        self._last_native_read = native_read
        self._maintain_repair_issues(native_read.binding.status, native_read.binding.reason)
        self._log_native_status_transition(native_read.binding.status, native_read.binding.reason)
        power_entity = self.actual_power_entity or ""
        energy_entity = self.actual_energy_entity or ""
        power_state = (
            _state_mapping(self.hass.states.get(power_entity), power_entity)
            if power_entity
            else None
        )
        energy_state = (
            _state_mapping(self.hass.states.get(energy_entity), energy_entity)
            if energy_entity
            else None
        )
        actual_power = validate_actual_state(
            power_state,
            expected_entity_id=power_entity or "",
            kind="power",
            now_utc=now,
        )
        actual_energy = validate_actual_state(
            energy_state,
            expected_entity_id=energy_entity or "",
            kind="energy",
            now_utc=now,
        )
        try:
            payload = await self.hass.async_add_executor_job(
                self._process_sync,
                native_read,
                actual_power,
                actual_energy,
                now,
            )
        except StorageError as err:
            _LOGGER.error("Solar Analytics storage fail-closed: %s", err)
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="storage_failure",
                translation_placeholders={"error_type": type(err).__name__},
            ) from err
        self._last_payload = payload
        return payload

    def _process_sync(
        self,
        native_read: NativeRead,
        actual_power: ActualState,
        actual_energy: ActualState,
        now_utc: datetime,
    ) -> dict[str, Any]:
        observation = native_read.observation if native_read.status == "ok" else None
        lineage_id: str | None = None
        if observation is not None:
            lineage_id = self._ensure_lineage_sync(observation, now_utc)
            self.store.upsert_current_profile(
                source_kind="native",
                lineage_id=lineage_id,
                observed_at_utc=observation.observed_at_utc,
                native_updated_at_utc=observation.native_updated_at_utc,
                observation_sequence=observation.observation_sequence,
                payload_sha256=observation.payload_sha256,
                profile=observation.profile.as_storage_rows(),
                quality={
                    "status": observation.profile.status,
                    "raw_count": observation.profile.raw_count,
                    "valid_count": len(observation.profile.valid_periods),
                    "invalid_count": observation.profile.invalid_count,
                },
            )
        if lineage_id is None:
            lineage_id = self.store.current_lineage_id()
        self.store.add_power_sample(
            actual_power.observed_at_utc or now_utc,
            actual_power.value if actual_power.valid else None,
        )
        reconciliation_status = reconcile_energy_counter(
            self.store, actual_energy=actual_energy, now_utc=now_utc, tz=self.time_zone
        )
        if observation is not None and lineage_id:
            reconcile_intervals(
                self.store, lineage_id=lineage_id, now_utc=now_utc, tz=self.time_zone
            )
        daily_rows = rollup_daily(
            self.store, lineage_id=lineage_id, now_utc=now_utc, tz=self.time_zone
        )
        accuracy = compute_accuracy(
            daily_rows, today_local=now_utc.astimezone(self.time_zone).date()
        )
        if lineage_id:
            self.store.save_accuracy(
                lineage_id=lineage_id,
                generated_at=now_utc,
                window_days=int(accuracy["window_days"]),
                valid_days=int(accuracy["valid_paired_days"]),
                accuracy_ready=bool(accuracy["accuracy_ready"]),
                payload=accuracy,
            )
        return build_payload(
            native_read=native_read,
            actual_power=actual_power,
            actual_energy=actual_energy,
            accuracy=accuracy,
            daily_rows=daily_rows,
            lineage_id=lineage_id,
            reconciliation_status=reconciliation_status,
            now_utc=now_utc,
        )

    def current_native_forecast_contract(self) -> dict[str, Any]:
        data = self.data or self._last_payload or {}
        return dict(data.get("native_forecast_contract") or {})

    async def async_unload(self) -> None:
        for unsubscribe in (self._morning_unsub, self._day_ahead_unsub, *self._unsubscribers):
            if unsubscribe is None:
                continue
            try:
                unsubscribe()
            except Exception:  # pragma: no cover - HA lifecycle boundary
                _LOGGER.debug("Solar Analytics timer unsubscribe failed", exc_info=True)
        self._morning_unsub = None
        self._day_ahead_unsub = None
        self._unsubscribers.clear()
        await self.native_adapter.async_unload()
        await self.hass.async_add_executor_job(self.store.close)
