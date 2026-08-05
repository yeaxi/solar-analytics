# Solar Analytics v2 — Final Goal

## Project context

- Repository: `/Users/rdudka/solar_analytics`
- Repository runtime source: `home_assistant/custom_components/solar_analytics/`
- Live destination: `/config/custom_components/solar_analytics/`
- Live Home Assistant: `homeassistant.local`
- Target HA Core: `2026.7.4`
- Reviewed native Forecast.Solar compatibility target: `forecast-solar==5.0.1`
- Specification: `.hermes/plans/2026-08-03-solar-analytics-v2-specification.md`
- Plan: `.hermes/plans/2026-08-03-solar-analytics-v2-plan.md`
- Existing evidence report: `reports/live_verification_2026-08-02.md`

## Objective

Implement Solar Analytics as a standalone, read-only custom integration for native Forecast.Solar forecast analytics and measured PV production.

The only authoritative detailed forecast source is the native Forecast.Solar Energy Dashboard contract:

```python
forecast_solar.energy.async_get_solar_forecast(hass, config_entry_id)
```

The canonical actual-production source is the exact Energy Dashboard binding:

```text
sensor.garage_cerbo_gx_pv_energy
sensor.garage_cerbo_gx_pv_power
```

No REST request, provider polling, REST fallback, synthetic VRM profile, or physical control is allowed.

## Required implementation

### Native contract and binding

- Initially support only HA Core `2026.7.4` under an explicit compatibility/capability guard.
- Consume native `wh_hours` from the observed successful native coordinator update.
- Do not call `estimate()`, `async_request_refresh()`, REST, or provider HTTP.
- Record only local observation time, local sequence, payload SHA-256, and adapter/contract versions; do not claim provider-issued generation or timestamp.
- Require exactly one Forecast.Solar config-entry ID selected by Energy Dashboard and exact canonical actual entity IDs.
- Changed, missing, ambiguous, unloaded, stale, malformed, or unsupported native source fails closed without a REST fallback.
- v1 accepts exactly one valid native plane; unsupported topology is diagnostic/fail-closed.

### Time, snapshots, and history

- Store timestamps in UTC and display in `Europe/Kyiv`.
- Use native period-end values as half-open intervals after boundary validation.
- Compute interval actual energy time-weighted from fresh `sensor.garage_cerbo_gx_pv_power` observations.
- Reconcile with `sensor.garage_cerbo_gx_pv_energy` as cumulative counter reconciliation with explicit unit/reset/decrease handling and v1 tolerance `max(0.1 kWh, 5% of the larger absolute daily power-integral/counter-delta value)`.
- Store the complete native `wh_hours` horizon for scheduled snapshots.
- For target local date `D`, capture:
  - `morning` at `D-1 06:00 Europe/Kyiv`;
  - `day_ahead` at `D-1 23:00 Europe/Kyiv`.
- Use only the morning snapshot as the accuracy baseline for the full target day.
- If the morning slot is unavailable/late/blocked, persist one terminal missing/blocked slot and never substitute latest or day-ahead. A separately marked historical-backfill record may be created only from audited Recorder evidence under the 2026-08-03 amendment; it does not rewrite the scheduled slot.
- Keep ten years of scheduled snapshots, normalized interval/daily results, quality metadata, and lineage; do not persist every internal native refresh.

### Analytics and readiness

- v1 insights cover latest profile, today/tomorrow, completed daily comparison, coverage, quality, and neutral curtailment/external-control diagnostics.
- Do not emit equipment-underperformance claims, notifications, recommendations, or physical actions.
- Use duration-weighted forecast, actual, and paired coverage over the same interval mask.
- Require forecast coverage `>=95%`, actual coverage `>=90%`, and paired coverage `>=90%` for a valid paired day.
- Use exact versioned signed-error, absolute-error, WAPE, bias, and energy-ratio formulas.
- Exclude current/incomplete day and confirmed curtailment/external-control days from accuracy eligibility.
- Keep percentage metrics null for denominator-zero/low-production cases.
- Set `accuracy_ready` only after 14 valid current-lineage morning-baseline days in the previous 30 completed local days.
- Treat `14/30` as runtime statistical status, not deployment authorization.

### Lineage and storage

