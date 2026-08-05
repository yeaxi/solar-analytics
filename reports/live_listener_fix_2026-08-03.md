# Solar Analytics v2 — listener lifecycle deployment evidence

Date: 2026-08-03
Timezone: Europe/Kyiv / UTC evidence timestamps
Scope: read-only Solar Analytics deployment; no physical service calls

## Local candidate gates

- Full test suite: `python3 -m pytest -q -rA` — **47 passed**.
- Compile gate: `python3 -m compileall -q solar_analytics home_assistant/custom_components/solar_analytics` — exit code `0`.
- Candidate archive:
  - path: `reports/candidate_20260803T130848Z/solar_analytics_v2_candidate.tar.gz`
  - bytes: `25842`
  - SHA-256: `a9cdfe0992eed4d1021a0e0d3cf505d8cf65d53a784beaaea5a57626ee99de50`
  - members: `15`
- Archive verification: traversal-safe; staged native listener fix present; no generated `__pycache__` in the promoted candidate.

## Root-cause fix

Pinned Home Assistant Core `2026.7.4` uses plain `DataUpdateCoordinator` for Forecast.Solar. It does not provide the `last_update_success_time` field used by the previous adapter guard.

The deployed fix:

- records a local UTC timestamp only when the Forecast.Solar coordinator listener fires;
- requires `runtime.last_update_success is True`;
- keeps retained/restored runtime data blocked before a listener event;
- retries listener attachment after config-entry setup ordering races;
- prevents duplicate listener registration;
- exposes `native_update_time_source=local_listener_observation`.

## Live backup and staging

Pre-promotion backup:

```text
/config/backups/solar_analytics_v2_listener_fix_predeploy_20260803T131013Z
```

Backup contents included the current component, package, SQLite database, SQLite WAL/SHM files, and a SHA-256 manifest. All manifest entries verified `OK`.

Live pre-promotion `ha core check`: exit code `0`.

Remote staging:

- local archive SHA-256: `a9cdfe0992eed4d1021a0e0d3cf505d8cf65d53a784beaaea5a57626ee99de50`
- remote archive SHA-256: `a9cdfe0992eed4d1021a0e0d3cf505d8cf65d53a784beaaea5a57626ee99de50`
- stage: `/config/.solar_analytics_v2_listener_fix_stage_20260803T130848Z_v2`
- staged files: `15`
- staged `compileall`: passed
- staged JSON validation: manifest, strings, Ukrainian translation passed
- archive member safety: passed

Promotion was performed after verifying that the live pre-change adapter/package hashes matched the backup baseline. Post-promotion `ha core check`: exit code `0`.

Post-promotion live hashes:

```text
/config/custom_components/solar_analytics/native_adapter.py
0e2648e210dac2c0de8930833c7e34daa74dfbf033920410ddd87dac706ba3a3

/config/custom_components/solar_analytics/coordinator.py
28959f36febb0fdd8457dee9a9d9cb724d493e6e1953171f40095ba1fcb78dcc

/config/packages/solar_analytics.yaml
623eddd45d79474f9b670617680f23b3ac030452fd4ec670e433b3cafb226bce
```

## Controlled restart

- Restart command: `ha core restart` — exit code `0`.
- HA readiness: HTTP `200`.
- Solar Analytics is read-only and has no physical-control path; physical device transitions are out of scope for this PV analytics acceptance report.

## Post-restart runtime evidence

Native Forecast.Solar entities became available after restart and were last updated at approximately `2026-08-03T13:44:27Z`.

Solar Analytics first post-restart capture occurred just before native runtime setup completed:

```text
2026-08-03T13:44:26Z
status=native_source_unavailable
validity_reason=entry_unloaded
```

On the next Solar Analytics coordinator cycle, retry attachment succeeded and the status changed to:

```text
2026-08-03T13:49:26Z
status=native_source_unavailable
validity_reason=native_update_not_observed
```

This is the expected fail-closed state until a normal Forecast.Solar listener update occurs. No manual refresh was issued.

Current observed analytics state remains:

```text
forecast_profile_analysis_allowed=false
forecast_solar_power=unknown
accuracy=insufficient_data
valid_paired_days=0
pv_performance_analysis_valid=off
pv_analysis_data_quality_problem=on
```

Canonical actual readback after restart:

```text
sensor.garage_cerbo_gx_pv_power = 1071.5 W
sensor.garage_cerbo_gx_pv_energy = 187.475 kWh
```

The live Forecast.Solar config contains an `api_key` option key, but its value is empty. Under pinned Core behavior this selects the one-hour native refresh interval. No secret value was read or retained.

## SQLite evidence

Read-only inspection after restart:

```text
PRAGMA integrity_check = ok
```

