# Native-derived REST resources for Home Assistant providers

Use this reference when a YAML REST entity must retain its identity while its request URL follows a native integration's config entry. The concrete source contract below was verified against Home Assistant Core `2026.7.4`, the `rest` integration in that release, and `forecast-solar==5.0.1`.

## Core safety rule

An **expected** URL is not evidence that an existing REST payload was fetched from that URL. Never mark a payload model-aligned merely because code can derive the intended resource. A hardcoded old request, setup fallback, restored attributes, delayed poll, or failed package reload can still supply the payload.

Keep these concepts separate:

1. native model contract;
2. expected REST resource;
3. producer provenance for the response actually consumed;
4. response freshness and availability.

If provenance is unavailable, keep model-dependent analytics blocked.

## HA REST `resource_template` lifecycle

On HA `2026.7.4`:

- `create_rest_data_from_config()` renders `resource_template` during REST setup and requires a non-empty resource;
- the REST coordinator or polling entity renders it again immediately before each REST update;
- the template does **not** install an entity-state dependency listener and does not refresh immediately when a referenced sensor changes;
- therefore a source change can lag by the source coordinator interval plus the REST polling interval unless a separately approved non-physical refresh is triggered;
- configure an explicit provider-rate-limit-safe `scan_interval`; do not inherit the generic REST default accidentally.

A referenced custom entity may not exist when the first setup render occurs. The bootstrap branch must be syntactically valid and fail closed. Do not use an old valid forecast model as fallback. Prefer a fixed-host sentinel that returns no usable period map, pair it with an availability template, and require consumers to reject unavailable/missing/stale results. Remember that an unavailable HA state can retain old attributes; consumers must inspect state availability before parsing attributes.

Validate a URL attribute before using it in Jinja: require a string with the exact allowlisted HTTPS prefix, otherwise render the sentinel. Never allow an entity attribute to choose an arbitrary host.

## Forecast.Solar native config mapping

For HA `2026.7.4`:

- latitude and longitude are in `entry.data`;
- plane data are config subentries of type `plane`;
- use `entry.get_subentries_of_type("plane")`, not the first arbitrary item in `entry.subentries`;
- module power is W and becomes path kWp via `/ 1000`;
- native azimuth is `0..360`; the API path uses `azimuth - 180`;
- inverter size is optional, stored in W, and becomes query kW via `/ 1000`;
- morning/evening damping are forecast-shaping options;
- an API key changes authentication/account behavior and must never appear in entity state, attributes, diagnostics, Recorder, or logs.

For the smallest public single-plane adapter, require exactly one usable Forecast.Solar entry, exactly one `plane` subentry, and no configured API key. Fail closed for multiple planes or authenticated native models rather than silently dropping planes or publishing a secret URL.

Validate persisted values even though the native config flow normally constrains them:

- latitude: finite and `-90..90`;
- longitude: finite and `-180..180`;
- declination: finite and `0..90`;
- native azimuth: finite and `0..360`;
- module power: finite and strictly positive;
- optional inverter power: absent or finite and strictly positive;
- damping: finite and `0..1`.

A model fingerprint must cover every forecast-shaping, non-secret field: location, plane geometry, module power, optional inverter, damping, and public/authenticated mode. Do not fingerprint only capacity/tilt/azimuth and then call the whole model aligned.

For a one-plane public model, a full period resource has the shape:

```text
https://api.forecast.solar/estimate/watthours/period/<lat>/<lon>/<declination>/<azimuth-180>/<module_kWp>?time=iso8601&damping=0&damping_morning=<x>&damping_evening=<y>[&inverter=<kW>]
```

Query ordering is irrelevant. If the implementation intentionally synchronizes capacity only, report `capacity_aligned`, not `aligned_to_native`, and retain the broader model gate.

## Identity-preserving design

To retain an existing entity such as `sensor.forecast_solar_hourly_api`:

1. Keep the same REST platform/block, name, and exact `unique_id`.
2. Change only `resource` to `resource_template`; avoid moving the producer to another entity platform in the same slice.
3. Preserve the response attributes and state/unit semantics unless a read-only consumer inventory proves they are unused.
4. Verify the registry did not assign a suffixed entity ID such as `_2`.
5. Back up the shared package and record rollback before reload/restart.

A small diagnostic source entity can expose module power as its state plus minimal non-secret attributes: contract status, expected URL, and model fingerprint. Make it unavailable unless the complete supported contract is valid. Avoid publishing the whole config-entry record, entry IDs, nested source maps, or API keys; derived URLs also disclose coordinates and should be excluded from Recorder when practical.

## Provenance handshake

Forecast.Solar's public response does not echo module capacity, plane geometry, the request URL, or a provider-signed model fingerprint. Stock REST `json_attributes` can only copy fields already present in the response; it cannot synthesize a templated provenance attribute while leaving the numeric state unchanged. Consequently, substituting native capacity or the expected URL in the consumer is not verification.

Compare these designs explicitly:

1. **Fingerprint in the existing REST state.** A post-response state template can stamp an expected model/request fingerprint, but it replaces the numeric state. Reject this whenever consumers depend on the existing kWh state, unit/device class, statistics, or dashboard contract. A single matching stamp also has a pre-request/post-response race and does not bind the consumed `result` mapping.
2. **Same-fetch companion sensor and consumer-owned handshake.** This is the smallest identity-preserving option when deductive local provenance is acceptable. Add a diagnostic sensor as a sibling under the *same* `rest:` parent—not a separate Template entity or separate REST block. Core 2026.7.4 gives siblings the same `RestData`/coordinator and one HTTP response. The companion's state template should encode only bounded non-secret fields, for example:

   ```text
   v1|request_sha256|result_sha256|contract_generation|response_nonce
   ```

   - `request_sha256` hashes the canonical allowlisted full-model URL;
   - `result_sha256` hashes canonical `value_json.result` (`to_json(sort_keys=true)`); the consumer recomputes it from the original entity's `result` attribute;
   - `contract_generation` is a monotonic native-helper revision that changes even when a model is changed and then reverted;
   - `response_nonce` is created once during that post-response template render, so duplicate HA state writes cannot look like extra fetches.

   A plain companion that only republishes the expected fingerprint proves expectation, not production. The consumer—not the producer token—must compute the stable-refresh count. Reset on startup/reload, native generation/request change, unavailable/stale state, malformed token, report-pair mismatch, or payload-digest mismatch. The first valid distinct response after reset is warm-up only. Require a second distinct response with the same request hash and contract generation. Core's serialized coordinator means request 2 starts after response 1; this closes the in-flight old-request/new-stamp race. Use `last_reported` for unchanged successful source reports, pair source and companion reports within a tight bound, and keep the latch non-sticky.
3. **Owned fetcher or local proxy.** An owned fetcher can atomically publish the immutable model snapshot, actual URL passed to the HTTP client, status, payload digest, and response ID. Replacing the REST entity is larger and requires explicit entity-registry migration to retain the old entity ID. A local proxy can retain the REST platform/unique ID and wrap the same response with provenance, but adds a service lifecycle, allowlisting, caching, startup, and provider-rate-limit contract.

The companion design proves local request provenance only under the reviewed static REST block and Core lifecycle. The owned design records the actual local request directly. Neither proves that Forecast.Solar internally honored the parameters; the public API provides no signed server attestation. Define `aligned_to_native` narrowly as a trusted local producer issuing the complete native-equivalent public request and atomically/deterministically binding the accepted response. If the product requires provider-signed computation proof, keep it blocked.

When no provenance-compatible design preserves existing consumer semantics, remain fail closed rather than weakening the mismatch guard. Never accept a producer-supplied `stable_refresh_count` or merely non-empty `response_id` as proof.

## Regression matrix

Pure contract tests:

- current native values produce the exact expected path and query;
- changing module power changes only the capacity component;
- HA azimuth boundaries map correctly;
- optional inverter absent/present behavior;
- damping included and bounded;
- missing, malformed, NaN, infinity, out-of-range, zero, and negative values reject;
- zero, one, and multiple plane subentries;
- unrelated subentry types ignored;
- API-key configured path rejects without exposing the key;
- model fingerprint changes for every model-shaping field;
- packaged and pure helper copies stay byte-identical or share one tested source.

HA/producer tests:

- startup with source entity absent uses no-data fallback and cannot authorize analytics;
- restored/stale REST attributes while state is unavailable are rejected;
- source and companion are siblings under one REST parent, one refresh makes exactly one provider GET, and the existing source state/result contract is unchanged;
- a standalone expected-fingerprint companion and an arbitrary self-reported `stable_refresh_count` never verify;
- source `result` and companion `result_sha256` mismatch blocks immediately;
- source model change during an in-flight request makes the first matching post-response token warm-up only;
- a second distinct nonce with the same request hash/generation verifies, while duplicate state writes with the same nonce do not advance the barrier;
- native change-and-revert, restart/reload, unavailable/stale reports, or unpaired source/companion `last_reported` times reset the latch;
- an unchanged successful response can advance through a new nonce/`last_reported` without relying on `last_updated`;
- failed request retains blocked status and no old attributes can keep alignment sticky;
- same REST `unique_id` retains the original entity ID and avoids `_2`;
- explicit scan interval respects provider rate limits;
- the staged shared-package YAML parses and contains the reviewed same-block companion/resource template, not a hand-edited variant;
- the real target HA package—not only mocks—proves render-before-fetch, post-response companion render, shared coordinator behavior, and serialized refreshes.

## Controlled verification

Local proof and live proof remain separate. Before live activation:

1. keep the shared package under a timestamped backup;
2. run pure tests, resource parsing, and a real-HA REST/entity smoke where available;
3. inventory consumers of both REST state and `result` attributes;
4. run `ha core check` before any approved restart;
5. after restart, verify the source contract, exact entity ID/unique ID, derived `/5.36`-style capacity, response timestamp, result shape, and blocked-to-aligned provenance transition;
6. verify the former hardcoded capacity is absent from active config and diagnostics;
7. make no physical service calls; REST refresh and state readback are non-physical but still belong to the approved live-verification scope;
8. roll back the package/component and restart if identity changes, the source stays unavailable, the old resource remains active, or analytics aligns before fresh provenance exists.
