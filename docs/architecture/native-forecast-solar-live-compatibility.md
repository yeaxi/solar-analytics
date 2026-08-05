# Native Forecast.Solar live-compatibility reference

Use this reference when a read-only Home Assistant analytics integration observes native Forecast.Solar rather than owning provider acquisition.

## Pinned Core 2026.7.4 facts

Authoritative source excerpts verified against the `2026.7.4` Core tree:

- `homeassistant.components.forecast_solar.energy.async_get_solar_forecast(hass, config_entry_id)` is an `async def` with exactly two parameters.
- It returns `None` unless the selected config entry exists and `entry.runtime_data` is a `ForecastSolarDataUpdateCoordinator`.
- On success it returns `{"wh_hours": {timestamp.isoformat(): value, ...}}`; zero-valued midnight entries may be omitted by Core.
- Core does not densify this series. `forecast-solar==5.0.1` copies `result["watt_hours_period"]` into `Estimate.wh_period`, and the Core helper forwards those period ends. The native series can therefore contain a sparse overnight cell: a non-midnight period end with explicit `0 Wh` may be many hours after the prior sunset point. Do not blanket-reject every interval over two hours. Treat a finite explicit zero-energy long cell as valid zero energy (which fabricates neither energy nor power), while retaining fail-closed rejection for long positive-energy gaps and malformed values.
- `forecast_solar.async_setup_entry` awaits `coordinator.async_config_entry_first_refresh()` and only then assigns the coordinator to `entry.runtime_data`; a non-null runtime therefore proves at least one successful setup refresh in that loaded-entry lifetime, not that periodic refreshes are continuing.
- Core version constants live in `homeassistant.const.__version__`; `homeassistant.__init__` is not the version source.
- `ForecastSolarDataUpdateCoordinator` subclasses plain `DataUpdateCoordinator`. Plain `DataUpdateCoordinator` initializes `last_update_success=True` but does **not** define `last_update_success_time`; that timestamp exists only on `TimestampDataUpdateCoordinator`.

## Disposable harness import pitfall

The `homeassistant` wheel does not declare every built-in integration's manifest requirements as distribution dependencies. In Core `2026.7.4`, `homeassistant/components/forecast_solar/manifest.json` pins `forecast-solar==5.0.1`; importing `homeassistant.components.forecast_solar.energy` first executes the package and coordinator imports, which raise `ModuleNotFoundError: No module named 'forecast_solar'` when that manifest requirement has not been processed.

Therefore, a bare-venv result such as `helper_imported=false` is not evidence of a missing helper or wrong signature until the underlying exception is captured. Have Home Assistant's loader process the Forecast.Solar requirements, or install the exact manifest pin in the disposable harness, then rerun the import/signature gate. Do not change the helper path or add direct provider acquisition to work around a harness-only missing requirement.

## Adapter rules

1. Resolve exactly one Energy Dashboard solar source and persist its exact Forecast.Solar config-entry ID only after validation.
2. Keep the adapter read-only: never use provider REST/HTTP, `estimate()`, or native `async_request_refresh()`.
3. Treat first-start `entry.runtime_data is None` as a transient `native_source_unavailable` result. Retry idempotent listener attachment on later normal analytics reads; do not initiate a native refresh.
4. Fail closed until an attached native `async_add_listener()` callback observes `runtime.last_update_success is True`, then record an adapter-local observation timestamp and enforce freshness against it. A listener callback may also report a failure transition, so do not stamp successful observation time unconditionally.
5. Guard Core version using `homeassistant.const.__version__`, then guard helper import/signature/payload/runtime shape and locally observed freshness independently.
6. Expose exact fail-closed reasons (`helper_import_or_signature`, `entry_unloaded`, `native_update_not_observed`, `last_update_not_successful`, etc.) in diagnostics and tests.
7. Do not infer provider attestation or provider acquisition time that the native helper does not expose.
8. Treat the removal callback returned by `runtime.async_add_listener()` as one-shot. If both adapter-owned shutdown and config-entry unload can reach cleanup, route both through the same idempotent wrapper.

## Source-parity gate before proposing a lifecycle fix

Do not infer that a listener retry is absent from a fail-closed runtime symptom alone. Before proposing or releasing a retry-only fix, compare the active component, the local source, and the relevant release artifacts:

1. read the deployed manifest version and hash the deployed adapter read-only;
2. count and locate every listener-attachment call in the active source;
3. compare adapter and coordinator hashes across the last-known-good and current candidates;
4. run the focused delayed-runtime test with bytecode and test-cache writes disabled.

If the deployed adapter already retries attachment on normal reads and is byte-identical to the last-known-good version, a restart-dependent outcome is a timing/lifecycle observation, not evidence of a removed retry. Do not ship a no-op patch. First produce a new RED test that models an uncovered lifecycle transition.

A native sensor update timestamp is not, by itself, proof that the analytics listener was attached before that update. Core assigns Forecast.Solar `entry.runtime_data` only after its first refresh; both a later analytics-cycle attachment and a config-entry `LOADED` callback can therefore miss the setup refresh. Continue to fail closed until a later naturally observed coordinator callback. Never retroactively admit retained payload or force a refresh.

