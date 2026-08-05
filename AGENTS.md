# Solar Analytics project context

## Scope

This repository implements Solar Analytics as a standalone, read-only Home Assistant custom integration. It has no physical-control acceptance gate and must not control boilers, heaters, accumulators, dehumidifiers, relays, batteries, or the grid.

Solar Analytics is PV-only. Non-PV device-state transitions are out of scope for blockers, evidence, acceptance criteria, physical readback, and rollback decisions.

## Authoritative sources

- Home Assistant native Forecast.Solar / Energy Dashboard binding is authoritative for forecast-profile lineage.
- Actual PV comes from the canonical Cerbo GX entities `sensor.garage_cerbo_gx_pv_power` and `sensor.garage_cerbo_gx_pv_energy`.
- The native adapter is the sole forecast acquisition path; it must not silently substitute another source.
- VRM is scalar-only telemetry; it is not a replacement for the native timestamped forecast-profile source.
- A scalar source being available does not make a timestamped forecast profile valid. Profile-dependent metrics and recommendations require an admitted native profile and fresh evidence.

## Read-only boundary

Do not call Home Assistant services, reload config entries, restart Home Assistant, trigger refreshes, call `estimate()`, or call provider HTTP endpoints while developing or validating this project unless a separate user-approved deployment task explicitly requires it. Never claim live success from a source-file test, `ha core check`, or a successful service-call response alone.

## Evidence contract

Every historical or runtime claim must state the exact source, timestamp/boundary, resolution, coverage, denominator, and uncertainty. Do not claim production readiness until native listener evidence, persistence evidence, restart/readiness evidence, migration checks, and the required read-only soak are complete.

Use `PASS`, `FIX_REQUIRED`, `BLOCKED`, or `PARTIAL`; do not infer success from missing errors or an empty/null data structure.

## Read-only soak checkpoint

The Solar Analytics checkpoint reads only the integration status/profile entities and the canonical actual-PV entities: `sensor.solar_analytics_native_forecast_solar_source_status`, `sensor.solar_analytics_analysis_status`, `sensor.solar_analytics_last_updated`, `sensor.solar_analytics_solar_forecast_accuracy`, `sensor.solar_analytics_solar_future_profile`, `sensor.garage_cerbo_gx_pv_power`, and `sensor.garage_cerbo_gx_pv_energy`. For the local SQLite store `/config/solar_analytics/solar_analytics.sqlite`, use mode=ro and inspect integrity plus `v2_lineages`, `v2_current_profile_cache`, `v2_snapshot_intervals`, `v2_daily_comparisons`, `v2_accuracy_results`, `v2_runtime_state.last_actual_sample`, and the newest `v2_accuracy_results` timestamp. Do not read boiler, heater, dehumidifier, accumulator, or other non-PV device state as soak evidence.

## Collector, snapshot, and analyzer boundary

`tools/pv_soak_checkpoint.py` is a local deterministic validator/analyzer. It performs no SSH, network, Home Assistant, provider, or SQLite access. The cron run must keep these stages separate:

1. **Collector:** gather only the allowlisted evidence through an independently read-only SSH command set into a temporary JSON envelope; never include raw logs or unallowlisted entities.
2. **Snapshot:** run `python tools/pv_soak_checkpoint.py snapshot --input <collector.json> --output-dir reports/soak_checkpoints`. The script validates the read-only contract, writes a content-addressed no-overwrite snapshot, and emits only its path/status.
3. **Analyzer:** run `python tools/pv_soak_checkpoint.py analyze --snapshot <snapshot.json>`. Deliver only the bounded PASS/BLOCKED JSON result with blockers and physical-call counts.

A malformed, stale, incomplete, digest-mismatched, or non-zero-write envelope is `BLOCKED`; never repair it by collecting broader state or retrying a mutation.

## Project-local references

Detailed architecture/evidence references live under `docs/architecture/`. This `AGENTS.md` remains the current short policy source; the reference copies do not authorize deployment or provider/config mutations.

## Implementation conventions

- Every config-flow field must have an inline UI description explaining meaning, use, and runtime/safety effect.
- Keep native-source provenance, profile admission, actual-source freshness, persistence, and presentation readiness as separate contracts.
- Keep secrets, tokens, and connection strings out of reports, fixtures, logs, and committed files.
- Preserve a rollback artifact and exact hashes for every staged live-file change; live deployment remains a separate approval-gated workflow.
