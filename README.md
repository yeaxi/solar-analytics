# Solar Analytics

Solar Analytics is a **read-only Home Assistant custom integration** that turns your existing Forecast.Solar Energy Dashboard binding and your own PV telemetry into a bounded set of accuracy, coverage, and diagnostic entities. It never controls devices, never sends notifications, and never calls services.

## What you get

Once configured, Solar Analytics adds one device with a small set of well-typed sensors and binary sensors, all under the `sensor.solar_analytics_*` and `binary_sensor.*` namespaces:

- **Actual PV power** and a **Forecast.Solar power** number so you can compare current output against the forecast in real time.
- **Forecast coverage**, **actual coverage**, and **paired coverage** diagnostic sensors that quantify how much of a day's data was usable.
- **Analysis status** and **native Forecast.Solar source status** enum sensors that always explain in plain language why the integration is or isn't producing analytics (`ready`, `insufficient_data`, `native_source_unavailable`, `native_source_stale`, `binding_ambiguous`, and so on — each state is translated for `en` and `uk` today).
- A rolling **forecast accuracy** entity that flips to `ready` after 14 valid paired days in a 30-day window.
- **Daily comparison**, **future profile**, and **performance heatmap** attribute-heavy sensors intended for dashboard cards (three of these are disabled by default; enable them from the entity registry when you want them).
- A **last updated** timestamp sensor and a **lineage** identifier for debugging.
- Two live binary sensors: **PV performance analysis valid** and **PV data-quality problem**. Four neutral binary sensors are included but disabled by default; they only exist so that pre-existing dashboards do not break during upgrade.

Solar Analytics is deliberately narrow. It does not manufacture underperformance / curtailment / storm-follow-up claims from missing data, does not open network sockets to any provider, and does not register services.

## Requirements

- Home Assistant Core **2026.7** or newer (the integration feature-detects patch-level changes within the supported minor line).
- The Home Assistant **Energy dashboard** is set up with **exactly one** solar source (any Forecast.Solar config entry is fine).
- Any PV **power sensor** with `device_class: power` (unit `W` or `kW`) and any PV **energy counter** with `device_class: energy` (unit `Wh` or `kWh`, `state_class: total` or `total_increasing`).

You can override the auto-detected sensors in the config flow if you want to point Solar Analytics at a specific inverter/plant instead of whatever the Energy dashboard is using.

## Installation

### HACS (custom repository)

1. In HACS, add this repository (`https://github.com/yeaxi/solar-analytics`) as a **custom repository** of type **Integration**.
2. Install "Solar Analytics" from the HACS list.
3. Restart Home Assistant.

### Manual copy

1. Copy `custom_components/solar_analytics/` into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

## Configuration

Add the integration from **Settings → Devices & Services → Add Integration → Solar Analytics**.

The config flow has one screen with these fields. All fields are optional; leave any of them blank to auto-detect from the Energy dashboard.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| Actual PV power sensor | entity selector (`sensor.*`, `device_class=power`) | auto | The instantaneous PV power sensor Solar Analytics compares against the forecast. |
| Actual PV energy-today sensor | entity selector (`sensor.*`, `device_class=energy`) | auto | The daily PV energy counter used for reconciliation and daily comparison. |
| Forecast.Solar config entry | config-entry selector (`forecast_solar`) | auto | Which Forecast.Solar entry to observe. |
| Analytics timezone | text (IANA name) | `hass.config.time_zone` | Timezone the morning and day-ahead snapshot boundaries and daily rollups use. |
| Morning snapshot hour | number (0-23) | `6` | Local hour at which the morning-baseline snapshot is taken the day before the target day. |
| Day-ahead snapshot hour | number (0-23) | `23` | Local hour at which the day-ahead diagnostic snapshot is taken. |

Every field has an inline description in the UI explaining what it does and what changing it means for the analytics lineage. You can change any field later from **Settings → Devices & Services → Solar Analytics → Reconfigure**. Changing sensor or Forecast.Solar-entry selections starts a new lineage; the previous lineage's accuracy history stays associated with the old lineage.

## Failure modes and what they mean

Solar Analytics fails closed: if it can't produce a trustworthy analytic, it says so instead of guessing.

| `analysis_status` state | What happened | What to do |
| --- | --- | --- |
| `ready` | 14+ valid paired days accumulated; accuracy is available. | Nothing. |
| `insufficient_data` | Native and actual data are fine but you haven't hit 14 valid paired days yet. | Wait; check `daily_comparison` to see what days are counting. |
| `native_source_unavailable` | The Forecast.Solar coordinator has not fired since HA started, or has been down. | Confirm your Forecast.Solar integration is loaded and produces `wh_hours`. |
| `native_source_stale` | The last observed native update is older than 2 hours. | Investigate the Forecast.Solar integration; check its logs. |
| `unsupported_native_contract` | HA version is below the supported minimum, or the Forecast.Solar internal shape changed. | Upgrade HA or file a Solar Analytics issue. |
| `actual_source_stale` / `actual_source_unavailable` | Your PV power or energy sensor is missing, unavailable, or older than 15 minutes. | Fix the sensor; Solar Analytics will resume. |
| `binding_ambiguous` / `binding_unavailable` | The Energy dashboard has zero or more than one solar source, or the source has zero or more than one Forecast.Solar entry. | Fix the Energy dashboard, or specify the sensors/entry explicitly in Reconfigure. |
| `canonical_actual_mismatch` | Your override sensors are missing or wrong. | Reconfigure with valid `device_class` sensors. |

Enable the `PV analysis data-quality problem` binary sensor for a single-line "something is wrong" signal you can automate on.

## Diagnostics

**Settings → Devices & Services → Solar Analytics → three-dot menu → Download diagnostics** returns a JSON snapshot containing the coordinator payload, native binding, and (redacted) config-entry contents. Attach this file to any GitHub issue instead of pasting logs.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Development

```bash
python3 -m pytest -q                                                    # unit tests
python3 -m compileall -q custom_components/solar_analytics tools scripts
ruff check .
ruff format --check .
```

The test suite is deterministic, hermetic (no network, no HA install required), and runs in well under one second. `.github/workflows/tests.yml` runs the same commands on Python 3.11 and 3.12 for every push and pull request.

## Project layout

```
custom_components/solar_analytics/   # the integration (source of truth)
tests/                                              # deterministic local tests
tools/                                              # local read-only analyzers (soak checkpoint)
scripts/                                            # local read-only report scripts
docs/architecture/                                  # design references
```

## License

Apache-2.0. See [LICENSE](LICENSE).
