# Native Forecast.Solar live-compatibility reference

Use this reference when a read-only Home Assistant analytics integration observes the native Forecast.Solar runtime used by Energy Dashboard.

## Runtime contract

1. Resolve exactly one Energy Dashboard solar source and persist its exact Forecast.Solar config-entry ID only after validation.
2. Keep the adapter read-only and use the native energy-platform callable only.
3. Treat `entry.runtime_data is None` as a transient unavailable state. Retry idempotent listener attachment on later normal analytics reads; do not initiate a refresh.
4. Fail closed until an attached listener observes `runtime.last_update_success is True`; record an adapter-local observation timestamp and enforce freshness against it.
5. A failure callback must clear successful freshness rather than stamping an observation unconditionally.

## Runtime replacement

The integration must track the identity of the native runtime object. When Home Assistant replaces it, detach the listener from the old object, attach exactly one listener to the new object, and read the new runtime before admitting a profile. Unload must detach the current listener and clear all runtime references.

A lifecycle test must cover:

- initial unavailable runtime;
- first successful runtime capture;
- runtime replacement;
- old-listener cleanup;
- new-listener attachment;
- failure transition;
- unload cleanup.

## Entity and registry safety

Existing entity IDs and unique IDs are separate contracts. Before changing an entity platform:

- inspect `/config/.storage/core.entity_registry` read-only;
- preserve the existing unique ID when continuity is required;
- use a suggested object ID only when needed to retain a dashboard object ID;
- never substitute a short unique ID with a longer entity/object ID without an exact registry check.

Translations belong under `custom_components/<domain>/translations/`. Remove obsolete service schemas when the integration exposes no services.

## Deployment evidence

Before an approved live change, capture a timestamped backup and SHA-256 manifest for the component, relevant registries, configuration, and Solar Analytics SQLite files. Run `ha core check`, promote only the verified artifact, and verify HTTP readiness, integration setup, entity availability, targeted logs, and SQLite integrity. Do not claim readiness from source tests alone.

During a read-only soak, do not restart, reload, or manually refresh merely to accelerate an observation window. A new boundary starts only after the last approved Home Assistant lifecycle change and the next natural native callback.
