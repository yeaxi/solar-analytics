# Solar Analytics Dashboard

**Стан live:** basic read-only deployment, native-derived shared Forecast.Solar REST producer, safety fail-closed guards, `ha core check`, controlled restart і post-restart verification пройдені. Period-end semantics і native capacity/geometry derivation підтверджені, але producer provenance/full-model equivalence не доведені; Solar Analytics profile metrics залишаються fail-closed. Повний evidence report: `reports/live_verification_2026-08-02.md`.

**Знімок live contract:** `2026-08-02T20:50:11Z`.

**Latest live continuation:** `2026-08-03T06:43:22Z` — corrected component, snapshot-admission quarantine, and guarded numeric REST startup fallback are deployed after scoped backups, `ha core check`, controlled restart, readiness, and read-only verification. No physical service call was executed.

**Local-only follow-up:** станом на `2026-08-03T06:44:19Z` pure contract layer має deterministic SHA-256 model/request/payload fingerprints, consumer-owned two-refresh barrier і explicit fail-closed producer handshake. The live component now includes the same snapshot quarantine and provenance gate; full native-model equivalence remains blocked.

## 1. Live inventory

### Actual PV / Victron

| Purpose | Entity | Live contract / note |
|---|---|---|
| Canonical actual power | `sensor.energy_solar_production_power` | `W`, `device_class: power`, `state_class: measurement`; attributes identify source `sensor.garage_cerbo_gx_pv_power`; live metadata says `connected_array_capacity_w: 5880` |
| Direct Cerbo fallback | `sensor.garage_cerbo_gx_pv_power` | `W`, power/measurement |
| Canonical daily total | `sensor.energy_pv_aggregate_today` | `kWh`, `device_class: energy`, `state_class: total_increasing`; has a daily reset and recent history |
| Cerbo lifetime energy | `sensor.garage_cerbo_gx_pv_energy` | `kWh`, `state_class: total`; not used as daily total |
| MPPT PV power | `sensor.smartsolar_mppt_ve_can_250_100_rev2_pv_yield_power` | available and diagnostic; aggregate power remains accounting source |
| MPPT daily yield | `sensor.smartsolar_mppt_ve_can_250_100_rev2_yield_today` | available; diagnostic only |
| MPPT voltage/current | `sensor.smartsolar_mppt_ve_can_250_100_rev2_pv_bus_voltage`, `..._pv_current` | available for diagnostic graph |
| MPPT state/mode | `sensor.smartsolar_mppt_ve_can_250_100_rev2_state`, `..._mppt_operation_mode` | available; current live state was `external_control` |
| MPPT error/off reason | `sensor.smartsolar_mppt_ve_can_250_100_rev2_error_code`, `..._device_off_reason` | error entity was `no_error` at inventory time |
| MPPT max today | `sensor.smartsolar_mppt_ve_can_250_100_rev2_max_power_today` | available |

The native Forecast.Solar config entry is the single model contract: read-only inspection returned `modules_power: 5360 W`, `inverter_size: 5190 W`, azimuth `138°`, declination `33°`, and morning/evening damping `0`. Official Forecast.Solar documentation confirms that `watt_hours_period` values are for the period from the previous timestamp to the timestamp in the key. The shared REST producer derives `/33/-42/5.36`, so capacity/geometry align; the public URL does not include all native shaping inputs and the response does not prove requested-model provenance.

### Forecast.Solar

| Purpose | Entity | Live contract |
|---|---|---|
| Period payload | `sensor.forecast_solar_hourly_api` | native-derived `resource_template`; live URL uses native `5.36 kWp`; values are Wh for the period from the previous timestamp to the timestamp in the key (period-end); direct API sum `55.927 kWh` matches live state; Solar Analytics provenance gate remains blocked |
| Today | `sensor.energy_production_today` | `kWh` |
| Today remaining | `sensor.energy_production_today_remaining` | `kWh` |
| Tomorrow | `sensor.energy_production_tomorrow` | `kWh` |
| Current power | `sensor.power_production_now` | `W` |
| Peak today/tomorrow | `sensor.power_highest_peak_time_today`, `sensor.power_highest_peak_time_tomorrow` | timestamp diagnostics |

