# Solar Analytics

Read-only Home Assistant custom integration. It compares your PV output with a solar forecast profile. The forecast can be any Home Assistant Energy dashboard solar-forecast integration (Forecast.Solar, Solcast, and similar) or a forecast entity you choose.

It does not control devices, send notifications, or call services.

## Requirements

- Home Assistant Core 2026.7 or newer
- A solar forecast source, one of:
  - An Energy dashboard solar-forecast integration (Forecast.Solar, Solcast, ...). With exactly one Energy dashboard solar source it is auto-detected.
  - A forecast entity whose attributes expose a timestamped Wh-per-period map (`wh_hours`, `wh_period`, or `watt_hours_period`).
- A PV power sensor (`device_class: power`, unit `W` or `kW`)
- A PV energy counter (`device_class: energy`, unit `Wh` or `kWh`)

## Install

### HACS

1. Add `https://github.com/yeaxi/solar-analytics` as a custom repository of type Integration.
2. Install Solar Analytics.
3. Restart Home Assistant.

### Manual copy

1. Copy `custom_components/solar_analytics/` into `config/custom_components/`.
   Alternatively, download `solar_analytics.zip` from the
   [GitHub Releases](https://github.com/yeaxi/solar-analytics/releases) page
   and unzip it into `config/custom_components/`.
2. Restart Home Assistant.

## Set up

Settings → Devices & Services → Add Integration → Solar Analytics.

Leave fields blank to auto-detect from the Energy dashboard. Change them later under Reconfigure.

| Field | Default | Use |
| --- | --- | --- |
| Forecast source type | Energy Dashboard integration | Observe an Energy dashboard forecast integration, or read a forecast entity |
| Actual PV power sensor | auto | Instantaneous PV power |
| Actual PV energy-today sensor | auto | Daily PV energy counter |
| Forecast config entry | auto | Which Energy dashboard forecast entry to observe (Forecast.Solar, Solcast, ...) |
| Forecast entity | — | The forecast entity to read (only for the "Forecast entity" source type) |
| Analytics timezone | Home Assistant timezone | Daily rollups and snapshot hours |
| Morning snapshot hour | 6 | Morning baseline, the day before |
| Day-ahead snapshot hour | 23 | Day-ahead diagnostic snapshot |

Changing the sensors or the forecast source starts a new accuracy history.

## What you get

One device with sensors that compare forecast and actual PV:

- Actual PV power and Forecast.Solar power
- Forecast, actual, and paired coverage
- Analysis status and Forecast.Solar source status
- Forecast accuracy after 14 valid paired days in 30 days
- Daily comparison, future profile, and performance heatmap (disabled by default; enable them in the entity registry)
- Imported actual history (disabled by default)
- Last updated timestamp
- Binary sensors: PV performance analysis valid, PV data-quality problem

## Imported actual history

Your PV sensors already recorded months of production before you installed
Solar Analytics. On setup the integration reads that history back out of Home
Assistant's long-term statistics for your actual PV **energy** sensor and
publishes it on `sensor.solar_analytics_imported_actual_history`, which is
disabled by default. Enable it in the entity registry to see one point per
local day: date, kWh, the fraction of that day's hours that were actually
recorded, and how many counter resets fell in it. Every row is labelled
`reconstructed_from_recorder_statistics`.

**This does not shorten the 14-day accuracy warm-up.** Accuracy compares a
forecast against an actual for the same day, and Home Assistant never saved
the historical hourly forecast anywhere, so there is nothing to compare your
imported production against. Imported days are production history only. They
never count toward a valid paired day, the rolling accuracy window, or the
accuracy sensor. Accuracy still starts counting from the day you installed
Solar Analytics.

The import is read-only. It asks the Recorder for one sensor's hourly totals
and writes nothing back to it. It refreshes when Home Assistant restarts or
the integration reloads, at most once per day, and re-running it never
double-counts. If the Recorder cannot be read the sensor says so
(`recorder_unavailable`) and the rest of the integration carries on.

## If it is not working

Check `sensor.solar_analytics_analysis_status`. Solar Analytics reports a status instead of guessing.

| Status | Meaning | What to do |
| --- | --- | --- |
| `ready` | Accuracy is available | Nothing |
| `insufficient_data` | Fewer than 14 valid paired days | Wait |
| `native_source_unavailable` | The forecast source has not produced a profile | Check that the forecast integration or entity is loaded |
| `native_source_stale` | Last forecast update is older than 2 hours | Check the forecast source |
| `unsupported_native_contract` | Home Assistant is too old, or the provider's forecast contract changed | Upgrade Home Assistant, or file a bug |
| `unsupported_forecast_entity_contract` | The chosen forecast entity exposes no timestamped Wh-per-period profile | Pick an entity with a `wh_hours`/`wh_period`/`watt_hours_period` attribute |
| `actual_source_stale` or `actual_source_unavailable` | PV sensor missing, unavailable, or older than 15 minutes | Fix the sensor |
| `binding_ambiguous` or `binding_unavailable` | Energy dashboard has zero or more than one solar source | Fix the Energy dashboard, or pick sensors in Reconfigure |
| `canonical_actual_mismatch` | Override sensors are wrong | Reconfigure with valid power and energy sensors |

The **PV data-quality problem** binary sensor is on when something is wrong.

Download diagnostics from Settings → Devices & Services → Solar Analytics → three-dot menu → Download diagnostics.

## Report a bug

Open a [bug report](https://github.com/yeaxi/solar-analytics/issues/new?template=bug_report.md). Attach the diagnostics JSON. Do not paste raw logs.

## Docs and license

Contributing and architecture: <https://yeaxi.github.io/solar-analytics/>

Apache-2.0. See [LICENSE](https://github.com/yeaxi/solar-analytics/blob/main/LICENSE).
