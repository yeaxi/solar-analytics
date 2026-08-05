# Solar Analytics v2 — Final Implementation and Migration Plan

> **This is a future staged implementation plan, not standing authorization.** Production writes, SQLite migration, controlled restart, entity-registry changes, dashboard reference changes, and permanent REST removal each require fresh explicit approval after their preceding local/review gate passes.
>
> **Repository runtime:** `home_assistant/custom_components/solar_analytics/`
> **Live runtime:** `/config/custom_components/solar_analytics/`

## Goal

Consolidate Solar Analytics into the existing custom integration, obtain the only authoritative detailed forecast from the native Forecast.Solar Energy Dashboard contract, compare it with canonical PV production, retain ten years of scheduled snapshots/aggregates, and retire the legacy REST producer only after a quantified read-only soak and consumer migration.

## Phase 0 — Contract/source baseline and v1 retirement inventory

1. Inventory all local/live consumers of `sensor.forecast_solar_hourly_api`, its `unique_id`, numeric state, `result` attribute, REST refresh automation, recommendations, notification actions, VRM consensus, and old schedules.
2. Read and record the live Energy Dashboard binding:
   - `sensor.garage_cerbo_gx_pv_energy`;
   - `sensor.garage_cerbo_gx_pv_power`;
   - exactly one Forecast.Solar config-entry ID.
3. Record source units, `state_class`, update cadence, reset/decrease behavior, and Recorder/statistics availability.
4. Capture candidate/live hashes, package/config backups, entity-registry/config-entry backups, SQLite/WAL backup, and dashboard storage hashes.
5. Record observed native model evidence without making it a hardcoded invariant.
6. Choose one canonical source tree for duplicate pure/runtime modules, or enforce byte/hash equality until consolidation is complete.
7. Define the explicit allowlist of public entity contracts that must survive migration.

**Gate:** no code cutover or live mutation until the source/binding/consumer baseline is reproducible.

### Phase 0 read-only evidence — 2026-08-04

The live consumer inventory was completed without writes:

- Legacy producer: `sensor.forecast_solar_hourly_api`, platform `rest`, enabled, no config entry.
- Legacy refresh automation: `automation.solar_analytics_refresh_forecast_solar_rest_after_native_model_change`.
- Legacy Recorder exclusion: `sensor.forecast_solar_hourly_api`.
- Current package references to the REST entity are limited to its producer, refresh automation and Recorder exclusion; no current Lovelace resource references it. Historical `.bak` files and restore-state records were excluded from the live-consumer set.
- Native consumers remain separate: `sensor.power_production_now`, `sensor.energy_production_today`, `sensor.energy_production_tomorrow`, `sensor.energy_current_hour`, and `sensor.energy_next_hour`.
- `sensor.forecast_solar_production_power_kw` is derived from native `sensor.power_production_now`; `sensor.forecast_solar_production_energy_v2` integrates that native-derived power. Neither depends on the REST entity.

This inventory is evidence for a future staged removal only. It does not authorize removal before the native-only soak, history/accuracy gates, backup, staged validation, restart and post-removal checks.

### Explicit early scoped removal amendment — 2026-08-04

The user explicitly selected the narrow early-removal path after reviewing the live inventory: the legacy REST producer has no current Solar Analytics, package, automation, or Lovelace consumers, and the native source is currently healthy. Therefore the REST package may be removed before the pre-removal 72-hour soak, provided the already-completed backup/candidate gates remain valid and all controlled `ha core check`, restart/readiness, entity/registry/statistics/dashboard, log, SQLite, rollback, and post-removal soak checks are still performed. The 72-hour native replacement-stability soak continues after removal. The separate `14/30` accuracy-ready status remains a runtime statistical gate and is not a REST-removal prerequisite.

## Phase 1 — Pinned native adapter RED/GREEN

1. Add a failing contract test for the exact `2026.7.4` native helper signature and `wh_hours` payload.
2. Add tests for import/signature/runtime-data/payload changes and unsupported Core versions.
3. Add tests for native coordinator success observation, stale retained data after failed refresh, unloaded entry, malformed profile, unsupported plane topology, and missing binding.
4. Implement one isolated `ForecastSolarNativeAdapter` with an explicit Core/version capability guard.
5. Observe native coordinator updates; do not call `estimate()`, `async_request_refresh()`, REST, or provider HTTP.
6. Record only local `observed_at_utc`, `observation_sequence`, and `payload_sha256`.
7. Add full-horizon count/digest/sum and numeric/period normalization tests.

**Gate:** all invalid native cases remain diagnostic/fail-closed and produce no admissible profile snapshot.

## Phase 2 — Exact binding, lifecycle, and scheduler

1. Bind exactly one Solar Analytics source to the Forecast.Solar entry selected by Energy Dashboard.
2. Require exact canonical actual entities and validate units/state classes/availability.
3. Add explicit source-change/reconfiguration handling; never silently rebind.
4. Use typed `entry.runtime_data`, coordinator update observation, `entry.add_update_listener()`, and `entry.async_on_unload()`.
5. Implement timezone-aware idempotent scheduled slots:
   - morning at `06:00 Europe/Kyiv` for the next local target day;
   - day-ahead at `23:00 Europe/Kyiv` for the next local target day.
