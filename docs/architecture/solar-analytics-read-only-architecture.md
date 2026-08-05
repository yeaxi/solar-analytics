# Read-only Solar Analytics dashboard architecture

Use this reference when Home Assistant must gain a separate solar forecast/actual analytics dashboard without Solcast, without modifying existing dashboards, and without any physical service calls. It captures the safest staged architecture for installations that already expose Forecast.Solar period data, VRM forecast scalars, canonical Cerbo PV telemetry, and Recorder statistics.

## Core decision

Deliver in two stages rather than forcing persistence, resampling, long-term analytics, and UI into one YAML dashboard:

1. **Basic:** isolated storage-mode Lovelace dashboard, optionally accompanied by template-only sensors that retain only the latest day-ahead and morning scalar snapshots.
2. **Extended:** a read-only custom integration that normalizes provider contracts, persists immutable snapshot archives through Home Assistant `Store`, computes deterministic 30-minute actual/forecast buckets, and serves bounded historical ranges to a read-only card.

The basic stage must not imply that it has a historical forecast archive. The extended stage must not use Recorder state history as the authoritative snapshot archive.

## Proven source-contract findings

### Canonical actual production

Use the Cerbo aggregate power entity as instantaneous actual PV and its aggregate daily utility meter as the daily actual. Keep a single-MPPT `yield_today`, voltage, current, state, error, and mode as diagnostics only. Never add a single MPPT value to the Cerbo aggregate.

For 30-minute analytics, require an actual power sensor with:

- `device_class: power`;
- `state_class: measurement`;
- a supported power unit, normally W;
- short-term Recorder statistics with numeric `mean` values.

A cumulative integration sensor or daily utility meter remains useful as a daily cross-check, but it is not sufficient proof that every 30-minute bucket was observed.

### Forecast.Solar period entity

Inspect the configured REST URL before inferring units from values or names. A source using:

```text
/estimate/watthours/period/...
```

returns `result` values in **Wh per forecast period**. Noon values around 4,000 are therefore not evidence that the array is watts. Convert Wh to kWh exactly once.

Do not consume the entity's scalar state as a daily or hourly value when its template sums all entries in `result`: one response can contain today and tomorrow, so the state can be the sum of multiple target dates. Parse the `result` mapping, partition it by target local date, and treat the scalar state as informational only.

The period entity should remain excluded from Recorder because its large current/future JSON attribute is not a historical forecast archive. Old forecast revisions cannot be reconstructed from the current API response.

### Native Forecast.Solar versus REST model identity

Read the native Forecast.Solar config entry and plane subentry read-only from the structured `data.entries` collection in `/config/.storage/core.config_entries`. In persisted storage, `subentries` may be a mapping; iterate its values, not its keys. Compare and fingerprint:

- `modules_power`;
- declination;
- HA azimuth versus API azimuth (`api_azimuth = ha_azimuth - 180`);
- inverter/damping options where applicable;
- timezone and issue time.

For this user's analytics, native Forecast.Solar is authoritative. Derive every REST request from those native values; do not maintain a second static kWp, inverter limit, geometry, or damping value. If the existing REST period URL/entity uses a different capacity or geometry, mark `blocked_model_mismatch` and do not blend totals, validate one model with the other, or rank them as if only forecast error explains the difference. Reparameterizing the REST producer is a live configuration change and requires backup plus explicit approval; until then, expose the mismatch and keep the dashboard read-only.

A verified `/watthours/period` payload contains Wh per irregular period even if an HA entity declares `kWh`. Confirm whether each timestamp is a period start or end before converting/rebinning. Unknown semantics must fail closed as `blocked_timestamp_semantics`; never relabel Wh as instantaneous W or synthesize unresolved boundary intervals.

### VRM forecast limitations

A VRM integration that exposes only yesterday/today/tomorrow totals, current/next-hour energy, and peak timestamps is a scalar source. It can support daily day-ahead/morning comparisons and current/next-hour cards, but it cannot support a deterministic 30-minute curve.

Never manufacture a VRM interval profile by spreading a daily total across 48 buckets or shaping it around a peak timestamp. A real timestamped interval array is required before VRM participates in interval-level metrics.

### Solcast denylist

Do not rely on the absence of a Solcast config entry. Registry ghosts such as old Solcast sensors or helper entity IDs can remain after removal. Configure exact source IDs/registry identities and add a static denylist scan for:

- `solcast`;
- `p10`, `p50`, `p90`;
- `estimated_actual`;
- `dampening`;
- legacy Solcast accuracy/error helpers.

Do not delete those ghosts as part of dashboard delivery unless a separate reference and dependency audit authorizes registry cleanup.

## Basic stage

### Dashboard boundary

Register a new dashboard ID and URL, for example:

