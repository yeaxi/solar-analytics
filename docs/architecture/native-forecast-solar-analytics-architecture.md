# Native Forecast.Solar and Solar Analytics Architecture

## Scope

Solar Analytics is a read-only Home Assistant custom integration. It observes a solar forecast profile, compares it with canonical PV telemetry, and persists bounded analytics. Physical planning, relay ownership, and control remain outside this project.

The forecast source is generalized. It is one of two user-selected sources (`forecast_source_type`):

- An **Energy Dashboard solar-forecast integration** (Forecast.Solar, Solcast, and any future provider), observed through `homeassistant.components.<domain>.energy.async_get_solar_forecast`, where `<domain>` is the bound config entry's own domain. Handled by `native_adapter.py`.
- A **forecast entity** whose attributes expose a timestamped Wh-per-period map (`wh_hours`, `wh_period`, or `watt_hours_period`). Handled by `forecast_source.py`.

Forecast.Solar keeps its exact model fingerprint and `wh_period` liveness gate, so an existing install's lineage and 14-day accuracy warm-up are preserved. The rest of this document describes the Forecast.Solar path in detail; the same normalization, admission, and persistence contracts apply to every source.

## Native acquisition contract

Home Assistant Core 2026.7.4 exposes the Energy Dashboard profile through the native energy-platform callable. Solar Analytics resolves it from the bound entry's domain rather than a single hardcoded provider:

```python
from homeassistant.components.forecast_solar.energy import async_get_solar_forecast

forecast = await async_get_solar_forecast(hass, config_entry_id)
# {"wh_hours": {"<ISO timestamp>": <Wh>, ...}}
```

The loaded provider config-entry runtime is the source of the timestamped profile. Solar Analytics must never construct a self-initiated acquisition path, pass raw config-entry secrets into analytics, or treat a cached scalar as a complete profile.

## Adapter contract

`ForecastSolarNativeAdapter` is the only boundary that imports the Home Assistant internal Forecast.Solar surface. It receives `hass` and the exact native config-entry ID, and returns:

- validated `wh_hours` periods;
- native config-entry identity;
- coordinator health and last-success state;
- a local payload digest and observation sequence;
- acquisition and observation timestamps;
- an explicit validation/admission reason.

Required checks:

1. Resolve the config entry and require the loaded state.
2. Re-resolve runtime data after replacement or reload; never retain a stale coordinator reference.
3. Require a successful, fresh native coordinator observation.
4. Require finite, non-negative Wh values keyed by timezone-aware ISO timestamps.
5. Validate ordering, horizon, period-end semantics, gaps, duplicates, and DST transitions.
6. Keep Wh/period distinct from instantaneous W and kWh summary sensors.
7. Record locally owned digest, sequence, and timestamps without claiming provider attestation.
8. Quarantine startup, unavailable, unloaded, stale, malformed, empty, or unsupported data.

A failed native observation is fail-closed. It cannot be repaired by a manual refresh or by substituting another source.

## Ownership boundaries

Solar Analytics owns:

- the config flow and native config-entry binding;
- canonical actual PV state validation;
- profile normalization and admission;
- scheduled snapshots and immutable lineage;
- bounded SQLite persistence;
- deterministic coverage, accuracy, and explainable status entities.

It does not own:

- the native Forecast.Solar config entry or its update cadence;
- Energy Dashboard registration or provider configuration;
- existing Lovelace storage, resources, or card layout;
- physical planner/executor logic or service calls.

## Lifecycle

The integration uses typed runtime data, one listener per native runtime identity, unload-safe cleanup, restart-safe snapshot scheduling, and setup-failure cleanup. A runtime replacement detaches the old listener before attaching to the new runtime. Until a successful listener observation is received, status and profile-dependent analytics remain unavailable.

## Storage and lineage

SQLite migrations are additive, transactional, and version checked. Every native lineage records the native entry ID, adapter and contract versions, model fingerprint, observation metadata, and admission state. Current profile and accuracy rows use bounded overwrite/upsert keys; refresh cadence never creates unbounded duplicate result rows. Historical evidence is never relabeled as a fresh native observation.

An accumulator read is bounded on both sides of the requested window, from one 30-minute bucket before the start through the end, so the primary-key index returns the buckets that can overlap instead of all history before the end. Interval rebuilding stops at one finalization marker in `v2_runtime_state`, which records the build revision, the lineage, the analytics timezone, and the newest local date it covers. A local day is final one hour after its own local midnight, measured through `local_day_bounds_utc` so a 23-hour or 25-hour DST day ends at its real instant. The build revision names the interval semantics a finished day is built with, currently the day-boundary clipping described under Quality gates, so a change that preserves every row does not bump it. The marker is refused, and every retained day rebuilt once, when it does not describe the current build revision, lineage, or timezone; that is how an upgrade repairs rows written under older semantics. Each rebuild write commits on its own and the marker is written last, so a crash replays a day rather than claiming uncommitted work. Daily aggregation still rescans every retained morning slot each cycle. Bounding that pass is separate follow-up work.

## Quality gates

Forecast periods require valid timestamps, non-negative finite Wh values, complete period semantics, and admissible native lineage. Actual PV requires fresh numeric states with the expected device/state/unit contracts. Daily pairing requires forecast coverage at least 95%, actual coverage at least 90%, and a valid morning baseline. Accuracy readiness requires 14 valid paired days in a rolling 30-day window.

Coverage is measured against the true length of the local day on the UTC timeline, so a DST transition day is 23 or 25 hours rather than 24. The single zero-Wh forecast cell that Forecast.Solar emits for the night straddles local midnight; it is clipped at the boundary and counted for both adjacent days, because splitting zero energy loses nothing. A boundary-crossing cell that carries energy is excluded instead of being apportioned by time.

## Verification matrix

Local checks cover normalization, timestamps, DST, gaps, duplicates, runtime replacement, migration, storage integrity, overwrite semantics, diagnostics redaction, and absence of physical service calls. Real-HA checks cover setup, unload, runtime listener observation, `ha core check`, readiness, targeted logs, SQLite integrity, lineage, and bounded row counts.

Existing dashboards remain structurally untouched. UI verification is separate from entity-state verification.
