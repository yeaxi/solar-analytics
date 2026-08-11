# Solar Analytics Recorder and Forecast Contract

Use this reference when a Home Assistant PV analytics task needs honest historical forecast comparison without Solcast or fabricated VRM data.

## Evidence-first inventory

Capture, for each raw entity:

- entity ID, state, unit, device/state class, attributes, last update, availability;
- Recorder state-history count and min/max timestamps;
- `statistics_meta` unit/ID and short-term/long-term row coverage;
- whether the forecast is a scalar, array in attributes, or a provider API payload.

Keep a small source map. Prefer the canonical aggregate PV power and daily energy entities; use MPPT entities for electrical diagnostics and limitation context.

## Recorder namespace pitfall

`states_meta.metadata_id` and `statistics_meta.id` are different ID namespaces. Never use a `states_meta` ID to query `statistics_short_term` or `statistics`. Resolve them independently:

```sql
SELECT metadata_id, entity_id FROM states_meta WHERE entity_id IN (...);
SELECT id, statistic_id, unit_of_measurement
FROM statistics_meta WHERE statistic_id IN (...);
```

Use the first map for `states`, the second map for `statistics_short_term`/`statistics`. A mistaken shared map can produce a plausible empty history and silently disable backfill.

For power short-term rows with `mean` and no `mean_weight`, use the known statistics cadence only after confirming it from timestamps; detect gaps and report coverage. Do not interpret an energy `sum` as power or substitute a lifetime counter for a daily aggregate.

## Forecast contract rules

1. Parse ISO timestamp keys and deduplicate deterministically (keep the last archived value, count the duplicate).
2. Convert W, kW, Wh, and kWh only when the point semantics and interval duration are known.
3. If declared metadata conflicts with observed values (for example a `kWh` attribute containing values around 4000), preserve both the declared and effective semantics and emit `metadata_mismatch`; require an explicit adapter/configuration decision.
4. VRM today/tomorrow/current-hour/next-hour scalars are valid scalar forecasts only. Search attributes for a real timestamped array first. If absent, store `hourly_profile_status: unavailable_scalar_only`; never divide a daily total across hours or create p10/p50/p90 bands.
5. A current forecast must not be used to evaluate an already finished historical day. Historical scalar values are eligible only when their archived timestamp belongs to the intended snapshot window; otherwise classify them as missing.

## Snapshot key and schedule

Use a durable table with a unique key:

```text
(provider, target_date, snapshot_type)
```

`day_ahead` targets the next local date at the configured day-ahead snapshot hour in the user-selected analytics timezone (defaults 23:00). `morning` targets the current local date at the configured morning snapshot hour (defaults 06:00). Use an idempotent insert (`INSERT OR IGNORE` or equivalent) so a restart inside the polling window cannot duplicate a snapshot. Store provider, snapshot timestamp, target date, daily scalar, profile (only if real), selected provider parameters, source entity/identifier, and quality flags.

## 30-minute/DST normalization

Aggregate actual power time-weighted between reports; split segments at interval boundaries. Floor and advance on the UTC timeline, then render the interval timestamp in the analytics timezone chosen by the user in the config flow (defaults to Home Assistant's own timezone). This avoids nonexistent or duplicated local wall-clock intervals at DST transitions. Store coverage seconds/ratio and gap reasons; never fill a gap with zero actual production.

The interval contract should include:

```text
interval_start, actual_power_average_w, actual_energy_kwh,
forecast_solar_power/energy, vrm_power/energy,
consensus_expected, analysis_valid, validity_reason,
curtailment_reason, storm_context, data_quality, coverage_ratio
```

## Validity and diagnosis boundaries

Use explicit reason codes such as `valid_low_expected_power`, `battery_full`, `bms_charge_limit`, `dvcc_limit`, `export_limit`, `mppt_error`, `external_control`, `thermal_derating`, `clipping`, `sensor_unavailable`, `forecast_unavailable`, and `unknown_curtailment`.

Curtailment/clipping invalidates a technical-performance interval but must not create a PV-fault alert. `forecast realization` and `expected-production deviation` are safe names; do not call a forecast comparison an independent weather-normalized actual or guaranteed performance ratio. Require both providers, valid uncurtailed intervals, adequate expected power, repeated valid days, weather/context review, and a step change against the system's own baseline before emitting `possible_underperformance`.

## Dashboard/deployment evidence

The local gate proves pure tests, compile, and JSON/YAML parsing only. Before live claims, record:

- backup locations and hashes;
- HACS files/resources (ApexCharts and Plotly separately);
- `ha core check` output;
- config-entry/entity creation;
- SQLite snapshot rows before/after restart;
- native Energy/Energy Split dashboard hashes unchanged;
- desktop and mobile screenshots;
- provider/profile limitations and unresolved entities.

If Plotly is required but its files/resource are absent, report it as a live blocker and do not claim the heatmap is operational. Keep the dashboard isolated and use prepared aggregates rather than dumping raw Recorder points into Hermes or a state attribute.
