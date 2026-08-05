# Solar Analytics v2 — staged legacy REST migration plan

**Date:** 2026-08-03
**Status:** PREPARED / NOT EXECUTED
**Scope:** PV forecast consumers only; no physical control, notifications, or unrelated device state.

## Current evidence

- Authoritative v2 forecast source: native Home Assistant Forecast.Solar Energy Dashboard helper.
- Canonical actual PV: `sensor.garage_cerbo_gx_pv_power` and `sensor.garage_cerbo_gx_pv_energy`.
- Live legacy producer: `sensor.forecast_solar_hourly_api` (`rest` platform).
- Live legacy package: `/config/packages/energy_split.yaml`.
- Local transitional overlay: `home_assistant/deployment/energy_split_rest_native_block.yaml`.
- Legacy refresh automation: `automation.solar_analytics_refresh_forecast_solar_rest_after_native_model_change`.
- Live legacy notification surface was inventoried separately; v2 itself registers no notification service or automation.
- Current native gate is not complete: `native_source_unavailable/native_update_not_observed`, `v2_lineages=0`, and no fresh post-candidate persistence sample.

The current repository checkout does not contain the live `energy_split.yaml`; removal/migration must therefore use a verified live backup and exact live consumer readback, not a guessed local replacement.

## Preconditions — all required

1. Native current-lifecycle listener callback observed after the final candidate.
2. Native profile admitted with the sparse-zero normalization contract and a non-null payload digest.
3. Fresh SQLite persistence row verified after the admitted callback; `PRAGMA integrity_check=ok`.
4. Quantified native-only read-only soak completed for the required window; no Solar Analytics exceptions, blocking imports, or source-contract regressions.
5. Exact live consumer/reference inventory refreshed after soak, including dashboards, packages, automations, helpers, registry entries, Recorder/statistics references, and any external consumers.
6. Migration mapping reviewed against real live entities and semantics. Do not map an irregular period-energy REST sensor to a scalar-only v2 entity.
7. Timestamped backup of every live package, registry/config record, and consumer artifact that will change; SHA-256 manifest and rollback script verified.
8. Separate approval checkpoint for permanent removal remains required by the project ordering, even though the overall execution run is authorized.

## Staged migration sequence

### Stage A — native-only validation

- Keep the legacy REST producer active and untouched.
- Observe v2 native callback, admissible profile, persistence, coverage, and lineage.
- Do not use the legacy REST entity as evidence for v2 provenance or accuracy.

### Stage B — consumer mapping

- For every live reference to `sensor.forecast_solar_hourly_api`, identify the exact consumer and required semantics.
- For interval analytics, migrate to a v2 normalized/profile-backed contract only after its coverage, period-end semantics, and unavailable behavior are verified.
- For scalar display paths, use the corresponding native/v2 scalar only where units and meaning match; preserve fail-closed unavailable states.
- Inspect notification automations separately; v2 analytics status must not silently recreate notification behavior.
- Record all changed references and semantic tests before changing live files.

### Stage C — staged migration

- Apply only the reviewed consumer/reference changes in an isolated live staging copy.
- Run `ha core check`; perform controlled restart/reload only with the pre-change backup available.
- Verify actual entity IDs, package loading, dashboard references, statistics, and logs.
- Keep the legacy REST producer available as rollback during the migration validation window.

### Stage D — permanent REST retirement

Only after Stages A–C pass:

- Back up the exact live `/config/packages/energy_split.yaml` and any related live storage.
- Remove only the scoped REST producer and its `update_entity` automation after confirming no remaining consumer references.
- Run `ha core check`, controlled restart, and a fresh post-removal log/entity/consumer audit.
- Confirm no Solar Analytics code or dashboard depends on the removed entity.
- Retain rollback artifacts and run a post-removal soak before calling retirement complete.

## Explicit non-actions now

- No live package removal.
- No REST entity disablement.
- No notification automation mutation.
- No dashboard structural change.
- No provider HTTP request from Solar Analytics.
- No native manual refresh.
- No physical service call.

The migration remains blocked until native admissibility, fresh persistence, quantified soak, and exact live consumer mapping are proven.
