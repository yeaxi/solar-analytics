# Solar Analytics — live verification report

**Verification date:** 2026-08-02 20:50 UTC / 23:50 Europe/Kyiv
**HA Core:** 2026.7.4
**Target:** `homeassistant.local`
**Overall status:** `LIVE_READ_ONLY_FAIL_CLOSED`
**Analytics readiness:** `PROVENANCE_BLOCKED_DATA_INSUFFICIENT`
**Audit/deployment note:** official Forecast.Solar period-end semantics are confirmed and the shared REST producer derives capacity/geometry from the native contract. The public REST response does not prove requested-model provenance and does not carry native inverter/damping inputs, so profile analytics intentionally remain fail-closed.



## Continuation live verification — corrected fail-closed revision

**Verification timestamp:** `2026-08-03T06:43:22Z`
**Activation:** staged component deployment plus a narrowly scoped REST-template startup guard; final readiness `attempt=1`, HTTP `200`.
**Final HA Core:** `2026.7.4`
**Final status:** `LIVE_READ_ONLY_FAIL_CLOSED`

### Deployment evidence

- Final component archive local/remote SHA-256: `3723b44d9604daf652efa6c492c97fa84525a1ce7f33bf99ad6e4b9e2035009e`.
- Final deployed component hashes: `analytics.py=a0556fe5fc06a4d61139bb846810d6f1ed9d084573879c0d1c965ebc76fa52a5`; `coordinator.py=aba19f4d2a17f601e65b6dc9fc1c3fd69a1eab7cc3ae630d79969c6db4defc6b`; `forecast_contract.py=91e620e0ca2b6ade19d41c0a817027cee4a407d8d54041dcf4b9504038b8d40d`.
- Final live `/config/packages/energy_split.yaml` SHA-256: `a434700aa9113ec246cfdec163fdd3f52b79df97163f7b33a80726aaa6a975cf`.
- Scoped rollback paths: `/config/backups/solar_analytics_provenance_20260803T062613Z`, `/config/backups/solar_analytics_snapshot_gate_20260803T063728Z`, `/config/backups/solar_analytics_rest_guard_20260803T063947Z`, `/config/backups/solar_analytics_rest_guard_fix_20260803T064145Z`.
- Final pre-restart and post-restart `ha core check`: PASS. Final startup guard log filter and Solar Analytics error filter: empty.

### Final read-only runtime evidence

```text
sensor.forecast_solar_hourly_api = 50.301 kWh
sensor.forecast_solar_hourly_api_2 = 404 / absent
native modules_power_w = 5360.0
native inverter_size_w = 5190.0
native azimuth = 138.0
native declination = 33.0
native morning/evening damping = 0.0 / 0.0
native model_fingerprint_sha256 = sha256:66de41eee7fafacfffee1fa6e36fc35069ec4fb05ed4a33992435e79f50e0934
forecast_solar_rest_url = .../33/-42/5.36?time=iso8601
analysis_status = forecast_contract_unavailable
forecast_profile_analysis_allowed = false
forecast_solar_expected_kwh = null
accuracy = insufficient_data
heatmap = unavailable
refresh automation = on
```

The main entity ID and numeric consumer contract remain unchanged. The native fingerprint is read successfully, but the stock REST producer still has no independently verifiable URL/model handshake; no profile analytics were enabled.

### Snapshot quarantine and persistence

The final read-only SQLite query returned:

```text
analysis_results=148
daily_results=2
forecast_snapshots=2
interval_accumulators=24
intervals=24
admissible_forecast_solar_rows=0
```

The two snapshot rows are one old `forecast_solar` row with `profile_status=complete` but `model_status=rest_capacity_unverified`, `normalization_blocked=true`, and no verified producer provenance, plus one `vrm` row with `profile_status=unavailable_scalar_only`. The old Forecast.Solar row is retained for audit and is quarantined by the new admission predicate; it is not used for daily metrics and no new blocked Forecast.Solar profile row was inserted after the corrected deploy. No retroactive forecast rewrite or SQLite deletion was performed.