The Forecast.Solar period payload currently has one Recorder state because the large `result` is stored in attributes. Period-end semantics are confirmed and the native-derived capacity/geometry URL is live, but normalization remains blocked by unverified producer provenance/full-model completeness; profile metrics do not consume the payload until that gate is satisfied.

### Local provenance contract (not deployed)

The local-only follow-up adds deterministic, non-secret provenance primitives in `forecast_contract.py`:

- `build_model_fingerprint()` covers latitude, longitude, declination, native azimuth, modules power, inverter size, morning/evening damping, exactly selected plane ID, and only `public`/`authenticated` mode;
- `build_request_fingerprint()` and `build_payload_fingerprint()` bind an owned producer observation to the exact URL and canonical `result` payload; API keys, tokens, passwords and connection strings are excluded;
- `evaluate_producer_provenance()` accepts only `producer_type: owned_fetcher`, exact request URL plus URL digest, matching full model fingerprint, matching locally computed payload digest, and a response generation; producer-supplied `stable_refresh_count` is not trusted;
- `advance_producer_provenance_barrier()` owns the counter in Solar Analytics, resets on model/URL change, rejects reused generations with changed payloads, and requires two distinct valid response generations;
- the stock `rest:` producer is intentionally not accepted: HA renders `resource_template` internally and exposes selected response attributes, but does not publish the effective request URL or a response-bound model stamp. A companion token generated from the same expected template would not be independent evidence;
- Forecast.Solar's public response documents `result`, metadata and ratelimit, but does not echo the request URL, inverter, damping or a provider-signed model fingerprint. Therefore full native-model equivalence remains formally blocked and `forecast_profile_analysis_allowed` remains `false`;
- Forecast.Solar profile snapshots are not persisted while this gate is false; VRM remains scalar-only. This is a local safety contract and blocker proof, not evidence of a deployed custom producer.

### Victron VRM Forecast

| Purpose | Entity | Live contract |
|---|---|---|
| Today | `sensor.victron_remote_monitoring_estimated_energy_production_today` | `kWh`, total scalar |
| Tomorrow | `sensor.victron_remote_monitoring_estimated_energy_production_tomorrow` | `kWh`, total scalar |
| Current/next hour | `..._estimated_energy_production_current_hour`, `..._next_hour` | `kWh` scalar |
| Peak today/tomorrow | `..._highest_peak_time_today`, `..._tomorrow` | timestamp scalar |

The existing HA VRM integration is the source. It does **not** expose an hourly array or a confidence interval in the inspected attributes. The candidate stores VRM daily snapshots plus known scalar current/next-hour values and marks `hourly_profile_status: unavailable_scalar_only`. It does not create a synthetic hourly curve and does not draw a confidence band.

### Limitation / weather context

| Purpose | Entity / source | Result |
|---|---|---|
| SOC | `sensor.cerbo_gx_dc_battery_charge` | `%`, live available |
| DVCC | `sensor.garage_cerbo_gx_dvcc_state` | enum, live value `forced_on` |
| Battery/AC diagnostics | `sensor.energy_battery_charge_power`, `sensor.energy_battery_discharge_power`, MultiPlus input/output, AC source | used when available; optional signals remain visible if unavailable |
| Weather | `weather.forecast_home` | current weather attributes include cloud coverage, wind speed, precipitation unit; no independent historical storm feed was confirmed |
| Sun | `sun.sun` | used for sunrise−60 snapshot when available |
| BMS charge-current/voltage limit | no verified live entity found | not inferred; validity remains conservative |
| Lightning/hail/Blitzortung | no verified live entity found | storm fields remain `null` unless weather entity exposes a condition |

## 2. VRM Forecast acquisition

No direct VRM API, MQTT, Node-RED or new provider connection is created. The coordinator reads the existing HA entities above. At every update it checks attributes for a real hourly array under `hourly`, `hourly_forecast`, `forecast`, `result` or `data`; only a non-empty numeric/timestamp array is accepted. The inspected live entity has no such array, so the result is explicitly scalar-only.

No Solcast integration, API, p10/p50/p90, Estimated Actual or dampening sensors are used by the candidate.

## 3. Four-layer architecture

