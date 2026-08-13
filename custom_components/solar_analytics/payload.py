"""Pure builder for the coordinator's per-tick output payload.

Separated from ``coordinator.py`` so the payload shape can be unit-tested
without instantiating a ``DataUpdateCoordinator`` (which requires Home
Assistant). The function has no side effects and reads no globals besides
the four version/coverage constants imported below.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .imported_actuals import IMPORT_PROVENANCE
from .native import NATIVE_ADAPTER_VERSION, NATIVE_CONTRACT_VERSION
from .native_adapter import NativeRead
from .v2_metrics import MIN_ACTUAL_COVERAGE, MIN_FORECAST_COVERAGE, ActualState

_FUTURE_POINTS_CAP = 96
_DAILY_POINTS_CAP = 30
_IMPORTED_POINTS_CAP = 180


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def build_imported_history_block(
    *, status: str, source_entity_id: str | None, rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Summarise imported historical actuals for one bounded state attribute.

    This block is reconstructed actual production only. It never contributes to
    ``accuracy``, ``daily_points``, ``valid_paired_day`` or the analysis status.
    """

    return {
        "status": status,
        "provenance": IMPORT_PROVENANCE,
        "source_entity": source_entity_id,
        "day_count": len(rows),
        "total_kwh": round(sum(float(row.get("energy_kwh") or 0.0) for row in rows), 3),
        "points": [
            [
                row.get("local_date"),
                round(float(row.get("energy_kwh") or 0.0), 3),
                round(float(row.get("coverage") or 0.0), 4),
                row.get("counter_resets"),
            ]
            for row in rows[-_IMPORTED_POINTS_CAP:]
        ],
        "schema": ["date", "energy_kwh", "coverage", "counter_resets"],
        "storage": f"SQLite v2; entity output bounded to {_IMPORTED_POINTS_CAP} days",
    }


