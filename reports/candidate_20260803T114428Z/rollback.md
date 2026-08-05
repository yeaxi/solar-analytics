# Solar Analytics v2 candidate rollback runbook

Candidate bundle: `reports/candidate_20260803T114428Z/solar_analytics_v2_candidate.tar.gz`
Candidate manifest: `reports/candidate_20260803T114428Z/manifest.json`

## Scope

Restore only the Solar Analytics custom component and its dedicated package file. Do not modify Forecast.Solar configuration, Energy Dashboard storage, Victron configuration, unrelated packages, dashboards, or physical-control automations.

## Precondition

Before any live deployment, create a timestamped remote backup containing the pre-change bytes of:

- `/config/custom_components/solar_analytics/`
- `/config/packages/solar_analytics.yaml`
- `/config/solar_analytics/solar_analytics.sqlite*` if present
- relevant entity-registry/config-entry/dashboard records only if they are explicitly changed

Record SHA-256 hashes and keep the backup directory unchanged.

## Abort conditions

Stop before restart if any of the following occurs:

- candidate/live hash mismatch;
- `ha core check` failure;
- unexpected config-entry/entity/dashboard mutation;
- new Solar Analytics traceback or import error;
- SQLite integrity failure;
- any physical or notification service path is registered.

## File rollback

1. Stop further activation; do not call physical services.
2. Preserve the failed candidate directory and post-change logs.
3. Restore the backed-up component directory and package file atomically.
4. Remove only candidate-created files that are absent from the backup.
5. Run `ha core check`.
6. If the check passes, perform the separately approved controlled restart.
7. Poll HA readiness and re-read the exact Solar Analytics entities, config entry state, package hash, and SQLite integrity.
8. Leave analytics fail-closed if native provenance is unavailable.

## SQLite rollback boundary

Do not delete or reclassify legacy REST history as native history. If the v2 store fails integrity or schema checks, stop the integration, preserve the failed database/WAL/SHM files, restore the pre-change SQLite/WAL/SHM backup as a complete set, run integrity check, and verify HA startup. A database restore is scoped to Solar Analytics only.

## Final evidence required

Record:

- pre-change backup path and SHA-256 manifest;
- candidate and remote archive hashes;
- `ha core check` output before restart;
- readiness result after restart;
- post-rollback entity/config-entry states;
- SQLite integrity result;
- fresh log-window result;
- explicit physical service-call count (`0` for this read-only integration).