```text
Existing HA raw entities + existing Forecast.Solar/VRM entities
        ↓
30-minute normalized actual/forecast intervals + immutable snapshots
        ↓
Pure deterministic analytics engine (no HA imports)
        ↓
HA sensors, binary validity/anomaly flags, ApexCharts, Plotly, bounded Hermes JSON
```

Hermes receives only `sensor.solar_analytics_insight_json` attributes/`hermes_json`; raw Recorder rows and high-frequency samples never go to Hermes.

## 4. Snapshot storage

File: `/config/solar_analytics/solar_analytics.sqlite`.

Tables:

- `forecast_snapshots`: unique key `(provider, target_date, snapshot_type)`; first insert wins;
- `interval_accumulators`: only time-weighted energy, covered seconds, last sample and quality—not raw high-frequency history;
- `intervals`: normalized 30-minute prepared records;
- `daily_results`: one compact row per local date;
- `analysis_results`: bounded structured insight history;
- `recommendations`: generated recommendation and optional manual `applied_at`;
- `storm_events`, `runtime_state`, `meta`.

Schedule:

- `day_ahead`: target next local date, first successful poll at `20:00 Europe/Kyiv`;
- `morning`: target current local date, sunrise−60 minutes; fixed configured fallback `05:00` only if sunrise is unavailable.

Retention defaults are 400 days for daily/snapshot data and 210 days for 30-minute interval/profile data (greater than required 12/6 months where the interval start is available). WAL + transaction-safe primary keys make restart retries idempotent.

## 5. Normalized schema

Each prepared interval contains:

```json
{
  "interval_start": "Europe/Kyiv ISO timestamp",
  "actual_power_average_w": 0,
  "actual_energy_kwh": 0,
  "forecast_solar_power_w": null,
  "forecast_solar_energy_kwh": null,
  "vrm_forecast_power_w": null,
  "vrm_forecast_energy_kwh": null,
  "consensus_expected_power_w": null,
  "consensus_expected_energy_kwh": null,
  "analysis_valid": false,
  "validity_reason": "forecast_unavailable",
  "curtailment_reason": null,
  "storm_context": null,
  "data_quality": "good",
  "coverage_ratio": 0.0
}
```

Actual samples are held time-weighted until the next sample and split at UTC 30-minute boundaries. UTC keys avoid DST collisions; local rendering uses `Europe/Kyiv`. Gaps above 15 minutes reduce coverage and never become a fabricated zero.

## 6. Formulas

- `interval_energy_kwh = average_power_w × 0.5 / 1000`.
- `Bias = sum(actual_valid_energy - forecast_energy) / sum(forecast_energy)`.
- `WAPE = sum(abs(actual_valid_energy - forecast_energy)) / sum(actual_valid_energy)`.
- `daily_error = (actual_daily - snapshot_daily) / snapshot_daily`.
- `peak_time_error_minutes = forecast_peak_timestamp - actual_peak_timestamp`.
- `peak_power_error_w = forecast_peak_w - actual_peak_w`.
- `forecast_solar_realization = actual_valid_energy / forecast_solar_valid_energy`.
- `vrm_realization = actual_valid_energy / vrm_forecast_valid_energy`.
- `consensus_expected = weight_solar × solar + weight_vrm × vrm`.
- `consensus_realization = actual_valid_energy / consensus_expected`.

Hourly MAPE near zero is deliberately not computed. WAPE/aggregate bias are the primary accuracy metrics for 7/14/30/90-day and all available valid history.

Consensus weights are equal until at least 14 valid days. After that, each available provider receives an inverse-WAPE score, normalized and bounded to `[0.2, 0.8]`; missing/stale providers are excluded from the effective denominator. A provider can never become 100% authoritative.

Baseline is the median consensus realization over the previous 30 valid, uncurtailed, non-storm days above the expected-energy threshold. Absolute actual/forecast level alone is never called degradation.

## 7. Validity mask

`binary_sensor.pv_performance_analysis_valid` is true only when actual, both forecasts, expected power and critical context are available and no exclusion is active. Reason codes are:

`valid_low_expected_power`, `battery_full`, `bms_charge_limit`, `dvcc_limit`, `export_limit`, `mppt_error`, `external_control`, `thermal_derating`, `clipping`, `sensor_unavailable`, `forecast_unavailable`, `unknown_curtailment`.

