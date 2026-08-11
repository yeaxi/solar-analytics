## Summary

Brief description of what this PR changes.

## Scope

- Files or subsystems touched:
- Read-only invariant preserved (no new `hass.services.async_call`, no `runtime.async_refresh()`, no provider HTTP): yes / no + why
- PV-only invariant preserved (no boiler / heater / accumulator / battery / grid state): yes / no + why

## Verification

- `python -m pytest -q` output:
- `ruff check .` clean: yes / no
- `python -m compileall -q home_assistant/custom_components/solar_analytics tools scripts` clean: yes / no
- Manual verification steps (if any):

## Docs

- README updated: yes / no / n/a
- CHANGELOG entry added: yes / no / n/a
- Translations updated (`strings.json` + `translations/*.json`): yes / no / n/a

## Screenshots

Attach relevant Lovelace or config-flow screenshots if the PR changes user-facing behavior.