Current v2 counts remained fail-closed at the inspection boundary:

```text
v2_lineages=0
v2_current_profile_cache=0
v2_snapshot_intervals=0
v2_intervals=0
v2_daily_comparisons=0
v2_accuracy_results=0
v2_snapshot_slots=5
```

The five existing slots remain blocked historical records with `admissible=0`; they are not treated as current valid forecast evidence.

## Logs

Post-restart filtered logs showed:

- Solar Analytics custom-integration loader warning only; no Solar Analytics traceback was observed.
- Legacy REST consumer still requests a redacted/invalid zero geometry URL and receives HTTP `422`; REST retirement remains intentionally blocked until native soak and consumer migration gates pass.
- Other unrelated integration warnings/errors were present (`svitgrid`, ESPHome connectivity, Victron MQTT, and an existing energy-bounded-executor sensor error); they are not attributed to Solar Analytics.

## Follow-up local storage gate (after listener deployment)

The new RED/GREEN retention test found that the previous `prune()` order deleted parent snapshot slots before child intervals while SQLite foreign keys were enabled. That produced `FOREIGN KEY constraint failed`. The fix now deletes child intervals first and wraps the complete retention operation in an atomic transaction. Pure-library and component-local `storage_v2.py` copies are byte-identical:

```text
sha256=c225c92212dc2f64b94c451161c0d7a0cffcb1cf6c0fd6e095e8e3ff5fd6a8cb
```

Local follow-up gates:

- targeted backup/restore/exact-boundary test: `1 passed`;
- full local suite after fix: `python3 -m pytest -q` — `48 passed`, exit code `0`;
- compile gate: exit code `0`;
- synthetic ten-year gate: passed on a disposable database;
- synthetic evidence: `reports/synthetic_storage_10y_20260803T1357Z.json`;
- synthetic evidence SHA-256: `7a2882cad62a8388500d981f85979e06617480164cfad7aab963260b6ca6e5dc`.

Synthetic volume/evidence:

```text
3653 days; 7306 snapshot slots; 175344 snapshot intervals
87672 normalized intervals; 3653 daily comparisons
ingest=1.794651625 s
indexed snapshot query=0.00008075 s
indexed interval query=0.019696334 s
indexed daily query=0.001770292 s
online backup=0.220103959 s
integrity before/restore/after prune=ok/ok/ok
backup row counts exact=true
source database after checkpoint=109375488 bytes including SHM
```

Retention evidence used `3650` days with strict cutoff `2016-01-04`; the oldest remaining row equals that cutoff. The storage fix was subsequently promoted live with a fresh backup, staged hash/compile/JSON checks, `ha core check`, controlled restart, and `HTTP 200` readiness. The later `config_entry` compatibility fix is included in a new local candidate and is not yet live-promoted.

## Pinned real-HA compatibility follow-up

A disposable venv was built with Home Assistant `2026.7.4` on Python `3.14.3`. The Forecast.Solar manifest was read directly and requires `forecast-solar==5.0.1`; installing that exact dependency resolved the initial import blocker (`ModuleNotFoundError: forecast_solar`). The native helper gate then passed:

```text
core_version=2026.7.4
forecast_solar=5.0.1
helper_imported=true
helper_signature=(hass: HomeAssistant, config_entry_id: str) -> dict | None
adapter_core_version_supported=true
```

A real HA lifecycle smoke using the pinned distribution, actual config-entry manager, registries, entity platforms, and a synthetic fail-closed native source completed successfully:

```text
config_entry_setup=true
entry_state_after_setup=LOADED
entity_count_after_setup=26
fail_closed_status=binding_unavailable
config_entry_unload=true
```

The smoke initially exposed a Core compatibility path in `SolarAnalyticsCoordinator`: the coordinator did not pass `config_entry` to `DataUpdateCoordinator`. The local fix now passes `config_entry=entry`; local tests and compile remain green. No provider request or native refresh was issued by the smoke.

New candidate containing this fix:

```text
reports/candidate_20260803T142305Z/solar_analytics_v2_candidate.tar.gz
member_count=15
archive_sha256=f51d52397b5674a5759b20c0770cf515bb387ce1065c0fb1496372eecd1daa4d
traversal_safe=true
```

The disposable smoke used a native-source stub for lifecycle isolation; direct runtime-data/`wh_hours` exercise through the actual Forecast.Solar coordinator remains a separate native-runtime gate.

The direct native runtime-data gate then passed against the actual pinned Core class and helper:

```text
runtime_class=ForecastSolarDataUpdateCoordinator
wh_period_to_wh_hours=true
midnight_zero_filtered=true
non_midnight_zero_preserved=true
network_called=false
```

