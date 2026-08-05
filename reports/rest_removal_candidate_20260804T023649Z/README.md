# Legacy Forecast.Solar REST removal candidate

Date: 2026-08-04
Timezone: UTC evidence timestamps
Scope: candidate plus controlled live-removal evidence; live `/config/packages/energy_split.yaml` was promoted only after explicit early-removal authorization and verified rollback backup.

## Source snapshot

```text
live_source=/config/packages/energy_split.yaml
before_sha256=a434700aa9113ec246cfdec163fdd3f52b79df97163f7b33a80726aaa6a975cf
before_lines=1373
```

## Candidate transformation

Removed only:

- legacy `rest:` producer for `sensor.forecast_solar_hourly_api`;
- `solar_analytics_refresh_forecast_solar_rest_on_native_model_change` automation;
- Recorder exclusion for the removed REST entity;
- duplicated deployment comments belonging to that legacy block.

Preserved native/package references:

- `sensor.power_production_now`;
- `sensor.energy_next_hour`;
- `sensor.forecast_solar_production_power_kw`;
- `input_select.solar_forecast_day`.

```text
after_sha256=1978380bd089c937a98f11343ae41fac67892cf9176989fb2039938f51f64271
after_lines=1307
removed_lines=66
added_lines=0
```

## Local candidate gates

```text
before_yaml_parse=ok
after_yaml_parse=ok
legacy_removed=asserted
native_package_references_preserved=asserted
candidate_rest_block=0
candidate_forecast_solar_hourly_api_references=0
candidate_refresh_automation_references=0
```

## Pre-removal gate status — read-only refresh 2026-08-04T12:38:32Z

At this historical boundary the candidate was still staged. The user subsequently authorized the early scoped removal because the REST producer had no active consumers; the native soak continues as a post-removal replacement-stability gate.

```text
native_callback_observed=true
native_observation_sequence=10
native_observed_at=2026-08-04T12:07:12.403434Z
native_status=ok
forecast_profile_analysis_allowed=true
native_soak_elapsed_hours=9.522
native_soak_required_hours=72
admissible_morning_snapshots=0
admissible_day_ahead_snapshots=1
accuracy_ready=false
valid_paired_days=0
```

The `14/30` accuracy status is a separate runtime statistical gate and is not required for REST removal. The pre-removal snapshot opportunities were the second day-ahead slot at `2026-08-04T20:00Z`, future morning slots at `2026-08-05T03:00Z` and `2026-08-06T03:00Z`, and the original 72-hour boundary at `2026-08-07T03:07:11Z`.

## Subsequent corrected runtime readback

The watcher predicate was too strict: observation sequences restart from `1` after a process/restart boundary. The normal native callback had previously been observed independently:

```text
native_status=ok
native_observation_sequence=1
native_observed_at=2026-08-04T03:07:11.536712Z
forecast_profile_analysis_allowed=true
ha_core_check=success
```

## Controlled live removal evidence

```text
user_early_removal_authorization=explicit
pre_core_check=success
atomic_package_promotion=success
live_package_sha256=1978380bd089c937a98f11343ae41fac67892cf9176989fb2039938f51f64271
post_promotion_core_check=success
controlled_restart=success
http_readiness=200
legacy_entity_registry_cleanup=success
legacy_rest_entity_after=404
legacy_refresh_automation_after=404
native_forecast_registry_entities_after=11
native_forecast_config_entry_unchanged=true
analytics_sqlite_integrity=ok
recorder_sqlite_integrity=ok
recorder_legacy_statistics_meta_rows=0
physical_service_calls=0
notifications=0
```

A valid post-reload native callback was observed at `2026-08-04T16:08:56.525240Z` with `native_source_status=ok`, `solar_future_profile=ready`, `forecast_profile_analysis_allowed=true`, `native_observation_sequence=1`, and payload digest `sha256:1e312078c86cf8380921bf9a430b05843e6421f789500a352f23013765b2b0e6`. This callback is the valid post-reload native-only soak boundary. The first state publication also reported `overall_status=actual_source_stale` because the actual-PV sample was older at that instant; the canonical PV power entity subsequently updated at `2026-08-04T16:13:22.062208Z`. No manual native refresh, Forecast.Solar reload, or additional Solar Analytics reload was used.

