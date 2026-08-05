# Solar Analytics v2 — Final Specification

**Review status:** Sol review recommendations incorporated
**Project:** `/Users/rdudka/solar_analytics`
**Repository runtime path:** `home_assistant/custom_components/solar_analytics/`
**Live destination:** `/config/custom_components/solar_analytics/`
**Target HA:** Home Assistant Core `2026.7.4`

## 1. Purpose and scope

Solar Analytics is a standalone, read-only Home Assistant custom integration for comparing the native Forecast.Solar forecast with measured PV production and publishing explainable analytics, forecast snapshots, data-quality status, and multi-year seasonal history.

The integration does not plan or control energy loads. Energy automations, planners, physical executors, ESS/PV/inverter/battery control, relay safety supervisors, and load recommendations are separate projects and are outside this specification.

The v2 design is a design/implementation contract. It does not by itself authorize live writes, storage migration, restart, registry edits, or permanent legacy entity deletion; each live phase needs a fresh explicit approval after its preceding gates pass.

## 2. Target architecture

The existing `custom_components/solar_analytics` becomes the single owner of:

- native forecast acquisition through the Home Assistant Forecast.Solar energy-platform contract;
- canonical actual-PV and scalar-context adapters;
- normalization and quality gates;
- scheduled snapshot capture;
- versioned SQLite persistence;
- deterministic forecast-vs-actual metrics;
- entities, diagnostics, and lifecycle;
- config/options and migration logic.

During migration, YAML may remain only as a compatibility shell. It must not remain an authoritative analytics input or contain the v2 forecast/accuracy logic. The legacy Forecast.Solar REST entity is not read by v2 analytics and is removed only by the separately gated migration described below.

The integration does not manage Lovelace storage/resources and does not absorb physical-control projects.

## 3. Authoritative data contracts

### 3.1 Native Forecast.Solar contract

The authoritative detailed forecast is the same native Energy Dashboard backend contract:

```python
homeassistant.components.forecast_solar.energy.async_get_solar_forecast(
    hass,
    config_entry_id,
)
```

On the pinned target Home Assistant Core `2026.7.4` with the reviewed Forecast.Solar compatibility package target `forecast-solar==5.0.1`, this returns a native profile containing `wh_hours`. The adapter consumes that profile and does not reconstruct a URL, call `estimate()`, invoke `async_request_refresh()`, issue a provider HTTP request, or use the REST entity as a fallback.

This import/runtime surface is an HA internal integration-platform contract, not an eternal public API. v2 initially allows only Home Assistant Core `2026.7.4`; another Core version requires the pinned compatibility suite and an explicit adapter allowlist update. Missing import, changed signature, changed runtime-data type, malformed payload, unsupported version, unloaded entry, or failed native update produces `unsupported_native_contract` or `native_source_unavailable`. The integration remains available for diagnostics but persists no admissible forecast snapshot.

The helper itself does not provide a provider-issued `issued_at` or generation and may expose retained data after a failed refresh. Solar Analytics therefore listens for/observes a successful native coordinator update and records only locally owned metadata:

```text
observed_at_utc
observation_sequence
payload_sha256
native_contract_version
```

These fields are local observation evidence. They must never be described as provider attestation or provider generation. If the adapter cannot prove that the observed profile came from a successful native update, it fails closed.

The v1 native target is exactly one valid native Forecast.Solar plane. A missing or ambiguous plane contract, or a native entry with unsupported plane topology, is `unsupported_native_contract` rather than a guessed or partially normalized profile.

### 3.2 Exact Energy Dashboard binding

The live Energy Dashboard storage identifies the canonical solar source as:

```text
actual energy: sensor.garage_cerbo_gx_pv_energy
actual power:  sensor.garage_cerbo_gx_pv_power
```

It also identifies the native Forecast.Solar config entry used for the solar forecast. The Solar Analytics runtime binding is valid only when:

- exactly one Forecast.Solar config-entry ID is selected for the Energy Dashboard solar source;
- that ID is the stored Solar Analytics native binding;
- the actual energy and power entities exactly match the canonical IDs above;
- all selected entities pass unit/state-class/availability validation.

There is no silent manual override or silent rebind. A missing, changed, or ambiguous Energy Dashboard binding fails closed and requires explicit reconfiguration. The config flow may display the discovered binding and provide an explicit reconfiguration path, but it must not permit an analytics source that diverges from the authoritative Energy Dashboard contract.

The exact observed native model values are deployment evidence, not hardcoded invariants. Model/config changes are handled through lineage transitions below.

### 3.3 Actual PV semantics