def build_payload(
    *,
    native_read: NativeRead,
    actual_power: ActualState,
    actual_energy: ActualState,
    accuracy: Mapping[str, Any],
    daily_rows: Sequence[Mapping[str, Any]],
    lineage_id: str | None,
    reconciliation_status: str,
    imported_history: Mapping[str, Any],
    now_utc: datetime,
) -> dict[str, Any]:
    """Return the JSON-serialisable payload consumed by the sensor platforms."""

    observation = native_read.observation
    model = observation.model if observation is not None else native_read.model
    native_contract: dict[str, Any] = dict(model.values) if model is not None else {}
    native_contract.update(
        {
            "status": native_read.status,
            "native_entry_id": native_read.binding.native_entry_id,
            "model_fingerprint_sha256": model.fingerprint if model else None,
            "native_contract_version": NATIVE_CONTRACT_VERSION,
            "adapter_version": NATIVE_ADAPTER_VERSION,
            "native_update_time_source": "local_listener_observation",
        }
    )

    forecast_power: float | None = None
    future_points: list[dict[str, Any]] = []
    if observation is not None:
        for period in observation.profile.valid_periods:
            if period.start_utc is None:
                continue
            future_points.append(
                {
                    "start_utc": period.start_utc.isoformat(),
                    "end_utc": period.end_utc.isoformat(),
                    "energy_wh": period.energy_wh,
                    "duration_seconds": period.duration_seconds,
                    "power_w": period.power_w,
                }
            )
            if period.start_utc <= now_utc < period.end_utc:
                forecast_power = period.power_w
        future_points = future_points[:_FUTURE_POINTS_CAP]

    daily_points = [
        [
            row.get("local_date"),
            row.get("actual_kwh"),
            row.get("forecast_kwh"),
            row.get("signed_error_kwh"),
            row.get("forecast_coverage"),
            row.get("actual_coverage"),
            row.get("valid_paired_day"),
            row.get("reason"),
        ]
        for row in daily_rows[-_DAILY_POINTS_CAP:]
    ]
    latest_daily: Mapping[str, Any] = daily_rows[-1] if daily_rows else {}

    status, validity_reason = _classify_status(native_read, actual_power, actual_energy, accuracy)

    insight = {
        "schema": "solar-analytics-v2",
        "generated_at": now_utc.isoformat(),
        "status": status,
        "lineage_id": lineage_id,
        "forecast_accuracy": dict(accuracy),
        "coverage": {
            "required_forecast": MIN_FORECAST_COVERAGE,
            "required_actual": MIN_ACTUAL_COVERAGE,
            "valid_paired_days": accuracy.get("valid_paired_days", 0),
        },
        "quality": {
            "native_status": native_read.status,
            "actual_power_status": actual_power.status,
            "actual_energy_status": actual_energy.status,
            "reconciliation_status": reconciliation_status,
            "curtailment": "unknown_not_claimed",
            "external_control": "unknown_not_claimed",
            "inverter_limitation": "unknown_not_claimed",
            "underperformance_claim_allowed": False,
        },
    }
    return {
        "status": status,
        "analysis_valid": bool(accuracy.get("accuracy_ready")),
        "forecast_profile_analysis_allowed": native_read.status == "ok" and observation is not None,
        "native_source_status": native_read.status,
        "native_forecast_contract": native_contract,
        "actual_power_w": actual_power.value if actual_power.valid else None,
        "actual_energy_kwh": actual_energy.value if actual_energy.valid else None,
        "forecast_solar_power_w": forecast_power,
        "vrm_forecast_power_w": None,
        "current_limitation": "not_claimed",
        "validity_reason": validity_reason,
        "curtailment_reason": None,
        "last_insight": validity_reason,
        "insight": insight,
        "hermes_json": json.dumps(
            insight, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
        "daily_points": daily_points,
        "future_points": future_points,
        "imported_actual_history": dict(imported_history),
        "heatmap": {"status": "unavailable", "x": [], "y": [], "z": [], "customdata": []},
        "accuracy": dict(accuracy),
        "forecast_coverage": latest_daily.get("forecast_coverage"),
        "actual_coverage": latest_daily.get("actual_coverage"),
        "paired_coverage": latest_daily.get("paired_coverage"),
        "lineage_id": lineage_id,
        "native_observation_sequence": observation.observation_sequence if observation else None,
        "native_payload_sha256": observation.payload_sha256 if observation else None,
        "native_observed_at": _iso(observation.observed_at_utc) if observation else None,
        "native_updated_at": _iso(observation.native_updated_at_utc) if observation else None,
        "source_map": {
            "forecast": "homeassistant.components.forecast_solar.energy.async_get_solar_forecast",
            "native_config_entry": native_read.binding.native_entry_id,
            "actual_power": native_read.binding.actual_power_entity,
            "actual_energy": native_read.binding.actual_energy_entity,
            "vrm": "scalar_context_only",
        },
        "last_updated": now_utc.isoformat(),
        "reconciliation_status": reconciliation_status,
    }


def _classify_status(
    native_read: NativeRead,
    actual_power: ActualState,
    actual_energy: ActualState,
    accuracy: Mapping[str, Any],
) -> tuple[str, str]:
    """Return the (analysis status, validity reason) tuple from the inputs.

    Precedence: native failures shadow actual-source failures; actual-source
    failures shadow accuracy readiness; accuracy readiness is the only path
    to ``ready``.
    """

    if native_read.status != "ok":
        return native_read.status, native_read.reason or native_read.status
    if not actual_power.valid or not actual_energy.valid:
        if actual_power.status == "stale" or actual_energy.status == "stale":
            status = "actual_source_stale"
        else:
            status = "actual_source_unavailable"
        return status, actual_power.reason or actual_energy.reason or status
    if accuracy.get("accuracy_ready"):
        return "ready", "paired_history_ready"
    return "insufficient_data", "native_and_actual_valid_but_history_below_gate"