## Entity registry and dashboard audit

Read-only live registry inspection at `2026-08-03T14:30:22Z` found `24` Solar Analytics entities, all on platform `solar_analytics`, with no disabled entries, and exactly one Forecast.Solar config entry. No credentials or API-key values were read.

The local analytics Lovelace JSON was parsed after a text-only policy correction:

```text
json=ok
entity_references=42
unique_entity_references=36
legacy_retention_text=false
v2_retention_text=true
live_lovelace_mutation=false
```

The dashboard card/layout structure and entity references were not changed; only stale persistence timing/retention prose was aligned with the v2 contract.

## Pre-probe live baseline

Read-only entity state readback at `2026-08-03T14:29:09Z` remained fail-closed as designed:

```text
native_source_status=native_source_unavailable
validity_reason=native_update_not_observed
native_observation_sequence=null
forecast_profile_analysis_allowed=false
forecast_coverage=unknown
actual_coverage=unknown
accuracy=insufficient_data
valid_paired_days=0
```

No native refresh, provider request, REST fallback, service call, restart, or physical control was performed for this readback.

## Native refresh-interval evidence

At `2026-08-03T14:35:38Z`, the canonical actual sensors were still updating, while native Forecast.Solar entities retained their post-restart timestamp:

```text
sensor.power_production_now.last_updated=2026-08-03T14:09:09Z
sensor.energy_production_today.last_updated=2026-08-03T14:09:09Z
sensor.energy_current_hour.last_updated=2026-08-03T14:09:09Z
sensor.garage_cerbo_gx_pv_power.last_updated=2026-08-03T14:35:38Z
sensor.garage_cerbo_gx_pv_energy.last_updated=2026-08-03T14:35:37Z
```

This explains the still-empty listener observation without inferring a source failure: the native coordinator has not yet emitted its next normal update after restart. The free-account configuration uses an approximately hourly refresh interval. No manual refresh, `estimate()`, provider HTTP, REST fallback, or restart was used to accelerate it.

## Fresh log boundary

A bounded post-restart log read at `2026-08-03T14:34Z` contained:

```text
custom integration solar_analytics has not been tested by Home Assistant
REST request to https://api.forecast.solar/estimate/watthours/period/... returned HTTP 422
```

No new Solar Analytics exception, Forecast.Solar native exception, traceback, or update failure appeared in the filtered log window. The REST `422` belongs to an existing legacy consumer and confirms that REST retirement remains a later migration phase; no REST endpoint or automation was removed.

## Live SQLite v2 persistence gate

Read-only SQLite inspection at `2026-08-03T14:34Z` returned `PRAGMA integrity_check = ok`. The v2 store contains terminal blocked historical slots but no admissible native profile yet:

```text
v2_snapshot_slots=5
v2_snapshot_intervals=0
v2_lineages=0
v2_current_profile_cache=0
v2_accuracy_results=0
v2_daily_comparisons=0
```

The five existing slots are `status=blocked`, `admissible=0`, `lineage_id=__unavailable__`, with historical exclusion reason `helper_import_or_signature`. They are not retroactively backfilled after the disposable helper dependency was corrected, because the v2 contract forbids replacing a missed fixed snapshot with a later observation. Runtime actual sampling is active and reconciled:

```text
last_actual_sample=2026-08-03T14:34:07Z
reconciliation_status:2026-08-03=reconciled
```

## Native listener callback boundary

A later read-only probe at `2026-08-03T16:11:37Z` captured the first post-restart native coordinator transition. Forecast.Solar native entities updated at `2026-08-03T16:09:08Z`, and Solar Analytics changed from the prior `native_source_unavailable` state to:

```text
native_source_status=unsupported_native_contract
validity_reason=wh_hours_validation_failed
native_observation_sequence=null
forecast_profile_analysis_allowed=false
```

This state transition proves that the listener/capture path was reached after a native update; the remaining failure is downstream validation of the helper's actual `wh_hours` payload. No payload values are exposed in this report, and no manual refresh, provider HTTP, REST fallback, or physical service call was used.

## Remaining blockers