`sensor.garage_cerbo_gx_pv_power` is the canonical runtime actual-power source. Its source-observation timestamps, not Solar Analytics poll timestamps, are used for integration. A documented maximum-gap rule is required; v1 uses the source's validated update cadence with a hard maximum observation gap of 15 minutes. If cadence or freshness cannot be established, the affected interval is invalid.

`sensor.garage_cerbo_gx_pv_energy` is a cumulative energy counter used for reconciliation and local-day counter-delta checks. The adapter validates its unit and `state_class`, handles reset/decrease, missing values, and midnight anchors, and never treats a counter decrease as negative production. The canonical interval actual remains the time-weighted power-derived energy; counter discrepancies beyond the versioned reconciliation tolerance block or quarantine the affected day and are reported diagnostically.

The v1 reconciliation tolerance is `max(0.1 kWh, 5% of the larger absolute daily power-integral/counter-delta value)`. The normalization version must record the exact unit conversions, maximum gap, reset/decrease handling, and this tolerance. A discrepancy above the tolerance quarantines the affected day. No implementation may silently choose between power integration and lifetime counter deltas.

### 3.4 VRM

Victron VRM remains scalar-only. It may provide current/today/tomorrow context, but no hourly VRM profile may be synthesized or inferred from scalar totals. VRM does not participate in native profile readiness, native accuracy, or consensus/fallback selection.

## 4. Time and interval semantics

- Canonical storage timestamps are UTC.
- User-facing dates/times use `Europe/Kyiv`.
- A native period-end value represents energy for the half-open UTC interval `[previous_period_end, period_end)`.
- The first point is inadmissible if its previous boundary is unavailable.
- An interval ending at local midnight is assigned by interval overlap, not by endpoint calendar date.
- A helper-filtered zero point at exactly `00:00:00` is not silently reconstructed; the affected boundary is accepted only when the preceding interval boundary is independently known.
- Internal gaps, overlapping periods, duplicate timestamps, non-finite values, negative values, or unreasonably long native periods are invalid.
- Forecast and actual values are energy per interval. Instantaneous watts are telemetry, not forecast energy.
- Actual energy is time-weighted from fresh power observations onto the same native forecast intervals.
- Missing, stale, restored, unknown, or unproven samples never become zero.
- DST 23/24/25-hour local days are handled by UTC interval arithmetic.

### 4.1 Coverage

For a completed target local day, first construct one eligible interval mask from the fixed morning forecast horizon after boundary and semantic validation. Then calculate duration-weighted values over the same mask:

```text
forecast_coverage = valid_forecast_duration / eligible_duration
actual_coverage   = valid_actual_duration / eligible_duration
paired_coverage   = valid_forecast_and_actual_duration / eligible_duration
```

A valid paired day requires:

```text
forecast_coverage >= 95%
actual_coverage   >= 90%
paired_coverage   >= 90%
```

Coverage denominators are duration-based, not row-count based. Incomplete first/last boundary intervals do not create denominator gaming.

## 5. Forecast snapshots

A scheduled occurrence has one immutable header:

```text
snapshot_slot key = (source_lineage_id, snapshot_type, scheduled_at_utc)
```

`target_local_date`, timezone, observed native sequence, payload digest, admissibility, and exclusion reason are attributes, not uniqueness dimensions. The complete native horizon is stored in child rows:

```text
(snapshot_slot_id, interval_end_utc)
```

The native observation digest/generation must not create multiple historical snapshots for one scheduled slot. One hundred internal coordinator refreshes outside a scheduled slot must not create one hundred history rows.

### 5.1 Fixed baseline

For a target local date `D`:

```text
morning snapshot:  D-1 06:00 Europe/Kyiv → target D
 day-ahead snapshot: D-1 23:00 Europe/Kyiv → target D
```

The `morning` snapshot is the sole accuracy baseline. The `day_ahead` snapshot is stored as a separately identifiable diagnostic/baseline candidate but never replaces a missing morning snapshot.

At each scheduled instant, capture only a native observation that:

- was observed at or before the scheduled instant;
- came from a successful native coordinator update observed by the adapter;
- is no older than the v1 maximum native observation age of two hours;
- passes the native profile, numeric, period, freshness, and binding gates.

A later observation must not be retroactively labeled as the scheduled slot. If Home Assistant was down, the native entry was unavailable, or no eligible observation existed, persist exactly one terminal `missing` or `blocked` slot. Do not rewrite it from latest, day-ahead, or a later refresh. The 2026-08-03 historical-backfill amendment permits separate `historical_backfill` records from audited Recorder evidence; those records must not mutate this scheduled slot or claim native scheduled provenance.