### Relay preservation and physical-action audit

The user explicitly approved preserving existing relay states during restart. Final readback preserved:

```text
switch.146235046566292_power = on; sensor.bathroom_hot_water_boiler_power = 0 W
switch.shelter_dehumidifier_plug_outlet = on; sensor.shelter_dehumidifier_power = 235.851 W
switch.boiler_socket_1 = off; sensor.parents_boiler_parents_home_boiler_normalized_power = 0.0 W
switch.bak_akamuliator_3_kvt_switch = off; sensor.bak_akamuliator_3_kvt_power = 0.0 W
```

Physical service calls: **none**. No Forecast.Solar config-entry mutation, ESS/PV/inverter/battery call, Solcast install, or synthetic VRM hourly profile was performed.

The existing package also contains an unrelated duplicate top-level `automation` key warning in the live YAML; it was not structurally rewritten in this scoped correction. The corrected REST startup guard itself produced no `value_json` or REST entity-add error in the final restart.

## Scope and safety gate

- Custom component, package, separate Lovelace view, and Plotly resource were installed.
- Existing Energy Dashboard, Energy Split Dashboard, Solar Forecast Comparison Dashboard, and Energy Planner registry entries were not structurally changed. The shared `sensor.forecast_solar_hourly_api` producer URL was intentionally changed to native-derived `5.36 kWp`, so its forecast values may change for existing consumers.
- No `turn_on`, `turn_off`, `toggle`, ESS, PV, inverter, battery, or other physical service call was executed.
- Forecast.Solar was not modified automatically.
- Solcast, API credentials, tokens, passwords, and connection strings were not added or retained.
- `input_boolean.solar_analytics_notifications_enabled` remains `off`.

## Deployment and runtime gates

| Gate | Result | Evidence |
|---|---|---|
| Pre-live backup | PASS | `/config/backups/solar_analytics_live_20260802T185804Z`; native-REST change backup `/config/backups/solar_analytics_native_rest_20260802T201856Z`; safety correction backup `/config/backups/solar_analytics_safety_20260802T203953Z` |
| `ha core check` after deployment | PASS | `Command completed successfully.` |
| Final `ha core check` after fail-closed/presentation guards | PASS | `Command completed successfully.` |
| Controlled restart/readiness | PASS | HA Core reported `2026.7.4`; readiness polling succeeded |
| Final targeted log window | PASS | No SQLite thread error, services.yaml error, Solar Analytics traceback, or update error |
| Config entry | PASS | One loaded Solar Analytics entry; 19 registered Solar Analytics entities, including native capacity-derived REST URL telemetry |
| SQLite open/read | PASS | Read-only query succeeded; WAL present and readable |
| Physical control safety | PASS | Static audit and execution history contain no physical service call |

## Live contract readback

### Native Forecast.Solar model

The component read the native model contract and exposed this fingerprint:

```text
modules_power_w: 5360.0
inverter_size_w: 5190.0
azimuth: 138.0
declination: 33.0
morning_damping: 0.0
evening_damping: 0.0
model_fingerprint: 5360.0:5190.0:138:33
```

### Shared Forecast.Solar REST entity

```text
source: sensor.forecast_solar_hourly_api
resource_template: native-derived from sensor.solar_analytics_native_forecast_solar_module_power
native_modules_power_w: 5360.0
forecast_solar_rest_url: .../33/-42/5.36?time=iso8601
declared_unit: kWh
effective_unit: Wh
value_semantics: energy
timestamp_semantics: end
contract_status: metadata_mismatch
source_state: 55.927
source_available: true
source_fresh: true
model_status: rest_capacity_unverified
normalization_blocked: true
rest_plane_capacity_w: null
rest_capacity_source: null
forecast_profile_analysis_allowed: false
live_state: 55.927 kWh
```

