# Solar Analytics project context (for AI agents and human contributors)

## Scope

Solar Analytics is a standalone, reusable, **read-only** Home Assistant custom integration. It is PV-only: it observes forecast and actual PV telemetry and produces analytics; it does not control boilers, heaters, accumulators, dehumidifiers, relays, batteries, or the grid.

Every user configures which sensors and which Forecast.Solar entry the integration binds to; there are no installation-specific hardcoded entity IDs, no hardcoded timezone, and no site-specific manufacturer labels in the shipping code.

## Authoritative sources

- Home Assistant native Forecast.Solar Energy binding (via `homeassistant.components.forecast_solar.energy.async_get_solar_forecast`) is the sole forecast-profile source.
- Actual PV telemetry comes from **user-selectable** entities in the config flow: an actual PV power sensor (`device_class=power`, unit W or kW) and an actual PV energy counter (`device_class=energy`, unit Wh or kWh). If the user leaves these blank, they are auto-detected from the Energy Dashboard's single solar source.
- The `custom_components/solar_analytics/native_adapter.py` module is the sole boundary that touches Home Assistant's Forecast.Solar internals. It must not silently substitute another source. A cached scalar is not a valid substitute for a timestamped forecast profile.

## Read-only boundary (non-negotiable)

While developing, testing, or validating this project, do not call Home Assistant services, reload config entries, restart Home Assistant, trigger refreshes on the native Forecast.Solar coordinator, call `estimate()`, or call provider HTTP endpoints. `tests/test_read_only_invariant.py` enforces this at the source-code level for the shipping integration; treat any addition of `hass.services.async_call`, `async_refresh()`, `estimate(`, `requests.*`, or `time.sleep` inside `custom_components/solar_analytics/` as a class-A regression.

## Evidence contract

Every historical or runtime claim about the shipping integration must state its source, timestamp/boundary, resolution, coverage, denominator, and uncertainty. Do not claim production readiness until native listener evidence, persistence evidence, restart/readiness evidence, migration checks, and a read-only soak are complete.

Use `PASS`, `FIX_REQUIRED`, `BLOCKED`, or `PARTIAL`; do not infer success from missing errors or an empty/null data structure.

## Read-only soak checkpoint

The soak checkpoint validator (`tools/pv_soak_checkpoint.py`) is a local deterministic analyzer. It performs no SSH, network, Home Assistant, provider, or SQLite access itself. Its allowlisted entity set is fixed (the Solar Analytics status/profile entities plus the two actual-PV entities the user configured; the tool takes the entity IDs from the collector envelope). A malformed, stale, incomplete, digest-mismatched, or non-zero-write envelope is `BLOCKED`; never repair it by collecting broader state or retrying a mutation.

The three stages of a soak run must stay separated:

1. **Collector** (outside this repo): gather only allowlisted evidence through an independently read-only SSH command set into a temporary JSON envelope; never include raw logs or unallowlisted entities.
2. **Snapshot**: `python tools/pv_soak_checkpoint.py snapshot --input <collector.json> --output-dir reports/soak_checkpoints`. The script validates the read-only contract, writes a content-addressed no-overwrite snapshot, and emits only its path/status.
3. **Analyzer**: `python tools/pv_soak_checkpoint.py analyze --snapshot <snapshot.json>`. Deliver only the bounded PASS/BLOCKED JSON result with blockers and physical-call counts.

## Implementation conventions

- Every config-flow field must have an inline UI description in `strings.json` explaining meaning, use, and runtime/safety effect. Every user-visible key must have translations in `translations/en.json`; `translations/uk.json` is our current baseline additional locale.
- Keep native-source provenance, profile admission, actual-source freshness, persistence, and presentation readiness as separate contracts. Do not collapse them into a single "healthy" flag.
- Version identifiers come from `manifest.json` at import time (see `const.py`). Do not add a second constant.
- Keep secrets, tokens, and connection strings out of reports, fixtures, logs, and committed files.
- Preserve a rollback artifact and exact hashes for every staged live-file change; live deployment remains a separate approval-gated workflow.

## Non-goals

- Anything that would make Solar Analytics installation-specific again (hardcoded entity IDs, hardcoded timezone, hardcoded manufacturer string, Ukrainian-only strings).
- Physical control of anything. This project has no `services.yaml`; introducing one is out of scope.
- Provider HTTP fallback paths. If the native Forecast.Solar contract cannot be observed, the integration fails closed. Do not paper over that with a second acquisition path.

## Project-local references

Detailed architecture references live under `docs/architecture/` and are published with the rest of the docs at https://yeaxi.github.io/solar-analytics/. This `AGENTS.md` is the short policy source; the reference copies do not authorize deployment or provider/config mutations. `README.md` is the user-facing document; `CONTRIBUTING.md` is the contributor-facing document.

## Cursor Cloud specific instructions

This project is a pure-Python package with **no runtime dependencies** and no
long-running service. There is nothing to "boot": development is entirely
`pytest` (with a stubbed Home Assistant, per `CONTRIBUTING.md`), `ruff`, `mypy`,
`compileall`, and the `tools/`/`scripts/` CLIs. No live Home Assistant, database,
or Forecast.Solar network access is used or permitted (see the read-only
boundary above).

Interpreter split (non-obvious): the integration targets **Python 3.14** (CI,
`ruff` `target-version`, and `mypy` `python_version` all pin 3.14), but the base
VM's system `python3` is 3.12. The startup update script provisions two
prebuilt virtualenvs so future agents do not need to install anything:

- `~/.venvs/solar-analytics` — Python 3.14, holds `requirements-dev.txt`
  (`pytest`, `ruff`, `mypy`). Use this for all lint/type/test/build checks.
- `~/.venvs/solar-analytics-docs` — Python 3.12, holds `requirements-docs.txt`
  (`mkdocs-material`). Docs CI runs on 3.12, not 3.14.

Run the checks documented in `CONTRIBUTING.md`, but invoke them through the
3.14 venv, e.g.:

```bash
~/.venvs/solar-analytics/bin/ruff check .
~/.venvs/solar-analytics/bin/ruff format --check .
~/.venvs/solar-analytics/bin/python -m mypy
~/.venvs/solar-analytics/bin/python -m compileall -q custom_components/solar_analytics tools scripts
~/.venvs/solar-analytics/bin/python -m pytest
```

Docs build (auxiliary CI gate) uses the 3.12 venv:

```bash
~/.venvs/solar-analytics-docs/bin/mkdocs build --strict
```

Gotchas:

- `mkdocs build --strict` prints a red, multi-line `mkdocs-material` banner
  advertising a future 2.0 release. That banner is advisory, **not** a build
  failure; the command still exits 0 and writes `site/` (which is gitignored).
- The soak checkpoint validator (`tools/pv_soak_checkpoint.py`) is a real,
  runnable CLI and the closest thing to an end-to-end "app": build a collector
  envelope from the `template` subcommand, `snapshot` it into an immutable
  content-addressed file, then `analyze` it. It never touches the network, HA,
  SSH, or SQLite itself (see the read-only soak checkpoint policy above).