Curtailment and clipping are invalid for technical performance conclusions but are not PV faults. A near-zero anomaly requires expected >20% of configured 4920 W (984 W), actual <5% (246 W), valid uncurtailed data and at least 15 minutes.

Persistent/step-change/gradual/time-of-day rules are implemented in `analytics.py`; they require repeated valid days, provider agreement and historical baseline evidence. Storm context blocks an immediate fault conclusion and requires a subsequent valid high-expected day plus confirmation.

## 8. Forecast.Solar recommendations

Recommendations are review-only and are emitted only with at least 20 valid days, 100 relevant valid intervals, two comparable 14-day windows, ≥5% proposed change and VRM directional support. Supported logic covers morning/evening damping, plane capacity, azimuth and diagnostics for inverter size/clipping. Tilt is intentionally withheld until seasonal data exists.

Generated recommendations are stored by hash. Manual application can be journaled with:

```yaml
service: solar_analytics.record_recommendation_applied
data:
  recommendation_id: "..."
```

No Forecast.Solar parameter is changed automatically.

## 9. Lovelace

Prepared file: `home_assistant/lovelace.solar_analytics.json`.

It uses only:

- `type: custom:apexcharts-card` for 48-hour actual/future graph, 90-day daily graph and prepared trend series;
- `type: custom:plotly-graph` for 30-minute realization heatmap and Victron diagnostics;
- standard `heading`, `grid`, `entities`, `tile`, `markdown`, `conditional`-compatible structure.

Live HACS status at inventory time:

- ApexCharts Card files are installed and resources are registered: `/local/community/apexcharts/apexcharts.min.js` and `/local/community/apexcharts/apexcharts-card.js`;
- Plotly repository is known to HACS, but `/config/www/community/plotly-graph-card` and a Plotly resource were not present. Installing `dbuezas/lovelace-plotly-graph-card` is a live change and is pending explicit deploy approval.

The previous local solar comparison files contained Solcast placeholder cards and stale 2460 W/temporary template entities. The new dashboard migrates the useful Forecast.Solar/Victron cards conceptually but does not copy those placeholders. Existing Energy Dashboard and Energy Split Dashboard files are not edited.

## 10. Backfill report (read-only inventory)

Recorder database exists at `/config/home-assistant_v2.db` and was approximately 5.77 GB at inspection. Dynamic schema inspection found:

- actual aggregate power long-term statistics: 281 hourly rows from `2026-07-21T21:00:00Z`;
- actual aggregate power short-term statistics: 3021 rows from `2026-07-23T01:15:00Z`;
- direct Cerbo PV power statistics: 218 hourly rows from `2026-07-24T16:00:00Z`;
- aggregate daily energy long-term statistics: only 6 rows from `2026-08-01T21:00:00Z`;
- Forecast.Solar daily scalar states: 345 today / 338 tomorrow states, no long-term statistics;
- VRM daily/current/next scalar states: approximately 206 long-term rows and ~2462 short-term rows each, no hourly profile array;
- hourly Forecast.Solar payload history: one state with current/future `result` attributes;
- MPPT state/error and DVCC are recorder states but do not have numeric statistics.

The read-only utility `scripts/recorder_backfill_report.py` was executed against the live DB and the compact output is stored at `reports/backfill_report_2026-08-02.json`. It reconstructed four Forecast.Solar day-ahead scalar rows (targets 2026-07-27, 2026-07-29, 2026-07-31, 2026-08-03) and four VRM scalar rows for the same archived snapshot windows. It produced actual daily aggregate-power estimates for 2026-07-23 through 2026-08-02; 2026-08-02 is incomplete (67.4% 5-minute coverage) and is excluded from completed-day accuracy. No current forecast was applied to historical dates.

Therefore:

- a true historical day-ahead snapshot archive does not exist before Solar Analytics starts;
- archived daily scalar values can be classified as `true historical snapshot reconstructed from valid archived data` only when their timestamp is before the target day and near the 20:00 local snapshot window;
- current forecast values must never be applied retroactively to earlier days;
- intervals with no archived snapshot remain `missing`, not guessed;
- weather/lightning/hail historical context was not sufficient to claim a storm explanation.