## Runtime-identity replacement seam

An idempotence guard based only on a non-null listener remover is insufficient if the observed config entry can replace `runtime_data` while the analytics entry remains loaded. The adapter can remain subscribed to runtime A while normal reads consume runtime B, causing either permanent `native_update_not_observed` or, if old timestamp evidence is retained, incorrect admission of B's payload.

When independent native reloads are in scope, make the normal-read attachment check runtime-aware:

- remember the exact runtime object that owns the current listener;
- same runtime plus an active remover is a no-op;
- a different runtime removes A exactly once, clears A's local callback timestamp and observation evidence, and attaches B exactly once;
- associate callbacks with their owning runtime and ignore them after that runtime is no longer current;
- use the existing analytics coordinator read cycle as the retry trigger—no sleep loop, dedicated timer, provider HTTP, or `async_request_refresh()`;
- use one stable adapter-owned cleanup path so explicit unload and config-entry unload cannot double-remove or accumulate active listeners.

Treat runtime replacement as a falsifiable hypothesis, not an assumed live root cause. The focused RED test is:

1. attach runtime A and, optionally, observe one successful A callback;
2. replace `native_entry.runtime_data` with runtime B without unloading the analytics entry;
3. execute one normal analytics read;
4. assert A was removed once, B has exactly one listener, and B remains fail-closed with `sequence=None` before its own callback;
5. emit one successful B callback and assert `status=ok` with the next sequence;
6. run explicit adapter unload followed by saved config-entry unload callbacks and assert every underlying remover ran exactly once.

Keep the existing delayed-setup regression separately: initialize with `runtime_data=None` or a non-callable listener surface, make a valid runtime available, execute a later normal read, and require exactly one attachment. That test is RED only for a genuinely missing retry; it is not evidence for runtime replacement.

## DataUpdateCoordinator listener-unload trap

On Core `2026.7.4`, `ConfigEntry.async_unload()` awaits the integration's `async_unload_entry()` first and calls `_async_process_on_unload()` only after that function returns `True`. Meanwhile, `DataUpdateCoordinator.async_add_listener()` returns a callback bound to one numeric listener ID, and its internal remover uses `self._listeners.pop(listener_id)` without a default.

This creates a deterministic double-remove failure when an integration does both of the following:

1. registers the raw coordinator remover with `entry.async_on_unload(remover)`; and
2. manually calls the same remover from its own `async_unload_entry()` path.

The manual call removes the listener first; Core then invokes the still-stored unload callback and raises `KeyError(listener_id)`. Assigning the adapter field to `None` after the manual call does not alter the callback already retained by the config entry. The numeric listener ID is coordinator-local and is unrelated to application observation sequence numbers.

Wrap the one-shot callback at the ownership boundary and register/use only that wrapper:

```python
remove_listener = runtime.async_add_listener(listener)
removed = False

def remove_once() -> None:
    nonlocal removed
    if removed:
        return
    removed = True  # set before delegation for reentrancy safety
    remove_listener()

self._native_listener_remove = remove_once
entry.async_on_unload(remove_once)
```

Keep any explicit adapter shutdown call, but have it invoke `remove_once`. Do not broadly catch `KeyError`, patch Home Assistant internals, or assume the coordinator's returned callback is itself idempotent.

The minimal regression test must exercise an **attached** native listener, not a fail-closed `binding_unavailable` setup. Model the coordinator with a keyed listener dictionary whose remover calls `pop(listener_id)`, then:

1. initialize/attach the adapter;
2. invoke adapter-owned unload;
3. invoke the saved config-entry unload callback;
4. assert no exception, an empty listener dictionary, and exactly one underlying removal.

A lifecycle smoke that never reaches listener attachment cannot detect this bug.

## Entity-registry compatibility

Existing entity IDs and unique IDs are separate contracts. Before changing an entity platform:

- inspect `/config/.storage/core.entity_registry` read-only;
- preserve the existing `unique_id` so Home Assistant updates the same entity;
- use a compatibility `suggested_object_id` only when needed to retain a dashboard object ID;
- never replace a short existing unique ID with the longer entity/object ID, because that can create `_2` entities.

## Resource and migration checks

- Put custom-component translations under `custom_components/<domain>/translations/`, not a project-level `translations/` directory.
- Remove an obsolete `services.yaml` when the v2 integration registers no services; leaving stale schemas can preserve a retired service contract.
- Keep legacy REST compatibility artifacts in the inventory until soak and consumer migration are complete; do not remove them early just because the new component is read-only.
- Before a restart, read all relevant relay states and measured power. Do not issue compensating physical calls merely to make a restart convenient.

## TDD probe

Use a fake HA module layout that mirrors Core: define `homeassistant.const.__version__` and leave `homeassistant.__version__` absent. The test must fail against an adapter that reads the root package, then pass after the adapter reads `homeassistant.const`.

## Evidence pattern

Record, without credentials or tokens:

- pinned Core version and helper signature;
- selected native entry ID;
- pre/post candidate hashes;
- exact fail-closed state and timestamp before/after one normal coordinator cycle;
- entity-registry unique-ID/object-ID mapping;
- `ha core check`, readiness, logs, SQLite integrity, and physical-call count.