A direct read-only GET of the native-derived URL returned HTTP 200, 34 result points, and `sum(result) = 55927 Wh = 55.927 kWh`, exactly matching the live shared REST sensor. The native capacity entity and URL are recalculated from the native Forecast.Solar subentry on coordinator refresh; a startup/model-change automation invokes only the non-physical `homeassistant.update_entity` refresh, while the REST scan interval remains 3600 seconds.

## Read-only Forecast.Solar contract audit

Official Forecast.Solar API documentation (`https://doc.forecast.solar/api:estimate`) states that `watts` and `watt_hours_period` values represent the period **from the previous timestamp to the timestamp in the key**. Therefore the live irregular keys such as `05:27:27`, `06:00:00`, and `20:41:40` are period-end timestamps, not instantaneous samples and not necessarily one-hour buckets.

The live REST YAML derives its capacity/geometry URL from `sensor.solar_analytics_native_forecast_solar_module_power`. The native contract produces `/33/-42/5.36`; `138° -> -42°` remains geometry-aligned and capacity matches native `5360 W`. This does **not** prove full native-model equivalence: the public URL omits native inverter/damping inputs and the response does not echo the requested model. The shared entity ID is unchanged, but Solar Analytics keeps profile metrics blocked until producer provenance is verifiable.

The API was temporarily unavailable during the first deployment attempt, but after the corrected restart the direct read-only endpoint returned HTTP 200 and matched the shared entity exactly. No Forecast.Solar config entry mutation, credential, or physical service call was used.

### Provenance blocker proof

The exact HA Core 2026.7.4 source was inspected read-only:

- `homeassistant/components/rest/__init__.py` renders `resource_template`, calls `rest.set_url(...)`, and then `rest.async_update()`; the effective URL is not exposed as a REST entity attribute;
- `homeassistant/components/rest/sensor.py` exposes only configured `json_attributes` and the rendered value template, so the existing sensor cannot independently stamp the URL used by the REST client;
- `homeassistant/components/forecast_solar/coordinator.py` passes native inverter and morning/evening damping into the native `ForecastSolar` client, while the shared YAML REST entity is a separate stock producer;
- Forecast.Solar API documentation (`https://doc.forecast.solar/api:estimate`) documents `result`, metadata and ratelimit, and explicitly describes `watt_hours_period` semantics, but the documented response does not echo request URL, inverter, damping or a provider-signed model fingerprint.

Conclusion: a companion value generated from the same expected YAML template would only repeat the expected URL and is not independent provenance evidence. The local evaluator therefore accepts only an explicitly owned fetcher that reports the exact URL, URL SHA-256, complete non-secret model fingerprint, response payload SHA-256 (checked independently by Solar Analytics), and response generation. The consumer-owned barrier requires two distinct valid generations. No such owned producer is deployed, so `model_status=rest_capacity_unverified`, `normalization_blocked=true`, and `forecast_profile_analysis_allowed=false` remain the correct live contract.

### Victron VRM Forecast

```text
vrm_hourly_profile_status: unavailable_scalar_only
```

Only existing scalar daily/current/next-hour entities are used. No synthetic hourly VRM profile or confidence band is generated.

## Fail-closed runtime evidence

Latest post-safety-patch live states:

```text
sensor.solar_analytics_analysis_status = forecast_contract_unavailable
sensor.solar_analytics_solar_forecast_accuracy = insufficient_data
sensor.solar_analytics_solar_performance_heatmap = unavailable
forecast_profile_analysis_allowed = false
source_state = 55.927
source_available = true
source_fresh = true
model_status = rest_capacity_unverified
normalization_blocked = true
rest_plane_capacity_w = null
rest_capacity_source = null
valid_coverage = 0.0
```

The REST producer is live and fresh, but the coordinator intentionally does not infer model provenance from the derived URL or a native capacity fallback. Because the public response does not echo the requested URL and omits native inverter/damping inputs, profile snapshots, accuracy, anomaly and heatmap metrics remain fail-closed.

