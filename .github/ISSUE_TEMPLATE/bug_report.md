---
name: Bug report
about: Report a defect in Solar Analytics
title: "bug: <short description>"
labels: bug
---

## Environment
- Home Assistant Core version:
- Solar Analytics version (`manifest.json` → `version`):
- Install method: HACS / manual copy / other
- Python version (if manual install):

## What happened
A clear, concise description of the incorrect behavior.

## What did you expect
What Solar Analytics should have done, and where that is documented (README section, entity name, docstring).

## Reproduction
Minimal steps to reproduce. Include the config-flow choices you made, the state of the four sensors:
- `sensor.solar_analytics_native_forecast_solar_source_status`
- `sensor.solar_analytics_analysis_status`
- `sensor.solar_analytics_solar_forecast_accuracy`
- `sensor.solar_analytics_last_updated`

## Diagnostics
Attach the JSON downloaded from the integration's "Download diagnostics" button (Settings → Devices & Services → Solar Analytics → three dots → Download diagnostics). Do not paste raw Home Assistant logs into the issue body; attach them as files if needed.

## Read-only invariant
Solar Analytics is a read-only integration. If the defect involves Solar Analytics writing state, calling services, or otherwise mutating the system, mention that explicitly — it is a class-A regression.
