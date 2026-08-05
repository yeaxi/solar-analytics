# Native Forecast.Solar and Solar Analytics Architecture

## Scope

This reference captures the verified architecture for a Home Assistant Solar Analytics integration that must use the same Forecast.Solar profile rendered by Energy Dashboard. It is a read-only analytics pattern. Physical load planning, bounded execution, relay ownership, and stop-only safety remain separate domains.

## Verified HA 2026.7.4 native path

Home Assistant Energy Dashboard uses the Forecast.Solar energy platform callable:

```python
from homeassistant.components.forecast_solar.energy import async_get_solar_forecast

forecast = await async_get_solar_forecast(hass, config_entry_id)
# {"wh_hours": {"<ISO timestamp>": <Wh>, ...}}
```

In the HA 2026.7.4 implementation, the data is held by the loaded native Forecast.Solar config-entry runtime (`entry.runtime_data.data.wh_period`). This is the backend path behind the Energy Dashboard forecast curve; scalar entities such as `sensor.energy_current_hour` are not the full profile.

Interpretation:

- `source_kind=native_energy_platform` proves local provenance to the same native forecast runtime used by Energy Dashboard;
- it is not a provider-signed attestation and must not be described as cryptographic proof of Forecast.Solar server internals;
- do not recreate a native-derived URL, issue another Forecast.Solar GET, call `estimate()`, or call `homeassistant.update_entity` to obtain this profile;
- never pass native API keys, connection strings, or raw config-entry secrets into Solar Analytics.

## Adapter contract

Implement one `ForecastSolarNativeAdapter` boundary. Inputs:

- Home Assistant runtime;
- native Forecast.Solar `config_entry_id`.

Outputs:

- normalized `wh_hours` profile;
- source/config-entry identity;
- native coordinator health and last-success status;
- local payload digest/generation;
- acquisition timestamp;
- validation/admission reason.

Required checks:

1. Resolve the config entry by ID and require `LOADED` state.
2. Re-resolve on refresh/reload; do not retain a stale coordinator reference.
3. Verify native coordinator `last_update_success` and freshness.
4. Require a mapping of ISO timestamps to finite, non-negative Wh values.
5. Validate timestamp timezone/ordering, forecast horizon, period-end semantics, gaps, DST transitions, and duplicate keys.
6. Keep `Wh/period` separate from instantaneous W and from kWh summary sensors.
7. Create a local digest/generation for snapshot lineage; do not imply provider-signed provenance.
8. Use startup warm-up/quarantine and fail closed on unavailable, unloaded, stale, malformed, empty, or unsupported native data.
9. Do not fall back to a REST payload while retaining the native provenance status.

Keep imports of the HA-internal energy-platform surface in this adapter only. Add an exact-target real-HA smoke test for each supported HA version; the surface is useful but HA-internal and may change.

## Target ownership boundaries

The dedicated `solar_analytics` custom integration owns:

- config flow, OptionsFlow/reconfigure, and native config-entry selection;
- actual PV, VRM scalar-only, and context adapters;
- profile normalization and fail-closed admission;
- snapshot scheduling and immutable lineage;
- SQLite repository and additive migrations;
- deterministic analytics and coverage;
- entities, diagnostics, issue/status reporting, and bounded notifications.

It does not own:

- the native Forecast.Solar config entry or its refresh cadence;
- Energy Dashboard registration or forecast provider configuration;
- existing Lovelace storage/resources/cards;
- a synthetic VRM hourly profile;
- automatic recommendations or physical service calls;
- the physical planner/executor or its internal coordinator/SQLite.

## Legacy compatibility

Keep `sensor.forecast_solar_hourly_api` as a legacy compatibility output until every consumer is inventoried and a separate migration is approved. It may preserve its existing state, unit, attributes, `unique_id`, and entity ID, but it must not feed new Solar Analytics analytics or snapshot admission.

Do not immediately replace a `rest` platform entity with a custom-platform entity of the same apparent name. HA entity-registry identity includes platform/integration details; an unsafe replacement can leave an orphan or create `_2`. A future migration requires:

- repository and live consumer inventory;
- entity-registry/config-entry/package backups and hashes;
- a shadow entity with a different ID;
- several refresh-cycle comparisons of state, attributes, timestamps, rounding, availability, and cadence;
- an exact registry migration with HA stopped;
- post-restart verification of ID, unique ID, dashboards, automations, and Recorder continuity;
- scoped rollback.

It is acceptable to keep the REST entity permanently as a compatibility shell if migration risk exceeds its benefit.

## Staged B-to-A-core migration

1. **Contract freeze:** inventory consumers; record entity/config/storage/dashboard contracts; back up component, package, registry, config entries, SQLite, and dashboard storage.
2. **Adapter shadow:** add the native adapter without changing legacy entities or historical rows. Compare native profile and legacy REST diagnostics only.
3. **Storage migration:** add versioned, additive, transactional schema migrations; reject unsupported newer schema; keep legacy blocked rows audit-only; do not backfill historical forecasts.
4. **Native cutover:** only native adapter output enters normalization and snapshot admission. Native failure disables/fails closed rather than authorizing REST.
5. **Read-only soak:** cover native refresh, snapshot windows, restart/reload, outage/recovery, exact entities, logs, hashes, and SQLite lineage.
6. **Legacy cleanup:** remove analytics-specific YAML/REST refresh logic only after consumer inventory and compatibility verification. Treat entity-platform takeover as its own maintenance slice.

Do not combine native cutover, SQLite migration, and entity-registry takeover in one restart.

## Configuration rules

The analytics config flow should select the native Forecast.Solar config entry and configure only analytics-specific inputs:

- actual PV power/energy entities;
- VRM scalar mapping;
- optional context mappings;
- snapshot schedule/retention;
- notification policy.

Do not duplicate native planes, azimuth, declination, inverter, damping, or API-key fields. Every onboarding field must have an inline UI description explaining meaning, analytics use, and safety effect.

## Lifecycle requirements

Use:

- `single_config_entry: true` plus a defensive singleton check;
- typed `entry.runtime_data`;
- `entry.add_update_listener()` for options/reconfigure;
- `entry.async_on_unload()` for every listener/timer;
- setup failure cleanup for DB/listeners/runtime;
- unload behavior that does not remove a live runtime when platform unload failed;
- idempotent restart-safe snapshot scheduling;
- native unload/reload → unavailable/quarantine, never REST authorization;
- an independent low-frequency read-only backstop if the provider event hook is not sufficient.

## Storage requirements

SQLite is appropriate for immutable forecast snapshots and prepared 30-minute aggregates. Use:

- additive `v1 -> v2 -> ...` migrations;
- explicit schema-version checks;
- backup before migration;
- fail-closed behavior on unsupported newer schema;
- lineage fields for `source_kind`, adapter contract version, payload digest/generation, acquisition time, observation time, and admission status;
- audit-only retention of old REST/blocked rows;
- no retroactive forecast rewrite.

Use HA `Store` only for small runtime flags; do not move full profile history into entity states or unrestricted attributes.

## Test/evidence matrix

Required local tests:

- exact native adapter `wh_hours` contract;
- malformed, empty, stale, unloaded, and unavailable source;
- finite/non-negative Wh and timestamp validation;
- period-end, DST, timezone, horizon, gap, and duplicate handling;
- config-entry reload and coordinator health;
- storage migration success, corruption, newer-schema rejection, and legacy quarantine;
- no REST GET, no `estimate()`, no `homeassistant.update_entity`, and no physical service calls;
- legacy entity compatibility;
- no retroactive forecast.

Required real-HA evidence:

- HA Core `2026.7.4` adapter smoke;
- config flow/setup/reload/unload;
- native Energy Dashboard profile correspondence;
- exact entity/unique-ID checks;
- diagnostics redaction;
- `ha core check`, controlled restart/readiness, targeted logs;
- SQLite row/lineage/admission query;
- candidate/live hashes and scoped rollback evidence.

Existing dashboards remain structurally untouched. Browser screenshots are a separate UI verification gate and must not be substituted with state/entity checks.