## SQLite persistence — original 2026-08-02 baseline

Read-only final query:

```text
analysis_results: 7
 daily_results: 1
forecast_snapshots: 0
interval_accumulators: 2
        intervals: 2
```

The database and WAL are present:

```text
/config/solar_analytics/solar_analytics.sqlite
/config/solar_analytics/solar_analytics.sqlite-wal
```

The latest stored analysis has `overall_status=forecast_contract_unavailable` and `forecast_profile_analysis_allowed=false`.

At the original 2026-08-02 verification point, `forecast_snapshots=0` was expected because the producer provenance/full-model gate remained blocked. The continuation verification above is the authoritative current SQLite state; it retains one stale blocked row for audit and reports `admissible_forecast_solar_rows=0`. The existing read-only Recorder backfill report is:

```text
reports/backfill_report_2026-08-02.json
```

It explicitly does not apply the current forecast retroactively and does not reconstruct a VRM hourly profile.

## Lovelace and resources

- Dashboard registry entry: `Solar Analytics`
- URL path: `/lovelace/solar-analytics`
- Dashboard storage key: `lovelace.solar_analytics`
- Dashboard views: `1`
- Frontend route: HTTP `200`
- Plotly resource: HTTP `200`, `3,125,584` bytes
- ApexCharts core resource: HTTP `200`, `533,680` bytes
- ApexCharts card resource: HTTP `200`, `1,627,706` bytes

Baseline comparison from the pre-live backup:

```text
native dashboard registry: unchanged (4 before, 5 after; one new Solar Analytics entry)
native resource registry: unchanged (10 before, 11 after; one new Plotly entry)
```

A browser screenshot was **not generated**: no Safari/Chrome window was open for background capture, and no foreground window was opened without explicit user direction. HTTP route/resource checks are not claimed as full browser-rendering verification.

## Local verification

Latest local run at `2026-08-03T06:44:19Z`:

```text
pytest: 30 passed
compileall: passed
JSON/YAML validation: passed
helper/analytics copies: identical
credential literal scan: 0 matches
```

The regression suite includes the SQLite executor-thread handoff test and the Forecast.Solar profile gate tests for:

- aligned native model;
- model mismatch;
- unresolved period timestamp semantics;
- missing/invalid native contract;
- native URL geometry/capacity/damping bounds;
- unavailable/stale REST source fail-closed handling;
- ambiguous Forecast.Solar plane selection fail-closed handling;
- deterministic non-secret SHA-256 fingerprint changes for every model-shaping field;
- exact owned-fetcher provenance match/mismatch, request/payload digest mismatch, stock bridge rejection, response-generation reuse, and consumer-owned two-refresh barrier;
- missing required inverter/damping values and no profile snapshot before the provenance gate.

Prior deployed file SHA-256 values from the 2026-08-02 baseline:

```text
analytics.py       2a3c4b4887889787501126e0605a4490ed51ec5ae13bcb6c38be11145f6c5931
coordinator.py     93b90dc016d0dd71412392640522677bca9eab0fa2906dcc9edda545e8cc2f20
binary_sensor.py   1e0d268eefc1932bbdee73aa6c89d904184e7b44ab06e3980f2589a7c63c1861
sensor.py          8c4698d0435aa80552c8a3126d5a8e19bf0d2c8c6c2da2ece57868ebbd624a2b
storage.py         e9e27fa5b660272d709f9240350a949431622ec835a989bba288662cf6c738c1
services.yaml      993174aa11edb4289d0cdee0fec9671282f63d14cf786d92d9d57f362cd55e35
forecast_contract.py b31af5706540c0ce18eb3e67e324623851c8ec5462e92cc4e1df5da885182064
energy_split.yaml bcb14253b3d29277025203f53372a004de495876d86e8b4347c5a82f29130bc7
```

