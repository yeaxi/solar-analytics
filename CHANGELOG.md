# Changelog

All notable changes to Solar Analytics are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Imported historical actual production. On setup Solar Analytics reads the
  configured actual PV **energy** sensor's long-term Recorder statistics
  (hourly, never purged) through `statistics_during_period` on the Recorder's
  own executor, derives each local day's kWh from the cumulative `sum` deltas,
  and stores them in the new `v2_imported_actual_daily` table. Counter resets
  are recorded rather than turned into negative days, and each day carries the
  fraction of its hours that were observed, measured against the real 23, 24 or
  25 hours of that local day.
- New `sensor.solar_analytics_imported_actual_history` diagnostic entity,
  disabled by default, carrying the bounded daily points, the import status,
  and an explicit `reconstructed_from_recorder_statistics` provenance label.
  Imported actuals are **not** wired into `valid_paired_day`, the rolling
  accuracy window, or WAPE, and they do not shorten the 14-day accuracy
  warm-up. Home Assistant never persists the timestamped forecast profile to
  any state, so there is no recorded historical forecast to pair them against,
  and splitting the one logged daily forecast scalar across hours is forbidden
  by rule 4 of the recorder/forecast contract.
- `scripts/verify_import_idempotency.py`, a deterministic rerunnable check that
  the import converges: it feeds a synthetic year of hourly statistics through
  the real reconstruction and the real store write three times and compares the
  row count and total kWh.
- Read-only scanner coverage for the write paths this feature invites:
  `async_import_statistics`, `async_add_external_statistics`,
  `async_adjust_statistics`, `hass.states.async_set`,
  `hass.states.async_remove`, any reference to the live Recorder database file,
  and an assertion that `sqlite3` stays imported only by `storage_v2.py`.
- `recorder` in `manifest.json` `after_dependencies` (an ordering hint, not a
  hard dependency: the entry still loads and reports `recorder_unavailable`).

  Evidence status for the import is **PARTIAL**. Home Assistant is not
  installed in CI, so the Recorder call is exercised against a recording stub.
  What is verified is the reconstruction arithmetic, the storage idempotency,
  and that exactly one statistic id is requested through the read API with no
  mutating call. What is not verified is live Recorder behaviour on a real
  installation. Do not claim this path production-ready without that evidence.
- Pinned `requirements-dev.txt` and Dependabot updates for GitHub Actions
  and pip so CI tool versions stop floating.
- Hermetic tests that `strings.json`, `translations/en.json`, and
  `translations/uk.json` share the same key tree, and that
  `pyproject.toml` version matches `manifest.json`.
- GitHub Pages docs site (MkDocs Material) at https://yeaxi.github.io/solar-analytics/.
- Home Assistant brand assets at `custom_components/solar_analytics/brand/`
  (`icon.png` 256x256 and `icon@2x.png` 512x512) so HACS renders a proper icon
  for the integration and the "brands" validator passes without a
  home-assistant/brands submission.
- Coordinator now emits a single warning log when the native Forecast.Solar
  binding becomes unavailable and a matching info log when it recovers;
  repeated identical statuses no longer log per 5-minute poll (silver-tier
  "log-when-unavailable" pattern).
- `CONTRIBUTING.md` documents the one-time GitHub-side owner setup (repository
  description, topics, and GitHub Pages source).

### Removed
- The six never-used `v2_backfill_*` tables and their eight store APIs. They
  had no callers and no tests, could not express the
  `observed_at_utc <= scheduled_at_utc` admissibility rule (no `scheduled_at_utc`
  column), had a nullable column inside a PRIMARY KEY so their `ON CONFLICT`
  never fired, and were never pruned. Storage schema version 5 drops all six on
  first open; nothing read them, so nothing is lost.
- `native.period_coverage_seconds` and the `native.parse_native_profile`
  compatibility alias, both unreferenced.