## Versioned migration backup and next local candidate

The verified pre-`2.1.3` backup is:

```text
/config/backups/solar_analytics_2_1_2_pre_migration_20260804T134043Z/
```

```text
solar_analytics_v2_1_2_live.tar.gz sha256=ef91bea28f77e42939753d89d61334d3ca905ecd2718fa392a78949e214137df
core.config_entries sha256=233838b7c8a8d1b16dca149fc994b9f77b6d53763c96e7e185b6c8a3af004f7a
core.entity_registry sha256=706b0097623392e1473bb653616140a7d2e3b4b4a78e0be078537b946d436ac3
solar_analytics.sqlite sha256=261db8dd1ee353a1cc19d53f73b3baa6f4957843585f9b8df4b7701641832815
SHA256SUMS=all OK
component_backup_manifest=2.1.2
backup_sqlite_integrity=ok
```

The `2.1.4` candidate was deployed live after explicit approval to avoid waiting through two separate 72-hour periods. It contains the runtime-swap listener fix and schema v4 accuracy-cache migration/overwrite semantics.

```text
manifest=2.1.4
entry_version=4
schema_version=4
native_adapter_sha256=f04306aa071565c45a1b9a25de89422001c308cd988e023ae221deaa8ac6eb03
full_pytest=62 passed
native_adapter_tests=9 passed
compileall=ok
manifest_json=ok
pure/HA storage_v2 SHA parity=true
archive=reports/solar_analytics_2_1_4_candidate_20260804T153034Z/solar_analytics_v2_1_4_candidate.tar.gz
archive_sha256=3acaeeb497311ee57dfae8a263e2be9b3d4add15bd9d19d5267a014e91eba981
backup=/config/backups/solar_analytics_2_1_3_pre_2_1_4_20260804T161803Z
backup_integrity=ok
backup_sha256sum_verified=true
candidate_local_remote_sha256_equal=true
pre_promotion_core_check=success
post_promotion_core_check=success
http_ready=200
live_accuracy_rows_before=262
live_accuracy_rows_after=1
live_accuracy_duplicate_logical_keys_after=0
live_sqlite_integrity=ok
v2_backfill_rows_unchanged=true
native_entry_preserved=01KY5NR2G39STXJYBHVYE32RFK
legacy_key_present=false
rest_entity=404
physical_service_calls=0
notifications=0
deployment_plan=reports/solar_analytics_2_1_4_candidate_20260804T153034Z/DEPLOYMENT_PLAN.md
```

After the `2.1.4` restart the live source is intentionally fail-closed as `entry_unloaded` until a fresh native Forecast.Solar callback is observed. That callback will be the new post-`2.1.4` soak boundary. The earlier post-reload `2.1.3` boundary is superseded by this approved deployment.

## Pre-removal rollback backup

Remote backup directory:

```text
/config/backups/solar_forecast_rest_removal_prechange_20260804T035200Z/
```

Verified artifacts:

```text
energy_split.yaml sha256=a434700aa9113ec246cfdec163fdd3f52b79df97163f7b33a80726aaa6a975cf
core.entity_registry sha256=546177197e4f426ce95f6f1ebab6218d9f60da363fcd87ef3033278b22e1fe3e
core.config_entries sha256=a9babd4cb7b91156b84f237be44298f46a17eb293acddad9841df573e4d924b0
solar_analytics.sqlite sha256=cdcfaefcf8526dc0871f5c8f79e5120e66dd783bbf13a0a8f885c757331e3269
home-assistant_v2.db bytes=6544568320
home-assistant_v2.db sha256=895a5b39c2cb7be87469cd3ce916ca98e39ea4656cb8a7873a9151385e1b50e1
home-assistant_v2.db integrity=ok
solar_analytics.sqlite integrity=ok
```

The validated candidate was staged remotely at `energy_split.after.yaml` with SHA-256 `1978380bd089c937a98f11343ae41fac67892cf9176989fb2039938f51f64271`; after the controlled promotion the live package has the same SHA-256. The verified original remains in the rollback backup.
