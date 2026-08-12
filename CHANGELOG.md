# Changelog

All notable changes to Solar Analytics are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Apache-2.0 `LICENSE`.
- `CHANGELOG.md` (this file).
- GitHub Actions CI running `pytest` and `ruff` on push and pull request.
- `ruff.toml` and `.github/` templates (bug report, feature request, PR).
- English-primary `strings.json` and `translations/en.json`; Ukrainian retained
  in `translations/uk.json`.
- Config-flow entity selectors for actual PV power/energy sensors, configurable
  timezone (defaults to `hass.config.time_zone`), configurable morning and
  day-ahead snapshot hours, and `async_step_reconfigure`.
- `diagnostics.py` returning a redacted coordinator snapshot.
- Coordinator-level, sensor-payload, config-flow, diagnostics, and read-only
  invariant tests.

### Changed
- Product scope: Solar Analytics is now a reusable, installation-agnostic
  custom integration. No canonical entity IDs, timezone, or manufacturer
  string are hardcoded to a single installation.
- `manifest.json`: added `energy` and `forecast_solar` dependencies, populated
  `codeowners`, added `issue_tracker`, set `iot_class` to `local_push`, set
  `quality_scale` to `platinum`.
- Version strings (`sw_version`, adapter version) are now derived from
  `manifest.json` at import time; no more three-way drift.
- Coordinator schedules morning and day-ahead snapshots in the configured
  timezone using `async_track_point_in_utc_time`, not in `hass.config.time_zone`.
- `DataUpdateCoordinator.always_update` set to `False`; state writes are
  driven by real payload changes.
- Config entry data is now attached via `entry.runtime_data` (modern HA pattern);
  `hass.data[DOMAIN]` is no longer used.
- Entities set `_attr_has_entity_name = True`, `_attr_translation_key`,
  `SensorStateClass.MEASUREMENT` on power sensors, `SensorDeviceClass.TIMESTAMP`
  on the last-updated sensor, `SensorDeviceClass.ENUM` with `options` on enum
  sensors, `EntityCategory.DIAGNOSTIC` where applicable, and per-entity
  availability logic. Legacy unique IDs and object IDs are preserved so
  existing installations do not get `_2` suffix duplicates.
- Native adapter loosened from an exact HA version pin to a minimum version
  (`2026.7`) with runtime feature detection for `wh_period` and
  `async_get_solar_forecast`.

### Removed
- Duplicated modules under top-level `solar_analytics/` (`native.py`,
  `storage_v2.py`, `v2_metrics.py`, `entity_contract.py`). The
  `home_assistant/custom_components/solar_analytics/` package is now the sole
  source of truth; tests import from it directly.
- Legacy analytics/storage stack `solar_analytics/analytics.py` and
  `solar_analytics/storage.py` (unused by the shipping integration) and its
  companion `tests/test_analytics.py`.
- Empty placeholder `home_assistant/packages/solar_analytics.yaml`.
- Historical per-deployment audit directories under `reports/` (kept only the
  soak-checkpoints directory with a README).

## [2.1.4] - 2026-08

Prior history is not tracked in this file. See git log for older commits.