1. Native compatibility is proven for the current lifecycle: callback observed at `2026-08-03T18:38:33Z`, `native_observation_sequence=1`, profile `raw_count=34`, `invalid_count=0`, `valid_count=33`, and `forecast_profile_analysis_allowed=true`.
2. Canonical actual PV telemetry was restored after a bounded `victron_mqtt` config-entry reload. At `2026-08-03T19:28:12Z`, both actual entities updated and v2 reported `native_and_actual_valid_but_history_below_gate` rather than `actual_source_stale`.
3. Fresh v2 persistence is now demonstrated in the correct tables: `v2_runtime_state.last_actual_sample=2026-08-03T19:28:12Z`, `v2_accumulators=12`, and `reconciliation_status=reconciled`. The separately named legacy `runtime_state` table remains stale but is not the v2 store and is excluded from v2 acceptance evidence.
4. History remains below the accuracy gate: `valid_paired_days=0`, `accuracy_ready=false`, `v2_daily_comparisons=0`. This requires fresh completed paired days and must not be backfilled from the legacy table.
5. The 72-hour native-only soak has not started.
6. Legacy REST consumer migration and permanent REST removal remain blocked until the history/soak gates complete.
7. Final entity-registry/dashboard/Recorder/statistics audit remains pending.
8. No production-ready/project-complete claim is valid yet.

## Diagnostic candidate promotion and post-restart boundary

Candidate `candidate_20260803T162049Z` was promoted after local `49`-test, compile, JSON, archive/hash, backup, and pre-install `ha core check` gates. The controlled restart reached:

```text
ready=true
http=200
core=2026.7.4
post_restart_ha_core_check=success
```

Immediate post-restart Solar Analytics state was expectedly fail-closed while the native entry was still initializing:

```text
native_source_status=native_source_unavailable
validity_reason=entry_unloaded
native_observation_sequence=null
```

The first diagnostic candidate exposed a blocking synchronous helper import in the event loop. A RED/GREEN fix was completed locally (`51 passed`) and included in candidate `candidate_20260803T171751Z`:

```text
archive_sha256=b8b41d7e9858bbd02e1995d1ae5c608d6f7e20b727551126959d849c22938e3a
archive_bytes=26236
members=15
```

That second candidate passed independent archive/hash/traversal, remote staging compile/JSON, backup, promotion, `ha core check`, and controlled restart. Fresh post-restart logs at `2026-08-03T17:20:38Z` contained no Solar Analytics or Forecast.Solar blocking-import warning; unrelated third-party integration warnings were excluded from this PV report.

The immediate second-candidate state remains fail-closed during native entry initialization:

```text
native_source_status=native_source_unavailable
validity_reason=entry_unloaded
native_observation_sequence=null
forecast_profile_analysis_allowed=false
accuracy=insufficient_data
valid_paired_days=0
```

The native PV entities and canonical actual PV sensors are loaded and updating; no manual refresh, provider HTTP, REST fallback, or physical-control path was used.

## Sparse overnight zero-energy contract fix

Pinned Core `2026.7.4` source replay established that native `wh_period` is sparse overnight. Core preserves an explicit non-midnight `0 Wh` boundary, and `forecast-solar==5.0.1` does not densify it. One deterministic replay produced:

```text
overnight_duration_seconds=31383.0
overnight_energy_wh=0.0
```

The previous normalizer rejected every duration above `7200 s`, so this valid zero-energy cell blocked the entire profile. The minimal fail-closed fix accepts an overlong cell only when its normalized energy is exactly `0.0 Wh`; positive-energy gaps, malformed/naive timestamps, duplicates, negative, and non-finite values remain blocked. No interpolation or synthetic energy is introduced.

Local verification after the fix:

```text
zero-energy regression + positive-gap rejection: passed
full suite: 51 passed
compileall: passed
pure/component native.py SHA-256 equal
pure/component storage_v2.py SHA-256 equal
adapter_version=2.0.1
normalization_version=native-period-end-v2.1
```

## Sparse-zero candidate live deployment

Candidate `candidate_20260803T172750Z` was independently validated and promoted after backup, transfer hash comparison, staging compile/JSON, `ha core check`, and controlled restart:

```text
archive_sha256=47eb1d7efe44d3f54912faebff4fc79143f6974df256d193f7c8cfde4dbe745e
archive_bytes=26241
members=15
ready=true
http=200
core=2026.7.4
post_restart_ha_core_check=success
```

Fresh PV log filtering at `2026-08-03T17:30:45Z` contained no Solar Analytics or Forecast.Solar exception, traceback, or blocking-import warning. Unrelated third-party integration warnings were excluded from this PV report.

The immediate post-restart state remains correctly fail-closed until the next normal native callback:

```text
native_source_status=native_source_unavailable
validity_reason=entry_unloaded
native_observation_sequence=null
forecast_profile_analysis_allowed=false
accuracy=insufficient_data
valid_paired_days=0
```

Current read-only live SQLite evidence:

```text
integrity=ok
v2_snapshot_slots=5
v2_lineages=0
v2_current_profile_cache=0
v2_accuracy_results=0
v2_daily_comparisons=0
last_actual_sample=2026-08-03T11:52:42.410913Z
```