```text
id: solar_analytics
url_path: solar-analytics
mode: storage
```

Create only its own `lovelace.solar_analytics` config. Prefer the supported Lovelace UI/WebSocket API. If direct storage registration is approved, back up and hash both the dashboard registry and new dashboard file, validate the `data.config` wrapper, and assert that every pre-existing registry item and every existing dashboard file hash is unchanged.

Reuse already-registered chart resources when verified HTTP 200. Avoid a new global resource in the basic stage.

The dashboard is strictly read-only:

- sensors and diagnostic entities only;
- no switches, selects, buttons, or service-backed controls;
- no `toggle`, `call-service`, or `perform-action` tap/hold actions;
- use `more-info` or navigation only;
- no heavy Jinja markdown that rerenders on broad state changes.

### Latest-snapshot persistence

If basic persistence is required, use a dedicated template-only package, not automations or helper writes. Trigger-based template sensors may preserve only the latest day-ahead and morning scalar snapshots.

Each snapshot must publish:

- provider and exact source entity;
- `target_date`;
- `slot` (`day_ahead` or `morning`);
- scheduled local time/window;
- `captured_at` and provider `source_reported_at`;
- timezone;
- daily kWh value;
- `quality` (`valid`, `missing`, `late`, `invalid_contract`, or `model_mismatch`).

A robust default is the first fresh provider report in a bounded window after an agreed local slot, such as 18:00–18:15 for day-ahead and 06:00–06:15 for morning. On startup inside the window, capture the first valid fresh report. After the window, mark the slot missing; do not relabel a later revision as the scheduled snapshot.

This restores the latest snapshot after restart but is not a durable multi-day archive. Recorder purge and missed downtime prevent historical guarantees.

## Extended read-only integration

Build a custom integration only after the source contracts and capture slots are frozen. It should:

- read exact existing entities rather than perform duplicate provider polling;
- register sensor/binary-sensor diagnostics only;
- register no physical services and contain no `hass.services.async_call` path;
- use `Store` for snapshots/runtime state;
- use supported Recorder statistics APIs rather than direct production SQL;
- execute statistics queries outside the event loop;
- expose a bounded read-only WebSocket range API for the historical card.

### Snapshot identity and schema

Use an idempotent key:

```text
schema | provider | target_date | day_ahead|morning
```

Persist a self-describing record containing:

- schema version;
- provider/adapter identity;
- target date and slot;
- scheduled/captured/source-reported timestamps;
- HA timezone;
- model capacity and a geometry/config fingerprint, not coordinates or credentials;
- source quantity/unit and period timestamp semantics;
- original target-date total;
- normalized interval array when supported;
- deterministic payload digest;
- completeness and quality reason.

Store Forecast.Solar day-ahead and morning profiles separately. Never overwrite one with the other or score a later live revision as the original snapshot.

### Crash-safe storage layout

Avoid rewriting a growing year archive twice per hour:

- a small runtime Store contains current-day buckets, capture markers, and pending commits;
- monthly archive shards contain immutable completed days/snapshots;
- persist a captured snapshot immediately;
- persist each completed actual bucket to runtime;
- finalize/archive a day idempotently after rollover;
- save archive first, then advance the runtime commit pointer;
- on save/schema failure, retain an explicit storage fault and retry without claiming the record committed.

Use a configurable retention window with hard record/byte caps, for example about 400 days and 32 MiB total. Rotate only the oldest completed shard, never the active month. Keep startup backfill bounded (for example 72 hours); older unrecoverable gaps remain missing.

## Deterministic 30-minute aggregation

### Time model

Define local wall-clock boundaries at `HH:00` and `HH:30`, then convert each bucket to half-open UTC intervals:

```text
[start_utc, end_utc)
```

Store the local date/label and UTC offset. Do not assume 48 buckets every day: DST transition days can contain 46 or 50 local half-hour intervals.

### Actual energy

Prefer six completed 5-minute Recorder statistic means for each bucket:

```text
actual_kWh = sum(mean_W * 300 seconds) / 3_600_000
```

A strict bucket is valid only when all expected samples are present, finite, and unit-compatible. Publish `sample_count`, `covered_seconds`, and a gap reason. Missing or partial data is `null`, never zero. Do not extrapolate across HA downtime.

Run after Recorder has finalized all six statistics, with a bounded retry window. On restart, backfill only completed buckets within the configured bound.

### Forecast energy

Before rebinning, prove whether each Forecast.Solar key marks a period start or period end. Irregular sunrise/sunset timestamps show that keys are not automatically exact one-hour buckets.

After the contract is known, split each period's Wh into overlapping 30-minute buckets proportionally by duration:

```text
bucket_Wh += period_Wh * overlap_seconds / period_duration_seconds
```

