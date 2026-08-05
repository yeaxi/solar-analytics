# Native Forecast.Solar and Solar Analytics Architecture

## Scope

Solar Analytics is a read-only Home Assistant custom integration. It observes the exact Forecast.Solar profile used by Energy Dashboard, compares it with canonical PV telemetry, and persists bounded analytics. Physical planning, relay ownership, and control remain outside this project.

## Native acquisition contract

Home Assistant Core 2026.7.4 exposes the Energy Dashboard profile through the native energy-platform callable:

```python
from homeassistant.components.forecast_solar.energy import async_get_solar_forecast

forecast = await async_get_solar_forecast(hass, config_entry_id)
# {"wh_hours": {"<ISO timestamp>": <Wh>, ...}}
```

The loaded Forecast.Solar config-entry runtime is the source of the timestamped profile. Solar Analytics must never construct a second acquisition path, pass raw config-entry secrets into analytics, or treat a cached scalar as a complete profile.

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

## Quality gates

Forecast periods require valid timestamps, non-negative finite Wh values, complete period semantics, and admissible native lineage. Actual PV requires fresh numeric states with the expected device/state/unit contracts. Daily pairing requires forecast coverage at least 95%, actual coverage at least 90%, and a valid morning baseline. Accuracy readiness requires 14 valid paired days in a rolling 30-day window.

## Verification matrix

Local checks cover normalization, timestamps, DST, gaps, duplicates, runtime replacement, migration, storage integrity, overwrite semantics, diagnostics redaction, and absence of physical service calls. Real-HA checks cover setup, unload, runtime listener observation, `ha core check`, readiness, targeted logs, SQLite integrity, lineage, and bounded row counts.

Existing dashboards remain structurally untouched. UI verification is separate from entity-state verification.