The actual PV entities are currently updating, but the post-third-candidate fresh persistence row has not yet been demonstrated. A read-only diagnostic probe is scheduled at `2026-08-03T18:15:00Z` after the expected native interval.

## Energy Dashboard import off-event-loop fix

The first post-third-candidate probe exposed a separate Solar Analytics blocking-call warning at `native_adapter.py:132` for synchronous import of `homeassistant.components.energy.data`. The helper import had already been moved off-loop, but binding discovery still imported the Energy Dashboard module synchronously.

The local fix now performs that module import through `hass.async_add_executor_job`, with a regression assertion covering both module-import paths. Local verification:

```text
targeted off-event-loop test: passed
full suite: 51 passed
pinned real-HA lifecycle smoke: setup=True, LOADED, 26 entities, unload=True
```

Candidate `candidate_20260803T173650Z` was promoted after a new backup and full transfer/staging/hash gates:

```text
archive_sha256=f5b104315ef6a79202ca6bf519a74d0edc246b91543e06f8e85317759814257b
archive_bytes=26253
members=15
backup=/config/backups/solar_analytics_v2_energy_binding_import_predeploy_20260803T173650Z
ready=true
http=200
core=2026.7.4
post_restart_ha_core_check=success
```

Fresh PV log boundary at `2026-08-03T17:39:22Z` contains no Solar Analytics or Forecast.Solar log lines. The live adapter now reaches `native_update_not_observed` rather than `entry_unloaded`, showing that binding/entry resolution completed and the listener is waiting for a current native callback:

```text
native_source_status=native_source_unavailable
validity_reason=native_update_not_observed
native_observation_sequence=null
forecast_profile_analysis_allowed=false
```

Live SQLite remains structurally healthy but has no admissible lineage or fresh actual sample:

```text
integrity=ok
v2_snapshot_slots=5
v2_lineages=0
v2_current_profile_cache=0
v2_accuracy_results=0
v2_daily_comparisons=0
last_actual_sample=2026-08-03T11:52:42.410913Z
```

The scheduled strictly read-only probe remains at `2026-08-03T18:15:00Z`; no manual refresh, provider HTTP, REST fallback, physical service call, or notification was used.

## Pinned native update cadence

The disposable pinned Core `2026.7.4` source confirms `ForecastSolarDataUpdateCoordinator` uses a `30 minute` update interval when an API-key option is present and `1 hour` otherwise. Only option-key presence was observed; no credential value was read or persisted. The live native entry is therefore given a 30-minute probe window after the `17:38Z` restart, without initiating any refresh.

## Admissible native callback and actual-source blocker

A later read-only observation confirmed the post-fix native callback:

```text
native_observed_at=2026-08-03T18:38:33.771351Z
native_updated_at=2026-08-03T18:38:33.765178Z
native_observation_sequence=1
native_source_status=ok
forecast_profile_analysis_allowed=true
lineage_id=4ff42f4773ca4611bb423dfab3358eb9
profile_raw_count=34
profile_invalid_count=0
profile_valid_count=33
```

The sparse overnight zero-energy period was admitted:

```text
energy_wh=0.0
duration_seconds=31819.0
valid=true
```

SQLite read-only evidence after the callback:

```text
integrity=ok
v2_snapshot_slots=5
v2_snapshot_intervals=0
v2_lineages=1
v2_current_profile_cache=1
v2_accuracy_results=7
v2_daily_comparisons=0
```

The new blocker is canonical actual telemetry freshness, not native forecast compatibility:

```text
overall_status=actual_source_stale
validity_reason=age_seconds:5573.9
accuracy_ready=false
valid_paired_days=0
last_actual_sample=2026-08-03T11:52:42.410913Z
```

No accuracy or underperformance claim is admitted while actual coverage is stale. The legacy REST `422` remains a separate retained consumer and is not used as a v2 fallback.

## Canonical actual-source diagnosis

The stale actual entities were traced read-only through Home Assistant's entity registry:

```text
sensor.garage_cerbo_gx_pv_power: platform=victron_mqtt, unique_id=sensor.victron_mqtt_system_0_system_dc_pv_power
sensor.garage_cerbo_gx_pv_energy: platform=victron_mqtt, unique_id=sensor.victron_mqtt_system_0_system_dc_pv_energy
configured host=venus.local
configured port=8883
configured ssl=true
password=not_read
```

`venus.local` resolves to `192.168.1.146`; read-only TCP checks showed `192.168.1.146:8883` open, while port `1883` was not open on `192.168.1.146` or `192.168.1.115`. Recent HA log history before the native callback (20:30–20:53 local) showed `victron_mqtt` attempting `192.168.1.115:1883` and `192.168.1.146:1883`, receiving `Connection refused`. This is an external Victron MQTT source/configuration blocker; Solar Analytics does not modify that integration, broker, or physical device.