The scheduler is timezone-aware and idempotent across restart/reload. A slot may transition from `pending` to exactly one terminal state; duplicate callbacks do not create duplicate slots.

Latest/current native profile is an overwrite-only diagnostic cache and does not create unbounded historical snapshots.

### 5.2 Retention

Retain for ten years:

- scheduled morning/day-ahead forecast snapshots and their full native horizons;
- normalized interval actual/forecast aggregates;
- daily comparison and accuracy results;
- source/model/normalization/metric lineage;
- quality and exclusion metadata.

Do not persist every internal native refresh. Use normalized/indexed tables, bounded current-profile cache, and indexed seasonal queries. Ten-year synthetic data must be used to validate storage size, startup, and query latency.

## 6. Lineage

Persisted `lineage_id` is an epoch, not merely a fingerprint. Every valid transition away from the previous source contract creates a new lineage, even if a later configuration returns to the earlier fingerprint (`A → B → A` creates three epochs).

A new lineage is required for changes to any of:

- native Forecast.Solar config-entry identity or recreation;
- native model contract or ordered plane topology;
- canonical actual source binding;
- normalization version;
- metric/algorithm version;
- native adapter compatibility contract.

Unavailable, malformed, or stale reads do not create a new lineage. No snapshot, interval, daily result, or accuracy metric may pair records from different source, actual, normalization, metric, or lineage epochs. A model change after an existing morning snapshot leaves that snapshot in its old lineage; it cannot be paired with a new-lineage actual result.

## 7. v1 analytics and metrics

The first release publishes:

- current/latest native forecast profile;
- today and tomorrow profile/summary views;
- actual PV production;
- completed-day forecast-vs-actual comparison;
- duration-weighted coverage;
- source, model, freshness, and quality status;
- neutral curtailment/external-control diagnostics;
- accuracy only after the history gate.

The first release does not publish:

- equipment-underperformance claims;
- physical-load recommendations;
- automatic notifications or persistent notifications;
- synthetic VRM curves;
- unqualified retroactive forecast reconstruction;
- automatic changes to existing dashboards or Lovelace resources.

### 7.1 Accuracy formulas

For each eligible completed target day, using the fixed morning forecast only:

```text
daily_signed_error_kwh   = actual_kwh - morning_forecast_kwh
daily_absolute_error_kwh = abs(actual_kwh - morning_forecast_kwh)
rolling_wape              = sum(abs(actual - forecast)) / sum(actual)
rolling_bias              = (sum(actual) - sum(forecast)) / sum(forecast)
energy_ratio              = sum(actual) / sum(forecast)
```

Sign convention: positive `daily_signed_error_kwh` and positive `rolling_bias` mean actual production exceeded forecast; negative means forecast exceeded actual.

Rules:

- the current incomplete local day is excluded;
- only completed current-lineage days with a fixed morning baseline participate;
- percentage metrics are `null` below an explicit aggregate-energy floor of `0.1 kWh` for the relevant denominator;
- denominator-zero cases are `null`, never zero;
- confirmed curtailment/external-control days are excluded from accuracy eligibility and retained as diagnostics;
- unknown control status may remain a neutral forecast-error observation but never enables an equipment-underperformance claim;
- VRM scalars never alter native accuracy;
- `metric_version` is persisted with each result.

### 7.2 Readiness

Statuses are independent:

- `profile_ready`: current native profile is valid; no historical requirement;
- `daily_comparison_ready`: at least one valid paired day exists;
- `accuracy_ready`: at least 14 valid paired days exist in the rolling previous 30 completed local days, all from the current lineage and passing coverage;
- `underperformance_insight`: not emitted by v1; neutral diagnostics explain exclusions instead.

Before `accuracy_ready`, expose `insufficient_data` with valid-day, coverage, excluded-day, and reason counts. The 14/30 runtime status is not a software-deployment gate.

## 8. Persistence and storage safety

The integration owns `/config/solar_analytics/solar_analytics.sqlite`.

Requirements:

- versioned additive schema migrations;
- one serialized DB writer and no blocking SQLite work on the event loop;
- transactional migrations;
- online backup including WAL state, or quiesced backup with verified WAL handling;
- `integrity_check` before/after migration;
- disposable restore test;
- explicit handling of disk-full, locked, corrupt, and newer-unsupported schema;
- idempotent snapshot and result keys;
- `normalization_version`, `metric_version`, `lineage_id`, source kind, adapter version, observation sequence, payload digest, acquisition time, admissibility, and exclusion reason;
- query-time gate requiring `admissible=1`, `source_kind=native`, and current compatible lineage for accuracy;
- no secrets, API keys, passwords, connection strings, or raw credentials;
- no unbounded forecast payload in entity attributes or logs.

