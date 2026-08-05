# Solar Analytics v2 — legacy REST/notification consumer inventory

**Inspection date:** 2026-08-03
**Scope:** read-only inventory after staged v2 deployment; no live consumer was disabled or removed.

## Authoritative native source

- Forecast.Solar config entry: `[REDACTED]` (exact ID retained only in live HA evidence, not duplicated here).
- Native Energy Dashboard entities observed:
  - `sensor.power_production_now`
  - `sensor.energy_production_today`
  - `sensor.energy_production_today_remaining`
  - `sensor.energy_production_tomorrow`
- Native Forecast.Solar API-key presence was verified by option-key presence only; credential values were not read or printed.
- Native entity values were present, but current-lifecycle listener observation had not yet occurred. Solar Analytics therefore remains fail-closed until the native coordinator listener fires successfully; no provider-issued success timestamp is assumed for Core `2026.7.4`.

## Legacy REST producer — retained intentionally before soak

- Live entity: `sensor.forecast_solar_hourly_api`
- Entity platform: `rest`
- Live package: `/config/packages/energy_split.yaml`
- Local source/package reference: not present in the current repository checkout; live package remains `/config/packages/energy_split.yaml`
- Local transitional overlay: `home_assistant/deployment/energy_split_rest_native_block.yaml`
- Transitional automation:
  - `automation.solar_analytics_refresh_forecast_solar_rest_after_native_model_change`
  - unique ID: `solar_analytics_refresh_forecast_solar_rest_on_native_model_change`

Removal status: **not removed**. Removal is prohibited until native deployment, quantified read-only soak, consumer/reference migration, scoped backup, controlled removal, and post-removal soak all pass.

## Legacy notification surface — retained for migration review

Live registry still contains:

- `input_boolean.solar_analytics_notifications_enabled`
- `input_datetime.solar_analytics_last_near_zero_notification`
- `input_datetime.solar_analytics_last_confirmed_notification`
- `input_datetime.solar_analytics_last_vrm_unavailable_notification`
- `automation.solar_analytics_gated_near_zero_pv_anomaly`
- `automation.solar_analytics_confirmed_persistent_or_post_storm_follow_up`
- `automation.solar_analytics_vrm_forecast_unavailable`

The v2 custom component does **not** register notification services, persistent notifications, or notification automations. These are legacy package consumers and require a separate migration decision after the native soak gate.

## v2 read-only safety result

- No v2 physical service calls: `0`.
- No v2 notification calls: `0`.
- No Forecast.Solar config-entry mutation.
- No provider HTTP request from the v2 component.
- Existing legacy REST/notification artifacts remain separate and are not treated as v2 provenance.

## Current blocker

At the latest post-restart check (`2026-08-03T19:20:14Z` UTC), native forecast compatibility was admitted (`native_source_status=ok`, `native_observation_sequence=1`), but canonical actual PV telemetry was stale. The v2 adapter reported `actual_source_stale`; profile readiness was allowed, while actual-vs-forecast accuracy, paired-day readiness, and underperformance claims remained fail-closed.

Read-only source tracing identified both canonical actual entities as `victron_mqtt`. The live config declares `venus.local:8883` with TLS, while fresh `victron_mqtt` logs show refused attempts to port `1883`. This external actual-source/MQTT blocker is outside Solar Analytics and was not modified.

## v2 source-boundary audit

A local source audit of `home_assistant/custom_components/solar_analytics` found no `requests`, `aiohttp`, REST provider call, `estimate()`, or `async_request_refresh()` path. The component source map explicitly marks `rest=prohibited`; the retained REST producer and its automation remain separate legacy consumers pending the migration gates.
