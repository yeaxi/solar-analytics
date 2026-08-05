"""Pure Forecast.Solar contract helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from math import isfinite
from typing import Any


def _finite(value: Any) -> float | None:
    """Return a finite float or None."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _format_number(value: float) -> str:
    """Format a Forecast.Solar path number without needless .0 suffixes."""

    if value.is_integer():
        return str(int(value))
    return format(value, ".10g")


def build_model_fingerprint(contract: Mapping[str, Any]) -> str | None:
    """Return a deterministic, non-secret fingerprint of the native model.

    The digest covers every model-shaping field that the adapter can observe,
    including fields that the public REST path cannot encode. It intentionally
    excludes API-key material; only the public/authenticated mode is included.
    A valid fingerprint is an identity for the expected contract, not proof that
    a separate REST producer fetched its payload from that contract.
    """

    if contract.get("status") != "ok":
        return None
    if build_forecast_solar_period_url(contract) is None:
        return None
    numeric_fields = (
        "latitude",
        "longitude",
        "declination",
        "azimuth",
        "modules_power_w",
    )
    numbers: dict[str, float] = {}
    for field in numeric_fields:
        value = _finite(contract.get(field))
        if value is None:
            return None
        numbers[field] = value
    optional_numbers: dict[str, float] = {}
    for field in ("inverter_size_w", "morning_damping", "evening_damping"):
        raw_value = contract.get(field)
        if raw_value is None:
            return None
        value = _finite(raw_value)
        if value is None:
            return None
        if field == "inverter_size_w" and value is not None and value <= 0.0:
            return None
        if field in {"morning_damping", "evening_damping"} and value is not None and not 0.0 <= value <= 1.0:
            return None
        optional_numbers[field] = value
    auth_mode = str(contract.get("auth_mode") or "public").strip().lower()
    if auth_mode not in {"public", "authenticated"}:
        return None
    canonical = {
        "schema": 1,
        **numbers,
        **optional_numbers,
        "plane_id": str(contract.get("plane_id") or ""),
        "auth_mode": auth_mode,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def build_payload_fingerprint(payload: Any) -> str | None:
    """Return a canonical non-secret SHA-256 fingerprint of a REST payload."""

    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def build_request_fingerprint(request_url: str | None) -> str | None:
    """Return a non-secret SHA-256 fingerprint of an exact request URL."""

    if not isinstance(request_url, str) or not request_url.startswith("https://"):
        return None
    return f'sha256:{hashlib.sha256(request_url.encode("utf-8")).hexdigest()}'


def evaluate_producer_provenance(
    expected_fingerprint: str | None,
    expected_url: str | None,
    observed: Mapping[str, Any] | None,
    *,
    source_available: bool,
    source_fresh: bool,
    observed_payload_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Verify one response from an explicitly owned Forecast.Solar fetcher.

    A stock HA ``rest`` entity is intentionally not an accepted producer: it
    does not expose the effective request URL or a response-bound model stamp.
    An owned fetcher must report the exact URL, URL digest, complete model
    fingerprint, canonical response digest, and a response generation. The
    caller must independently compute the payload digest from the live payload;
    the two-refresh admission barrier is handled by
    :func:`advance_producer_provenance_barrier`.
    """

    if not source_available:
        return {"verified": False, "status": "source_unavailable"}
    if not source_fresh:
        return {"verified": False, "status": "source_stale"}
    prefix = "https://api.forecast.solar/estimate/watthours/period/"
    if not isinstance(expected_url, str) or not expected_url.startswith(prefix):
        return {"verified": False, "status": "expected_url_invalid"}
    if not isinstance(expected_fingerprint, str) or not _is_model_fingerprint(expected_fingerprint):
        return {"verified": False, "status": "expected_fingerprint_invalid"}
    if not isinstance(observed, Mapping):
        return {"verified": False, "status": "producer_stamp_missing"}
    producer_type = observed.get("producer_type")
    if producer_type != "owned_fetcher":
        return {"verified": False, "status": "producer_type_unverified"}
    if observed.get("request_url") != expected_url:
        return {"verified": False, "status": "request_url_mismatch"}
    expected_request_fingerprint = build_request_fingerprint(expected_url)
    if observed.get("request_url_sha256") != expected_request_fingerprint:
        return {"verified": False, "status": "request_fingerprint_mismatch"}
    observed_fingerprint = observed.get("model_fingerprint")
    if not isinstance(observed_fingerprint, str) or not _is_model_fingerprint(observed_fingerprint):
        return {"verified": False, "status": "producer_fingerprint_missing"}
    if observed_fingerprint != expected_fingerprint:
        return {"verified": False, "status": "producer_fingerprint_mismatch"}
    payload_fingerprint = observed.get("payload_sha256")
    if not isinstance(payload_fingerprint, str) or not _is_sha256_fingerprint(payload_fingerprint):
        return {"verified": False, "status": "payload_fingerprint_missing"}
    if observed_payload_fingerprint != payload_fingerprint:
        return {"verified": False, "status": "payload_fingerprint_mismatch"}
    response_generation = observed.get("response_generation")
    if not isinstance(response_generation, str) or not response_generation.strip():
        return {"verified": False, "status": "response_generation_missing"}
    return {
        "verified": True,
        "status": "response_verified",
        "producer_type": producer_type,
        "model_fingerprint": expected_fingerprint,
        "request_url": expected_url,
        "request_url_sha256": expected_request_fingerprint,
        "response_generation": response_generation,
        "payload_sha256": payload_fingerprint,
    }


def advance_producer_provenance_barrier(
    previous: Mapping[str, Any] | None,
    response: Mapping[str, Any],
) -> dict[str, Any]:
    """Require two distinct consumer-observed response generations.

    The producer supplies only an opaque generation identity. The counter is
    owned here, resets on model/URL change, and never trusts a producer-supplied
    ``stable_refresh_count``. Reusing one generation with a different payload
    is rejected as a producer contract failure.
    """

    if not response.get("verified"):
        return {
            **dict(response),
            "verified": False,
            "stable_refresh_count": 0,
        }
    generation = response.get("response_generation")
    payload_fingerprint = response.get("payload_sha256")
    model_fingerprint = response.get("model_fingerprint")
    request_url = response.get("request_url")
    if not isinstance(generation, str) or not generation.strip():
        return {"verified": False, "status": "response_generation_missing", "stable_refresh_count": 0}
    prior = dict(previous or {})
    same_contract = (
        prior.get("model_fingerprint") == model_fingerprint
        and prior.get("request_url") == request_url
    )
    if not same_contract:
        count = 1
    elif generation == prior.get("last_response_generation"):
        if payload_fingerprint != prior.get("last_payload_sha256"):
            return {
                "verified": False,
                "status": "response_generation_reused",
                "stable_refresh_count": 0,
            }
        count = int(prior.get("stable_refresh_count") or 0)
    else:
        count = min(int(prior.get("stable_refresh_count") or 0) + 1, 2)
    return {
        **dict(response),
        "verified": count >= 2,
        "status": "verified" if count >= 2 else "refresh_barrier_pending",
        "stable_refresh_count": count,
        "last_response_generation": generation,
        "last_payload_sha256": payload_fingerprint,
    }


def _is_model_fingerprint(value: str) -> bool:
    """Validate the public shape of a SHA-256 model fingerprint."""

    digest = value.removeprefix("sha256:")
    return (
        value.startswith("sha256:")
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )


def _is_sha256_fingerprint(value: str) -> bool:
    """Validate the common prefixed SHA-256 digest shape."""

    return _is_model_fingerprint(value)


def build_forecast_solar_period_url(contract: Mapping[str, Any]) -> str | None:
    """Build a capacity/geometry-only public Forecast.Solar period URL.

    Home Assistant stores azimuth in a 0..360 north-based convention while the
    Forecast.Solar API uses -180..180 with 0=south. The native HA integration
    applies ``azimuth - 180``; this helper mirrors that conversion. The public
    URL does not carry all native shaping inputs (notably inverter and damping),
    so callers must not label its response as a complete native-model match.
    """

    if contract.get("status") != "ok":
        return None
    latitude = _finite(contract.get("latitude"))
    longitude = _finite(contract.get("longitude"))
    declination = _finite(contract.get("declination"))
    azimuth = _finite(contract.get("azimuth"))
    modules_power_w = _finite(contract.get("modules_power_w"))
    if (
        latitude is None
        or not -90.0 <= latitude <= 90.0
        or longitude is None
        or not -180.0 <= longitude <= 180.0
        or declination is None
        or not 0.0 <= declination <= 90.0
        or azimuth is None
        or not 0.0 <= azimuth <= 360.0
        or modules_power_w is None
        or modules_power_w <= 0.0
    ):
        return None
    raw_inverter_size_w = contract.get("inverter_size_w")
    inverter_size_w = _finite(raw_inverter_size_w)
    if raw_inverter_size_w is not None and (inverter_size_w is None or inverter_size_w <= 0.0):
        return None
    for damping_key in ("morning_damping", "evening_damping"):
        raw_damping = contract.get(damping_key)
        damping = _finite(raw_damping)
        if raw_damping is not None and (damping is None or not 0.0 <= damping <= 1.0):
            return None
    kwp = modules_power_w / 1000.0
    api_azimuth = azimuth - 180.0
    return (
        "https://api.forecast.solar/estimate/watthours/period/"
        f"{_format_number(latitude)}/{_format_number(longitude)}/"
        f"{_format_number(declination)}/{_format_number(api_azimuth)}/"
        f"{_format_number(kwp)}?time=iso8601"
    )