Use deterministic Decimal/rational arithmetic and round only for presentation. Acceptance requires conservation: the normalized bucket sum equals the original target-date period sum within the explicit Decimal tolerance.

Treat missing internal intervals, duplicate/non-monotonic timestamps, naive timestamps, invalid units, negative/non-finite values, and unresolved first/last period boundaries as contract failures. Do not silently fill them with zeros.

## Analytics contract

Keep day-ahead and morning metrics separate. Calculate metrics only from buckets where both forecast and actual are valid.

Recommended neutral outputs include:

- signed error kWh;
- absolute error kWh;
- bias;
- MAE/RMSE;
- WAPE or daily energy ratio;
- valid-bucket count and coverage percentage.

Percentage error near zero actual production should be `null` below a documented floor; never let sunrise/night buckets dominate the score. Daily scalar comparisons may remain available when interval coverage is incomplete, but label them separately from strict 30-minute analytics.

VRM remains daily-only until it exposes a verified interval array.

## Resource budget

For a constrained HA host with a multi-gigabyte Recorder DB and hundreds of sensors:

- do not create one entity per provider/bucket;
- do not record full arrays as frequently changing entity attributes;
- keep the existing REST JSON entity excluded from Recorder;
- expose roughly 8–12 summary/health entities rather than 48×provider entities;
- perform at most two routine bucket closures per hour plus two snapshot captures per day;
- cap range-query days/rows and cache bounded results;
- render one or two charts rather than many global-state Jinja templates.

Long-term 30-minute navigation needs a read-only custom card/WebSocket consumer. Existing Recorder cards can show recent recorded summary entities, but they cannot reconstruct an arbitrary historical snapshot archive stored in `Store`.

## Acceptance and blockers

### Basic acceptance

- New dashboard registry item only; all existing dashboard hashes unchanged.
- Correct `data.config` storage wrapper and route HTTP 200.
- Existing resource URLs return HTTP 200.
- Exact source allowlist and zero forbidden Solcast references.
- Zero physical/toggle/service actions in dashboard/package.
- Missing/late snapshot behavior is visible and tested.
- No claim of historical forecast backfill.

### Extended acceptance

- Forecast.Solar period unit and timestamp semantics proven.
- Model-capacity mismatch either resolved with approval or displayed as separate contracts.
- Six-sample actual aggregation, missing data, DST 46/50 buckets, and forecast conservation tested.
- Snapshot capture is exactly once per slot and restart-safe.
- Store failure, retry, migration, retention, and duplicate commit paths tested.
- VRM interval analytics remain unavailable rather than synthesized.
- Static scan shows no physical service calls, control entities, or forbidden provider terms.
- Local tests and a real-HA compatibility smoke pass before any approved deployment.

### Live approval boundary

Read-only inventory and local parser/card tests may proceed. Require explicit approval for:

- writes under `/config` or `.storage`;
- dashboard/resource registration;
- config-entry creation or provider configuration changes;
- reload/restart;
- any physical service call.

For a permanently read-only analytics product, physical service calls should be architecturally absent rather than merely disabled by mode. A Goal or architecture document describes potential live scope; it is not standing authorization. Require a fresh approval for production deployment/storage migration, another for restart when needed, and another for permanent legacy-producer or registry removal.

## Native Energy-platform v2 review gates

Use these gates when replacing a Forecast.Solar REST profile producer with Home Assistant's native Energy Dashboard profile.

### Compatibility and provenance boundary

For HA Core `2026.7.4`, the verified energy-platform helper is:

```python
homeassistant.components.forecast_solar.energy.async_get_solar_forecast(
    hass, config_entry_id
)
```

It returns `{"wh_hours": {iso_timestamp: Wh, ...}}` from the Forecast.Solar config entry's `runtime_data.data.wh_period`. It supplies no provider `issued_at`, acquisition timestamp, or response generation, and it can return retained coordinator data after a later refresh failure unless the adapter checks coordinator health separately. The helper also omits a zero-valued point exactly at midnight, so first-boundary reconstruction must never be assumed.

Treat the direct module path and runtime shape as a version-scoped HA integration-platform contract, not a semver-stable third-party API:

1. Start with an exact tested Core-version allowlist; a new Core version requires a pinned real-HA compatibility test before widening it.
2. Isolate all imports/runtime inspection in one adapter. On changed import, signature, entry state, runtime type, payload shape, or coordinator failure, keep diagnostics loaded but block profile admission.
3. Never call `estimate()`, `async_request_refresh()`, provider HTTP, or REST as a recovery path.
4. Record only locally owned `observed_at`, observation sequence, and canonical payload digest. Do not relabel them as provider generation or provider attestation.
5. Learn freshness from successful native coordinator updates observed by a listener. Re-reading the same cached payload on a local timer does not prove a fresh provider update.