Until the canonical actual source resumes updates, v2 remains fail-closed for actual-vs-forecast accuracy, coverage, paired-day readiness, and underperformance claims. No reconnect service, physical call, or unrelated integration mutation was performed.

## Recheck after explicit wait

At `2026-08-03T19:25:44Z`, after the explicit wait, the canonical actual entities still had the same last-update timestamps:

```text
sensor.garage_cerbo_gx_pv_power last_updated=2026-08-03T17:40:39.203157Z
sensor.garage_cerbo_gx_pv_energy last_updated=2026-08-03T17:38:35.016019Z
actual_source_stale age_seconds=6173.9
```

Native forecast compatibility remained admitted (`native_source_status=ok`, `native_observation_sequence=1`, `forecast_profile_analysis_allowed=true`). SQLite remained healthy with one lineage, one current profile cache, zero daily comparisons, and the unchanged stale runtime actual sample; the periodic insufficiency results increased to `9` without creating accuracy readiness. The external `victron_mqtt`/MQTT source blocker therefore remains unresolved.

## Bounded Victron source recovery

After the user resumed the task, the configured endpoint became reachable again (`192.168.1.146:8883=open`). The existing `victron_mqtt` config entry was reloaded once through the supported Home Assistant service:

```text
service=homeassistant.reload_config_entry
entry_id=[REDACTED]
result=success
affected_entities=[]
physical_service_calls=0
```

Post-reload readback confirmed:

```text
sensor.garage_cerbo_gx_pv_power last_updated=2026-08-03T19:28:12.829896Z
sensor.garage_cerbo_gx_pv_energy last_updated=2026-08-03T19:28:12.834860Z
analysis_status=insufficient_data
validity_reason=native_and_actual_valid_but_history_below_gate
```

The prior actual-source stale condition is resolved for this lifecycle. V2 persistence is fresh in the correct table (`v2_runtime_state.last_actual_sample=2026-08-03T19:28:12.829896Z`); the old legacy `runtime_state` row is not v2 evidence. No Victron configuration, credentials, broker settings, physical entity, or non-PV device was modified.

## Native-only soak start

A 72-hour PV-only native soak baseline was recorded at `2026-08-03T19:28:33Z` UTC after actual-source recovery. Durable strictly read-only checkpoints were scheduled as cron job `26d6f1d301f6`, 12 runs every 6 hours, next run `2026-08-04T01:30:44Z` UTC. Each checkpoint reads native/actual PV states, v2 SQLite counters and bounded fresh logs only; it performs no refresh, provider HTTP, REST fallback, service call, notification, restart, or physical action.

## Snapshot/history gate interpretation

The current `v2_daily_comparisons=0` is expected at this point. All five existing snapshot slots are terminal historical blockers with `exclusion_reason=helper_import_or_signature` from before the native/import fixes; their immutable identity forbids backfill. The next new morning slot is scheduled for `2026-08-04T03:00:00Z` (`06:00 Europe/Kyiv`). A daily comparison can appear only after its target forecast day completes, so accuracy readiness cannot be inferred from the current profile cache or manufactured by reusing an old slot.

## Authorized historical Recorder backfill amendment

The user explicitly authorized changing the plan to permit retroactive forecast backfill. The authoritative goal set, plan, specification, and project goal were amended so that historical backfill is allowed only as an explicitly marked, separate capture mode/lineage. Scheduled native slots remain immutable; historical records cannot be relabeled as current native observations, cannot update `current_lineage_id`, and cannot satisfy the 72-hour native-only soak.

The amended path uses additive `v2_backfill_*` tables and an idempotent run identity. Legacy REST history is never admitted as native Forecast.Solar provenance; this run is classified as `historical_legacy_rest` and its accuracy result is diagnostic/backfill-only.

## Recorder audit and source provenance

Read-only Recorder inspection used `/config/home-assistant_v2.db` through `states_meta`/`metadata_id` timestamp-scoped queries. No Recorder database write was performed. The selected forecast source was:

```text
forecast_source_entity=sensor.forecast_solar_hourly_api
source_kind=historical_legacy_rest
recorder_rows=1
result_keys=34
source_observed_at_utc=2026-07-27T11:55:29.360044Z
source_timezone=Europe/Kyiv
forecast_payload_sha256=sha256:5571cb65140c9e0c4ab4671d94f20e8b77216ab56e4bdf3555e433aa9519feb0
period_end_range=2026-07-27 05:18:59 .. 2026-07-28 20:49:14 Europe/Kyiv
```