Existing invalid REST rows remain excluded from native analytics queries. Under the historical-backfill amendment, a separately captured legacy REST record may be retained only with `source_kind=historical_legacy_rest` and explicit backfill status; it is never reclassified as native-valid or retroactively rewritten.

## 9. Integration lifecycle and configuration

Use:

- one Solar Analytics config entry with defensive singleton validation;
- typed `entry.runtime_data`;
- an isolated `ForecastSolarNativeAdapter`;
- a `DataUpdateCoordinator` for observed native/actual/context data and published analysis;
- native coordinator update observation rather than self-triggered native refresh;
- `entry.add_update_listener()` for options/reconfigure;
- `entry.async_on_unload()` for every listener/timer/resource;
- timezone-aware idempotent scheduled slots;
- degraded source/quality diagnostics while analytics is blocked.

Config flow follows the exact Energy Dashboard binding and canonical actual entities. It does not ask the user to duplicate native geometry, capacity, inverter, damping, or credentials. Every user-facing field has an inline description explaining its meaning, use, and analytics effect.

## 10. Entities, dashboards, and migration boundaries

The custom integration publishes stable typed entities for source status, profile status, current profile cache, daily comparison, coverage, accuracy, last update, lineage, and explainable quality reasons. Large profiles/history must be served through bounded storage-backed access or future read-only API, not unbounded entity attributes.

Existing Energy Dashboard, Energy Split Dashboard, and Solar Forecast Comparison Dashboard are not structurally edited. If a dashboard references the legacy REST entity, permanent removal is blocked until either:

- the reference is removed, or
- a separately approved reference-only substitution is made without changing layout/card structure.

No broken card is acceptable.

### 10.1 Current-v1 retirement scope

The migration must explicitly remove or quarantine old behavior that conflicts with v2:

- REST entity as analytics input;
- old 20:00/sunrise-minus-60 scheduling;
- VRM consensus/profile inference;
- recommendation service and recommendation application state;
- persistent notifications and notification options;
- old 400/210-day retention assumptions;
- user-editable morning fallback settings that can substitute latest/day-ahead data.

Preserve only an explicit allowlist of entity contracts actually required by approved dashboards or external consumers until their migration is verified. The repository currently contains duplicate pure/runtime modules; the implementation must choose one canonical source tree or enforce byte/hash equality as a temporary guard and test against drift.

## 11. Safety and privacy

Solar Analytics v1 is read-only:

- no physical service calls;
- no notification service calls;
- no relay, boiler, accumulator, ESS, inverter, battery, PV, or load-control actions;
- no Forecast.Solar config-entry mutation;
- no native coordinator refresh request;
- no provider HTTP/network client;
- no automatic forecast corrections;
- no secrets in fingerprints, logs, diagnostics, snapshots, or exports.

Any future executor consumes only a narrow versioned analytics contract and independently validates it; it must not access coordinator internals or SQLite directly.

## 12. Acceptance criteria

### Design ready

- This specification, plan, and Goal agree on source, time, snapshot, lineage, metrics, retention, migration, and safety boundaries.
- Native compatibility is explicitly pinned to HA Core `2026.7.4` and guarded.

### Implementation ready

- native adapter, exact binding, actual aggregation, interval/DST, snapshot scheduler, lineage, storage migration, metrics, and lifecycle tests pass;
- synthetic ten-year dataset passes size/query/retention checks;
- blocked/missing native data cannot create an admissible snapshot or unlock accuracy;
- no current/latest/day-ahead substitution can enter morning accuracy.

### Deployment ready

- local and disposable real-HA `2026.7.4` compatibility gates pass;
- backups, WAL/integrity checks, restore runbook, hashes, and rollback are verified;
- fresh explicit approval exists for the specific live phase.

### Migration complete

- at least 72-hour quantified native read-only soak completed with at least two valid morning and two valid day-ahead slots;
- one successful reload or controlled restart;
- no new Solar Analytics errors, duplicate slots, or unexpected DB growth;
- consumer inventory is zero for legacy dependencies or approved reference-only substitutions are complete;
- REST entity removal leaves no `_2`, orphan registry record, reused statistic ID, unintended surviving Recorder/statistics break, or broken dashboard card;
- post-removal soak and scoped rollback audit pass.

### Runtime accuracy ready

- independently accumulated 14 valid current-lineage morning-baseline days in the previous 30 completed local days.