The live candidate does not perform a destructive or broad Recorder backfill before approval. A narrow read-only backfill utility is available, but extended period normalization remains blocked until the Forecast.Solar source/target timestamp contract is independently verified.

## 11. Files in this candidate

- `solar_analytics/analytics.py` — pure normalization, formulas, validity, consensus, anomalies, recommendations, insights;
- `solar_analytics/forecast_contract.py` — native URL bounds, non-secret full model fingerprint and producer provenance verifier;
- `solar_analytics/storage.py` — SQLite schema and retention;
- `tests/test_analytics.py` — RED/GREEN tests;
- `home_assistant/custom_components/solar_analytics/` — config flow, translations, coordinator, entities, pure engine copy;
- `home_assistant/packages/solar_analytics.yaml` — default-off helpers and gated cooldown/dedup notifications;
- `home_assistant/lovelace.solar_analytics.json` — separate dashboard config;
- `README.md` — this contract and evidence report.

## 12. Expert architecture review

A focused GPT-5.6 Sol review returned:

- **Basic stage:** viable after explicit live-change approval, if it is presented as a read-only dashboard with current scalar data and latest snapshots only.
- **Extended stage:** `BLOCKED / FIX_REQUIRED` until the Forecast.Solar period timestamp contract, model-capacity policy and snapshot capture windows are frozen.

The review confirmed:

- `sensor.forecast_solar_hourly_api` uses `watthours/period`; values are Wh per irregular period;
- native Forecast.Solar is `5360 W` with `inverter_size: 5190 W`; the existing REST/canonical metadata is `5880 W`. User policy is native-authoritative: REST must derive from the native config, not become a second model or static value;
- VRM exposes daily/current/next-hour scalars but no verified interval array;
- the last 24-hour actual short-term history had only `227/288` expected 5-minute rows, including nine missing 30-minute buckets; missing data must remain `null`, never zero;
- new dashboard route `/solar-analytics` is free and existing dashboard/resource hashes must remain unchanged;
- legacy Solcast registry ghosts exist, but no active Solcast config entry or active-dashboard usage was found; exact allowlists remain mandatory.

The local candidate now discovers the native Forecast.Solar config entry on each coordinator refresh, requires exactly one `plane` subentry, validates geometry/capacity/damping bounds, exposes `sensor.solar_analytics_native_forecast_solar_module_power`, derives the shared REST URL, and uses official period-end normalization. The coordinator rejects unavailable/stale REST attributes and does not infer native provenance from a derived URL or fallback capacity. Local tests cover capacity URL changes, bounds, explicit `end`-period conversion, deterministic model fingerprints, explicit producer match/mismatch, missing/stale source, and fail-closed snapshot gates. The follow-up is local-only and has not been deployed; the previous live deployment was completed after scoped backups, staged copy, `ha core check`, controlled restart, startup refresh automation, readiness, direct API comparison, and post-restart verification.

## 13. Live gate result

The capacity/geometry-derived shared REST change is live and verified, but the expert safety review correctly identified that full native-model provenance is not observable from the public response. Solar Analytics therefore remains intentionally fail-closed. Rollback points:

```text
/config/backups/solar_analytics_native_rest_20260802T201856Z
/config/backups/solar_analytics_safety_20260802T203953Z
```

No physical service call, Forecast.Solar config-entry mutation, credential change, or existing dashboard file edit was performed. A future custom producer must stamp verifiable URL/model provenance (or an equivalent response contract) before profile analytics can be unblocked.

## 14. Cron checkpoint pipeline

The scheduled 72-hour soak uses a three-stage read-only boundary:

```text
read-only SSH collector → validated content-addressed snapshot → bounded analyzer
```

`tools/pv_soak_checkpoint.py` performs only local JSON validation, no-overwrite snapshot creation, digest verification, and PASS/BLOCKED analysis. It has no network, SSH, Home Assistant, provider, or SQLite access. The collector envelope is limited to the entities, log streams, and SQLite tables in `AGENTS.md`; non-zero physical/mutation/network-write counts, stale evidence, unknown values, malformed digests, or missing allowlisted fields produce `BLOCKED`.