The native Forecast.Solar Recorder scalar entities were not used to reconstruct a detailed profile: they contain scalar history only. Canonical actual Recorder data was read from `sensor.garage_cerbo_gx_pv_power` and `sensor.garage_cerbo_gx_pv_energy` for the two forecast dates:

```text
actual_power_recorder_rows=40517
actual_energy_recorder_rows=19309
```

Actual power was integrated time-weighted with a 15-minute maximum admissible gap. The cumulative energy counter reconciled on both target dates. No interpolation or synthetic overnight production was introduced.

## Backfill implementation and live deployment

The additive backfill schema was implemented as integration version `2.1.0`, schema version `3`, with a pure parser/integration module, Recorder runner, separate lineage, source digest, snapshot identity, interval quality rows, daily comparisons, and backfill accuracy result. Local gates passed:

```text
full suite=57 passed
compileall=passed
manifest JSON=passed
pure/component backfill.py SHA-256 equal
pure/component storage_v2.py SHA-256 equal
```

Candidate `candidate_20260803T200442Z` was transferred and staged:

```text
archive_sha256=478f1af3a52e093457af1998c0a960267ec80daafd1761d4078784938adaa31b
members=17
traversal_safe=true
remote_staging_compile=passed
remote_manifest=2.1.0
predeploy_component_backup_sha256=53ffe18bbef0305588d743906852333ac36f1f2315e37a16a1ecf3fc89ee405e
predeploy_sqlite_backup=/config/backups/solar_analytics_v3_backfill_predeploy_20260803T200442Z.sqlite
predeploy_sqlite_backup_sha256=9f350c728e479895007d119dffba3eb25f1922bbf10766496e381b39b952df98
predeploy_sqlite_backup_integrity=ok
ha_core_check_before=success
controlled_restart=success
readiness_http=200
ha_core_check_after=success
```

The post-backfill idempotency patch was promoted as `2.1.1` after local regression/full-suite/compile/JSON gates and a separate component backup:

```text
candidate_archive_sha256=6acb129e0185e1cce9d86ba98dcbd2d11a082bf8cbd3db93c149e459f0086536
live_version=2.1.1
prepatch_component_backup_sha256=3732f56e4b788534a4392852334bf0919b92248277bdb6ca99b9d7fd4fde8ad3
ha_core_check_after_patch=success
```

The patch makes repeated accuracy writes for one `run_id` replace the prior result instead of accumulating timestamp-only duplicates. No second live backfill write was performed.

## Backfill commit and independent readback

The dry-run completed first and produced the following fail-closed result:

```text
run_id=recorder-e19beaaaa0b2851b61ef39d9
lineage_id=backfill-recorder-e19beaaaa0b2851b61ef39d9
forecast_periods=34
valid_forecast_periods=33
invalid_forecast_periods=1
write=dry_run_no_database_write
```

The single live write then committed atomically:

```text
write=historical_backfill_committed
storage_integrity=ok
```

Independent read-only SQLite verification returned:

```text
integrity=ok
schema=3
v2_lineages=2
v2_snapshot_slots=6
v2_snapshot_intervals=34
v2_daily_comparisons=0
v2_accuracy_results=19
v2_backfill_runs=1
v2_backfill_snapshots=2
v2_backfill_snapshot_intervals=34
v2_backfill_intervals=33
v2_backfill_daily_comparisons=2
v2_backfill_accuracy_results=1
```

The native namespace remained isolated:

```text
native_lineage=4ff42f4773ca4611bb423dfab3358eb9
backfill_lineage=backfill-recorder-e19beaaaa0b2851b61ef39d9
current_lineage_id=4ff42f4773ca4611bb423dfab3358eb9
native_daily_comparisons=0
native_snapshot_slots_untouched_by_backfill=true
```

The 34 snapshot cells contain 33 valid cells and one fail-closed `missing_previous_boundary` cell. The historical REST payload omits explicit overnight zero cells, so both target dates remain below the 95% forecast/90% actual/paired coverage gates:

```text
2026-07-27 forecast_coverage=0.6469907407 actual_coverage=0.6398941437 paired_coverage=0.6118171296 valid_paired_day=false reason=coverage_below_gate reconciliation=reconciled
2026-07-28 forecast_coverage=0.6450462963 actual_coverage=0.6110216123 paired_coverage=0.5758564815 valid_paired_day=false reason=coverage_below_gate reconciliation=reconciled
backfill_valid_paired_days=0
backfill_accuracy_ready=false
native_accuracy_ready=false
native_soak_completed=false
```

Therefore the database is now populated with auditable historical forecast cells and actual interval evidence, while the runtime correctly reports that no valid paired historical day has been proven. This is a data-quality result, not a failed write.

## Current post-backfill status and remaining gates