Current local/redeploy source hashes (computed at `2026-08-03T06:44:19Z`):

```text
solar_analytics/analytics.py                                      a0556fe5fc06a4d61139bb846810d6f1ed9d084573879c0d1c965ebc76fa52a5
solar_analytics/forecast_contract.py                              91e620e0ca2b6ade19d41c0a817027cee4a407d8d54041dcf4b9504038b8d40d
home_assistant/custom_components/solar_analytics/analytics.py     a0556fe5fc06a4d61139bb846810d6f1ed9d084573879c0d1c965ebc76fa52a5
home_assistant/custom_components/solar_analytics/forecast_contract.py 91e620e0ca2b6ade19d41c0a817027cee4a407d8d54041dcf4b9504038b8d40d
home_assistant/custom_components/solar_analytics/coordinator.py   aba19f4d2a17f601e65b6dc9fc1c3fd69a1eab7cc3ae630d79969c6db4defc6b
home_assistant/deployment/energy_split_rest_native_block.yaml     5b0a393bcdfc07528511c26f6578031959f8c238c94bf5bb6083c562910ca93d
tests/test_analytics.py                                           6f55c7a03a412393cec3adc6b6c7095425441d6fd060f6a53d5c16304ea82887
README.md                                                         fed02f58c3c469582340325c3767078d4db88c334cad2f3e7d89f55ad2f9e8a1
```

## Limitation matrix

| Area | Current result | Impact | Exit condition |
|---|---|---|---|
| Forecast.Solar timestamps | **Resolved:** `watt_hours_period` uses previous timestamp → key timestamp (period-end) | Timestamp normalization is understood, but profile analytics remain blocked by producer provenance/full-model completeness | Implement a producer that stamps verifiable URL/model provenance, or keep profile metrics disabled |
| Forecast.Solar model alignment | **Capacity/geometry derived, full alignment unverified:** shared REST producer uses `/5.36`, but public response does not echo requested URL and omits native inverter/damping | Existing shared sensor is live; Solar Analytics does not treat it as fully native-aligned and does not accept snapshots | Use a provenance-stamping custom producer or an equivalent independently verifiable response contract |
| Local provenance follow-up | **Implemented, tested, and live fail-closed:** SHA-256 model/request/payload contract plus consumer-owned two-refresh barrier; stock REST/companion bridge is rejected | Live gate remains closed because expected URL construction or a response payload match cannot prove actual producer/full-model equivalence | Implement and verify an owned producer with actual request capture, or formally retain the blocker |
| VRM | scalar-only | No detailed hourly VRM comparison/heatmap | Existing HA integration must expose a real timestamped hourly array |
| Historical accuracy | valid coverage `0`; no current profile snapshots | Bias/WAPE/realization/recommendations remain insufficient | Accumulate valid, aligned, uncurtailed intervals and completed-day snapshots |
| External control context | optional `external_control` signal unavailable in current component context | Conservative classification; possible underperformance is not asserted | Expose a verified limitation signal if available |
| Browser rendering | not screenshot-verified | Route/resources are verified, visual layout is not | Open HA in a browser and capture desktop/mobile views |
| Backup restore | backup created, restore test not run | Rollback artifact exists but restore procedure is not independently exercised | Test restore on a disposable copy or approved maintenance window |

## Rollback locations

Primary rollback for the original Solar Analytics deployment:

```text
/config/backups/solar_analytics_live_20260802T185804Z
```

Native-REST change rollback:

```text
/config/backups/solar_analytics_native_rest_20260802T201856Z
```

Safety fail-closed correction rollback:

```text
/config/backups/solar_analytics_safety_20260802T203953Z
```

Additional rollback points inside that directory include:

```text
pre_profile_guard/
pre_sensor_presentation_guard.py
profile_guard_bundle/
sensor_presentation_guard.py
MANIFEST.json
```

No credentials or secrets are included in this report.