Bind the adapter to exactly the Forecast.Solar entry selected by the Energy Dashboard solar source. Energy configuration can hold a list of forecast entry IDs; missing, changed, or multiple ambiguous IDs fail closed. Do not silently rebind to a manually selected entry. Validate the canonical actual energy and power identities at runtime as well as in config flow.

### Fixed-slot snapshot identity

A fixed scheduled snapshot is a semantic slot, not a provider refresh:

```text
unique slot = source_lineage_id + snapshot_type + scheduled_at_utc
```

Keep provider/local observation generation and payload digest as attributes, never as uniqueness dimensions. Model one capture as a header plus normalized child intervals because a complete native horizon can span several target dates:

- `snapshot_slots`: schedule, target local date, lineage, observed time/digest, admissibility, and terminal reason;
- `forecast_snapshot_intervals`: slot ID, UTC start/end, native period-end timestamp, energy Wh, and local-date overlap.

At the fixed morning slot, select only a native observation that existed at or before the scheduled instant. A post-slot refresh must not be backdated as the baseline. If HA was down or no eligible profile existed, persist a terminal `missing`/`blocked` slot and never substitute latest or day-ahead. A retry or restart must not create a second scheduled snapshot. Assert that many internal refreshes outside a slot add no historical snapshot rows.

Represent period-end values as half-open UTC intervals `[previous_end, current_end)`. Reject the first interval when its previous boundary is unavailable, preserve irregular periods, assign energy by interval overlap rather than endpoint date, and require payload-to-storage count/digest/Wh conservation. Excluding unresolved first/last boundaries must not hide internal gaps or reduce the coverage denominator opportunistically.

### Actual and coverage contract

Treat a canonical PV energy entity as a cumulative counter unless its reset semantics prove otherwise. Define explicitly:

- counter unit/state-class and reset/decrease handling;
- local-day anchor rules;
- the time-weighted method and maximum gap for the power entity;
- freshness from source observations or a verified transport heartbeat, not from the analytics poll time;
- reconciliation tolerance between integrated power energy and cumulative-counter delta.

Never clamp negative/unknown/stale/wrong-unit inputs to zero. Compute forecast, actual, and paired coverage by duration over the same eligible morning-snapshot intervals. A conservative default gate is forecast `>=95%`, actual `>=90%`, and paired overlap `>=90%`, with tests for 23/24/25-hour local days.

Define accuracy mathematically before implementation. Keep morning-only, completed-day metrics separate from current-day progress and day-ahead diagnostics. A useful neutral v1 set is signed error kWh, absolute error kWh, WAPE, bias, and energy ratio with an explicit low-energy denominator guard. VRM scalars must not influence native profile readiness, interval accuracy, or consensus. Defer equipment-underperformance claims unless a separate conservative evidence contract exists; curtailment/external-control should be tri-state diagnostics, not absence-implies-safe booleans.

### Lineage and schema safety

A model fingerprint is an attribute, not a lineage identity. Persist a monotonic lineage epoch so `A -> B -> A` creates three lineages and old A days cannot unlock the new A epoch. A valid native model/source transition creates a new epoch; malformed or unavailable reads do not. Prevent pairing across source-entry, actual-source, normalization-version, or metric-version epochs. For multi-plane native entries, either fingerprint every plane in canonical order or explicitly fail closed when more than one plane is unsupported.

For an owned SQLite archive:

- use one serialized writer off the HA event loop;
- version immutable inputs and derived metric algorithms separately;
- keep current/latest profile as overwrite-only bounded cache, not append-only refresh history;
- quarantine legacy REST rows at query time with explicit `native + admissible + current_lineage` predicates;
- take a consistent SQLite online/quiesced backup that accounts for WAL, run integrity checks, and prove a disposable restore before live migration;
- test disk-full, lock, corrupt/newer schema, duplicate commit, retention, and a synthetic ten-year dataset with bounded startup/query/size budgets.

### Migration sequencing

Use this order:

```text
pinned adapter tests
-> local/disposable real-HA candidate
-> separately approved native deployment
-> quantified read-only soak
-> consumer migration
-> separately approved REST/registry removal
-> post-removal soak and rollback audit
```

Do not put the soak after legacy removal while also naming soak as a removal prerequisite. Make the soak measurable (for example, at least 72 hours, two valid morning slots, two day-ahead slots, one lifecycle reload/restart, no duplicate slots, clean logs and DB integrity). The 14-of-30 accuracy history threshold is a runtime status, not a software-release or migration-completion gate.

When removing a legacy entity, require no unintended Recorder continuity break for surviving canonical entities. The removed legacy series may end intentionally, but its entity/unique/statistic identity and rows must never be reused or reclassified as native history. If a dashboard still references it, either block removal or perform a separately approved exact reference-only migration while preserving card/layout structure and hashes.