6. Select only an eligible successful native observation already available at or before the slot, within the v1 two-hour maximum age.
7. Persist one terminal missing/blocked slot when a scheduled capture is impossible; never rewrite that scheduled slot from latest/day-ahead. Under the 2026-08-03 historical-backfill amendment, a separate explicitly marked backfill record may be created from audited Recorder evidence, with a separate source kind/lineage and no native-slot mutation.
8. Ensure duplicate callbacks, restart, reload, and timer cleanup do not duplicate slots.

**Gate:** setup/reload/unload/source-unload/options/restart and scheduler tests pass without stale timers or duplicate captures.

## Phase 3 — Storage v2 and ten-year history

1. Design an additive schema migration from the current schema.
2. Add `snapshot_slot` headers keyed by `(source_lineage_id, snapshot_type, scheduled_at_utc)`.
3. Store complete native horizons in child rows keyed by `(snapshot_slot_id, interval_end_utc)`.
4. Add lineage, source kind, adapter/contract version, normalization/metric versions, observation metadata, admissibility, and exclusion reasons.
5. Store latest/current profile as overwrite-only cache; never append every coordinator refresh.
6. Add one serialized DB writer and transactional migrations.
7. Implement online/WAL-safe backup, pre/post `integrity_check`, disposable restore, and fail-closed disk-full/locked/corrupt/newer-schema handling.
8. Implement ten-year retention and indexed daily/seasonal queries.
9. Test synthetic ten-year volume, DB size, startup, indexed query latency, and exact retention boundaries.

**Gate:** disposable migration and restore pass; legacy REST rows remain excluded from all native analytics queries.

## Phase 4 — Actual normalization, intervals, lineage, and metrics

1. Define half-open UTC intervals from native period ends; reject unknown first boundaries, duplicates, overlaps, internal gaps, invalid durations, and midnight ambiguity.
2. Integrate canonical power using source-observation timestamps, unit validation, maximum gap, stale/unknown/negative guards, and time weighting.
3. Reconcile with cumulative energy counter using explicit reset/decrease, local-day anchor, unit, and tolerance rules.
4. Implement duration-weighted forecast/actual/paired coverage over one eligible interval mask.
5. Implement `morning` accuracy for the full next target local day only; exclude current/incomplete days.
6. Implement exact v1 metrics and version them:
   - daily signed error;
   - daily absolute error;
   - rolling WAPE;
   - rolling bias;
   - energy ratio.
7. Implement aggregate-energy floor and denominator-zero behavior.
8. Exclude confirmed curtailment/external-control days from accuracy eligibility; keep neutral diagnostics; never emit equipment-underperformance claims.
9. Create a new persisted `lineage_id` for every valid source/model/actual/normalization/metric transition, including `A→B→A`.
10. Enforce current-lineage and no-cross-epoch pairing in every analytics query.

**Gate:** RED/GREEN tests cover DST, counter resets, source gaps, current-day exclusion, fixed-baseline leakage, model changes, low production, and all coverage gates.

## Phase 5 — Current-v1 retirement in the candidate

Explicitly remove or quarantine behavior that conflicts with v2:

- REST as analytics input and REST provenance path;
- old `20:00`/sunrise-minus-60 schedule;
- VRM consensus/profile inference;
- recommendation service/storage and `record_recommendation_applied`;
- persistent notifications and notification options;
- 400/210-day retention assumptions;
- latest/day-ahead morning fallback settings;
- any provider URL/network client;
- any notification or physical service call.

Preserve only the approved entity compatibility allowlist until the separate consumer migration gate.

**Gate:** static scans and focused tests prove zero provider network calls, zero notification calls, zero physical calls, no unqualified retroactive forecast, and no legacy REST input to native analytics. Historical backfill code must be source-qualified and isolated as a separate capture mode.

## Phase 6 — Full candidate and disposable real-HA gate

1. Run local pytest, compileall, YAML/resource parsing, copy/hash drift checks, credential scan, and static safety scan.
2. Run the component candidate against a disposable HA Core `2026.7.4` environment with the native Forecast.Solar compatibility package pinned to `forecast-solar==5.0.1`.
3. Verify exact native profile correspondence, source binding, slot identity, interval normalization, lineage, SQLite migration, and fail-closed behavior.
4. Exercise reload, unload, restart, unavailable native source, changed model, stale source, missing morning slot, and DB restore.
5. Prepare scoped backup/restore and a live runbook.

**Gate:** implementation-ready, but no live mutation yet.

## Phase 7 — Separately approved native production deployment

Only after a fresh approval for this phase:

1. Create timestamped scoped backups and SHA-256 manifests.
2. Deploy only the affected custom integration, package, schema, and documentation files.
3. Run `ha core check` before restart.
4. Perform one controlled restart and verify readiness.
5. Read back source/profile status, canonical actual entities, logs, slot rows, lineage, SQLite integrity, entity registry, and hashes.
6. Do not execute any physical or notification service call.

**Gate:** live native cutover is healthy and fail-closed where expected.

## Phase 8 — Quantified read-only soak

Before REST removal, require at least:

- `72` hours of native-only read-only runtime;
- at least two valid morning snapshots;
- at least two valid day-ahead snapshots;
- one successful reload or controlled restart;
- exact native profile/source correspondence;
- no duplicate slots or unexpected refresh-level row growth;
- no new Solar Analytics errors;
- SQLite integrity and query-size checks;
- disposable rollback restore tested.

The `14/30` accuracy-ready status is not required to deploy or remove REST; it is a runtime statistical gate.

## Phase 8A — Authorized historical Recorder backfill

This phase was added by explicit user direction on 2026-08-03. It may run in parallel with, but cannot replace, the native-only soak.

1. Perform a read-only Recorder audit for canonical actual power/energy and every candidate Forecast.Solar detailed profile entity. Record entity identity, platform/provenance, timestamp range, units, state classes, attribute keys, row counts, and gaps without printing credentials or unbounded payloads.
2. Admit only exact canonical actual entities. Integrate Recorder power observations time-weightedly with the same stale/gap/DST rules as live v2; reconcile the cumulative counter and quarantine discrepancies.
3. Admit a forecast history only when its detailed period values, period-end semantics, source identity, and provenance are auditable. Scalar “now/today” values are insufficient. A legacy REST series is explicitly `historical_legacy_rest`, never `native`.
4. Add an additive, idempotent backfill schema/run identity. Store `capture_mode=historical_backfill`, source kind, source lineage, source timestamps, row/payload digest, and quality/exclusion reason. Do not rewrite existing scheduled slots, legacy rows, or the current native lineage.
5. Compute separate retrospective backfill intervals/daily comparisons/accuracy results. Publish them with an explicit backfill status; do not use them to claim native freshness, close the 72-hour soak, or set native `accuracy_ready`.
6. Before live SQLite mutation, pass local tests, compile/resource checks, schema migration/restore tests, a read-only Recorder evidence report, a timestamped SQLite/WAL backup, SHA-256 verification, `ha core check`, and post-write integrity/readback. The user's explicit backfill request is the approval for this scoped data phase; it does not authorize source/config/physical changes.

**Gate:** historical data are either admitted with explicit provenance and separate backfill status or remain excluded with a reason; no scheduled native slot or legacy REST record is reclassified.

## Phase 9 — Separately approved consumer migration and REST removal

Only after fresh approval for this destructive compatibility step:

1. Re-run repository/live consumer inventory, including exact `result` attribute references and dashboard references.
2. Migrate required consumers to approved native/custom entities.
3. If a dashboard references the legacy entity, either remove the reference or obtain separate approval for a reference-only substitution that preserves layout/card structure.
4. Back up REST package, entity registry, config entries, SQLite/WAL, and affected dashboard storage.
5. Remove REST producer, refresh automation, legacy entity, and obsolete compatibility package fragments.
6. Run `ha core check`, one controlled restart, readiness, logs, entity registry, Recorder/statistics, and dashboard-reference checks.
7. Confirm no `_2`, orphan registry record, reused statistic ID, or unintended break for surviving canonical entities. The legacy series may end intentionally and must never be reclassified as native history.
8. If any check fails, restore the scoped compatibility layer; never restore REST as authoritative analytics input.

## Phase 10 — Post-removal soak and seasonal readiness

1. Soak after removal and run the rollback audit.
2. Verify current/latest cache versus scheduled history separation.
3. Verify model `A→B→A` lineage behavior and no cross-epoch pairing.
4. Verify current/tomorrow UI and daily comparison after valid paired days.
5. Keep accuracy `insufficient_data` until 14 valid current-lineage morning-baseline days in the previous 30 completed local days.
6. Complete final dashboard structural/reference audit, backup restore evidence, hashes, documentation, and release status.

## Required evidence

Local:

```text
python3 -m pytest -q
python3 -m compileall -q solar_analytics home_assistant/custom_components/solar_analytics
YAML/resource parsing for affected files
copy/hash drift, credential, notification, network, and physical-call scans
synthetic ten-year storage/latency results
```

Live:

```text
ha core check
controlled restart/readiness HTTP 200
native binding/profile/actual entity checks
snapshot slot/lineage/admission checks
logs filtered for new Solar Analytics/Forecast.Solar failures
SQLite schema/integrity/row-growth/retention checks
entity registry, Recorder/statistics, and dashboard-reference checks
SHA-256 candidate/live comparison
```

## Scoped rollback

Rollback is not a full Supervisor restore unless separately approved. Restore only the affected component/package/schema/registry/dashboard-reference slice, verify SQLite/WAL integrity and readiness, and leave analytics fail-closed if native provenance is unavailable.