- Persist a separate `lineage_id` epoch for every valid source/model/actual/normalization/metric transition; `A→B→A` creates three epochs.
- Never pair snapshots/results across lineage epochs.
- Use a versioned SQLite schema with one serialized writer, transactional migrations, WAL-safe backup, integrity checks, disposable restore, and fail-closed disk/corruption/newer-schema handling.
- Store scheduled snapshot headers by `(source_lineage_id, snapshot_type, scheduled_at_utc)` and full-horizon child intervals by `(snapshot_slot_id, interval_end_utc)`.
- Exclude legacy REST rows from native analytics queries; historical backfill may retain them only as explicitly marked `historical_legacy_rest` diagnostics and never reclassifies or rewrites them as native-valid.

## Migration and safety boundaries

The current repository may still contain REST input, old schedules, VRM consensus, recommendations, notifications, and shorter retention. The implementation must explicitly remove or quarantine those v1 behaviors and choose one canonical source tree for duplicate pure/runtime modules.

The legacy `sensor.forecast_solar_hourly_api` can be permanently removed only after:

1. fresh consumer inventory;
2. migration of required consumers;
3. dashboard references removed or separately approved for reference-only substitution without layout changes;
4. scoped package/registry/config-entry/SQLite/WAL/dashboard backups and hashes;
5. at least 72 hours of quantified native read-only soak with at least two valid morning and two valid day-ahead slots;
6. separate fresh approval for the removal step;
7. controlled removal, `ha core check`, restart/readiness, log/entity/Recorder/statistics/dashboard-reference verification;
8. confirmation of no `_2`, orphan registry record, reused statistic ID, or unintended break for surviving canonical entities;
9. post-removal soak and scoped rollback audit.

The legacy Recorder series may end intentionally. Its ID/statistics identity must never be reused or converted into native forecast history.

### Explicit early scoped REST-removal amendment — 2026-08-04

The user selected an early, narrowly scoped removal because the live consumer inventory found no current consumer of `sensor.forecast_solar_hourly_api`; the native source is healthy and the package/candidate/rollback backups are ready. This amendment moves the native replacement-stability soak to post-removal. It does not waive controlled deployment, `ha core check`, restart/readiness, registry/statistics/dashboard verification, rollback evidence, or post-removal soak. The `14/30` runtime accuracy status remains independent and is not required for REST removal.

## Permissions

This Goal defines project scope and acceptance criteria. It is not standing live authorization.

Each live phase requires fresh explicit approval for:

- `/config` writes or deployment;
- SQLite/schema migration;
- controlled Home Assistant restart/reload;
- entity-registry/config-entry/dashboard-reference changes;
- permanent REST entity removal;
- scoped rollback.

No approval in this document authorizes:

- `turn_on`, `turn_off`, `toggle`, or any physical service call;
- boiler, accumulator, ESS, PV, inverter, battery, or load-control actions;
- notification or persistent-notification service calls;
- Forecast.Solar config-entry mutation;
- native `async_request_refresh()`;
- REST/provider HTTP calls from Solar Analytics;
- silent source rebinding;
- latest/day-ahead substitution for a missing morning baseline;
- unqualified reuse of legacy REST history for native accuracy. Explicitly marked historical-backfill diagnostics are allowed under the 2026-08-03 amendment but do not close native soak or native readiness gates;
- secrets, API keys, passwords, connection strings, or credentials in code/logs/docs;
- structural changes to existing dashboards/Lovelace resources.

## Completion states

1. **Design ready:** specification, plan, and Goal are cross-document consistent and all mandatory contracts/gates are explicit.
2. **Implementation ready:** candidate code, schema, tests, pinned real-HA compatibility checks, and disposable restore pass.
3. **Deployment ready:** scoped backups/restore runbook/hashes pass and fresh approval exists for the exact live phase.
4. **Migration complete:** native soak, consumer migration, REST removal, post-removal soak, and rollback audit pass.
5. **Runtime accuracy ready:** independently accumulated 14/30 valid current-lineage morning-baseline days pass the coverage and quality gates.

The task is complete only when the relevant completion state is backed by real local/live evidence. If the native HA contract changes or cannot be verified on HA Core `2026.7.4`, isolate the adapter and remain fail-closed; never silently reintroduce REST as an authoritative fallback.
