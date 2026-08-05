# Solar Analytics

Read-only Home Assistant custom integration for native Forecast.Solar profile analytics and canonical PV telemetry.

## Contract

- Forecast acquisition uses only Home Assistant's native Forecast.Solar Energy Dashboard runtime.
- Actual PV uses `sensor.garage_cerbo_gx_pv_power` and `sensor.garage_cerbo_gx_pv_energy`.
- Forecast cells are native `Wh/period` values with period-end semantics.
- Internal intervals are half-open UTC; presentation timezone is `Europe/Kyiv`.
- Unavailable, stale, non-numeric, non-finite, duplicate, gapped, reset, DST-ambiguous, and incomplete data fail closed.
- The integration registers no control services and performs no physical actions or notifications.
- Existing dashboards and Lovelace resources are not structurally modified.

## Runtime

- Home Assistant Core target: `2026.7.4`
- Integration manifest: `2.1.4`
- Config-entry version: `4`
- SQLite schema: `4`
- Morning snapshot: D-1 06:00 Europe/Kyiv; the only accuracy baseline.
- Day-ahead snapshot: D-1 23:00 Europe/Kyiv; persistence and diagnostics only.
- Forecast coverage gate: `>=95%`.
- Actual coverage gate: `>=90%`.
- Accuracy readiness: `14` valid paired days in a rolling `30-day` window.

## Native adapter

`home_assistant/custom_components/solar_analytics/native_adapter.py` owns the narrow Home Assistant runtime boundary. It resolves the configured native entry, binds one listener to the current runtime identity, rebinds safely after runtime replacement, validates the native profile, and records local observation metadata. It never initiates a refresh and never substitutes a cached or synthetic profile for a missing native callback.

## Persistence

SQLite stores bounded lineages, snapshots, integrated actual PV intervals, daily comparisons, accuracy results, runtime state, and diagnostics. Current profile and accuracy writes are overwrite/upsert operations with logical keys; refresh cadence does not create unbounded result rows. Schema migrations are additive and integrity checked.

## Validation

Run locally:

```bash
python3 -m pytest -q
python3 -m compileall -q solar_analytics home_assistant/custom_components/solar_analytics
```

The test suite covers native normalization, timestamp and DST handling, actual-state quality gates, runtime listener replacement, config-entry migration, SQLite migration/integrity, bounded persistence, diagnostics, and the absence of physical service calls.

## Layout

- `solar_analytics/` — pure deterministic analytics and storage helpers.
- `home_assistant/custom_components/solar_analytics/` — Home Assistant integration.
- `tests/` — local regression and quality tests.
- `docs/architecture/` — native-only architecture and verification references.
- `tools/` and `scripts/` — deterministic local analysis utilities.