- `native._canonical_wh_hours`, also unreferenced. Its one rule, dropping a
  zero point at exact midnight, belonged to no shipped path;
  `normalize_native_wh_hours` keeps a non-midnight zero boundary and
  quarantines the first cell instead.
- `scripts/recorder_backfill_report.py` and its two `ruff.toml` per-file
  exceptions. The script opened an archived Recorder database directly and
  rebuilt daily rows from `states`, which duplicated the shipped importer. It
  could only recover a daily forecast scalar, and rule 4 of the recorder and
  forecast contract forbids feeding that into accuracy. The `scripts/`
  inventory in `CONTRIBUTING.md` now names `verify_import_idempotency.py`, the
  one script left. The 2.2.1 note about the report stays as written; it was
  true for that release.

### Changed
- CI tests and lint now run on Python 3.14 (Home Assistant 2026.7 runtime)
  with least-privilege permissions, concurrency cancellation, job
  timeouts, and a split lint/pytest job. Docs Pages write permission is
  limited to the deploy job.
- README is now end-user only (install, setup, troubleshooting, bug reports).
  Contributor, agent, and architecture docs stay in `CONTRIBUTING.md`,
  `AGENTS.md`, and `docs/architecture/`.
- `manifest.json` `documentation` URL now points at the docs site.
- `hacs.json`: removed the invalid `"hacs"` key that made the HACS validator
  reject the manifest; explicitly declared `"zip_release": false`.

### Fixed
- Daily coverage could never clear its 95% / 90% gate, so
  `sensor.solar_analytics_analysis_status` was stuck on `insufficient_data`
  and forecast accuracy never became `ready`. Forecast.Solar reports the whole
  night as one zero-Wh period straddling local midnight, and every
  boundary-crossing period was dropped, leaving only daylight in a numerator
  divided by a full day. A zero-Wh period is now clipped at local midnight and
  counted for both adjacent days; a boundary-crossing period that carries
  energy is still excluded rather than apportioned by time. Existing days are
  recomputed from the stored immutable snapshots, so the correction applies to
  history already on disk.
- Day length is now measured between two UTC instants instead of two local
  datetimes sharing a timezone, which always reported 24 hours. DST transition
  days are measured against their real 23 or 25 hours, and the three coverage
  ratios are clamped at 1.0.
- The snapshot schedule section of
  `docs/architecture/solar-analytics-recorder-and-forecast-contract.md` said
  the morning snapshot targets the current local date. Both configured
  snapshots are taken on D-1 and target D, which is what `daily_schedule()` and
  `_capture_scheduled()` already do. Documentation and test only; no runtime
  behaviour changed.

## [2.2.1] - 2026-08

Housekeeping release covering PRs #2 and #3 as a single tag.

### Added
- Full `PARALLEL_UPDATES = 0` on both platforms; icon translations via
  `icons.json` with per-state icons for enum sensors; exception translations
  for `UpdateFailed` via `translation_domain` + `translation_key`.
- Repair issues for the six actionable native-binding failure modes
  (`canonical_actual_mismatch`, `binding_changed` fixable;
  `binding_unavailable`, `binding_ambiguous`, `native_entry_unavailable`,
  `unsupported_native_contract` informational) with full translations and
  `ConfirmRepairFlow` handling.
- New `custom_components/solar_analytics/payload.py` with a pure
  `build_payload` function and companion tests.
- `hassfest` + HACS validation workflow (`.github/workflows/validate.yml`).
- Strict mypy against the seven pure modules (`const.py`, `entity_contract.py`,
  `migration.py`, `native.py`, `v2_metrics.py`, `storage_v2.py`,
  `tools/pv_soak_checkpoint.py`).
- Diagnostics test coverage (`tests/test_diagnostics.py`).
- Generic `examples/lovelace-example.yaml` + `examples/README.md`.

### Changed
- `scripts/recorder_backfill_report.py` fully parameterised: entity IDs,
  timezone, and day-ahead hour are now CLI flags.

## [2.2.0] - 2026-08

Reusable, installation-agnostic refactor.

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