- Native current lifecycle is still fail-closed after the controlled restart until a normal native listener callback is observed; no manual refresh or provider request was issued.
- Actual PV source is currently valid/reconciled after the bounded existing `victron_mqtt` reload.
- Historical backfill is present only in `v2_backfill_*`; it does not count toward native-only soak or native accuracy readiness.
- Legacy REST migration/removal remains pending and was not performed.
- Physical service calls: `0`; notifications: `0`; Forecast.Solar config entry was not changed.

## 2026-08-04 lifecycle regression and recovery

After the controlled restart, the canonical Victron actual source became stale again. Read-only diagnosis confirmed the existing entry remained unchanged:

```text
victron_mqtt port=8883
victron_mqtt ssl=true
venus.local:8883=open
venus.local:1883=closed
```

The already-approved bounded reload of the existing Victron entry returned `success`; no host, port, TLS, credential, broker or Forecast.Solar configuration was changed. Actual PV states recovered at:

```text
power_last_updated=2026-08-04T01:58:54Z
energy_last_updated=2026-08-04T01:58:54Z
```

The reload regression then exposed a real HA lifecycle bug. Core `2026.7.4` logged:

```text
2026-08-04T02:00:42Z
[custom_components.solar_analytics]
Error unloading entry Solar Analytics for solar_analytics
homeassistant.helpers.update_coordinator.__async_remove_listener_internal
KeyError: 12
```

Root cause: the adapter registered the native unsubscribe callback with `entry.async_on_unload()` and also invoked the same raw unsubscribe manually from `async_unload()`. The fix wraps the unsubscribe in an idempotent `remove_once()` closure shared by both paths.

Regression evidence:

```text
RED: test_native_adapter_listener_cleanup_is_idempotent -> ValueError: list.remove(x): x not in list
GREEN: targeted native-adapter tests passed
GREEN: full local suite = 58 passed
compileall=passed
```

Candidate `2.1.2` was staged and promoted with:

```text
candidate=reports/candidate_20260804T020441Z/solar_analytics_v2_candidate.tar.gz
candidate_sha256=27539e0d0da927107ae5be2ff2a7276dfc539960f38baf7837f69b76acf2a1c5
live_version=2.1.2
rollback_backup_sha256=87ba1744ca031cb1deda33c016e2dd0c9df4a6a98cc10f1c44fc6e12ea96521c
ha_core_check=success
controlled_restart=success
readiness_http=200
```

An intermediate deployment attempt left a rollback directory under `custom_components`, which HA correctly attempted to import and reported as `ModuleNotFoundError`. The rollback directory was moved to `/config/backups/`; the current `/config/custom_components` contains only the real `solar_analytics` package. The old `05:05:47Z` import error remains in the historical log buffer; no matching setup failure occurred after the directory was moved.

Real HA unload/reload smoke of `2.1.2` returned `success`. A fresh log-window query found no new `KeyError: 12`, `Error unloading entry`, `setup failed`, or `ModuleNotFoundError` for Solar Analytics.

Current post-recovery readback:

```text
generated_at=2026-08-04T02:13:11Z
native_status=native_source_unavailable
validity_reason=native_update_not_observed
actual_power_status=valid
actual_energy_status=valid
v2_last_actual_sample=2026-08-04T02:07:13.251949Z
current_lineage_id=4ff42f4773ca4611bb423dfab3358eb9
sqlite_integrity=ok
```

The remaining `native_update_not_observed` state is intentional fail-closed behavior after restart/reload: the adapter will admit the profile only after the next normal Forecast.Solar coordinator callback. No manual native refresh was issued.

## Corrected native callback evidence — 2026-08-04

The bounded watcher exited with `exit 1` because it incorrectly required `observation_sequence > 5`. The in-memory observation sequence is reset on process/restart boundaries; the first valid callback after the `2.1.2` lifecycle deployment therefore had sequence `1`, not `6`.

The callback did occur and was independently confirmed through HA state and SQLite:

```text
native_status=ok
forecast_profile_analysis_allowed=true
native_observation_sequence=1
native_observed_at=2026-08-04T03:07:11.536712Z
native_updated_at=2026-08-04T03:07:11.533464Z
payload_sha256=sha256:048029c40c82c6c025b43d8031757e0ac1e77177183ee555d109cea1decb42d4
lineage_id=4ff42f4773ca4611bb423dfab3358eb9
current_lineage_id=unchanged
sqlite_integrity=ok
ha_core_check=success
last_actual_sample=2026-08-04T03:48:04Z
```

This closes the post-restart native freshness admission gate, but does not close the 72-hour native-only soak or accuracy readiness. The next watcher must compare `observed_at_utc` (or a captured callback boundary), not assume a monotonically increasing sequence across restarts.
