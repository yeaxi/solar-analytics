"""The single place Solar Analytics reads Home Assistant's Recorder.

Read-only by construction. This module calls exactly one Recorder API,
``statistics_during_period``, for exactly one statistic id, dispatched on the
Recorder's own executor. It never opens the live ``home-assistant_v2.db``
itself: a second connection from the integration process is a WAL and locking
hazard and bypasses Home Assistant's session management. It never calls a
Recorder write API such as ``async_import_statistics`` or
``async_add_external_statistics``.

Long-term ``statistics`` is the only place months of actual PV history live.
``statistics_short_term`` is purged at the default 10-day retention, which is
below the 14 paired days the accuracy metric needs.
"""

from __future__ import annotations

import functools
import importlib
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_hourly_energy_statistics(
    hass: HomeAssistant,
    *,
    statistic_id: str,
    start_utc: datetime,
    end_utc: datetime,
) -> list[Mapping[str, Any]] | None:
    """Return hourly cumulative ``sum`` rows in kWh, or ``None`` if unreadable.

    ``None`` means the Recorder could not be asked at all and is reported as
    such; an empty list means it was asked and holds nothing for this entity.
    """

    try:
        recorder = importlib.import_module("homeassistant.components.recorder")
        statistics = importlib.import_module("homeassistant.components.recorder.statistics")
        instance = recorder.get_instance(hass)
        result = await instance.async_add_executor_job(
            functools.partial(
                statistics.statistics_during_period,
                hass,
                start_utc,
                end_utc,
                {statistic_id},
                "hour",
                {"energy": "kWh"},
                {"sum"},
            )
        )
    except Exception as err:
        _LOGGER.debug(
            "Solar Analytics could not read recorder statistics for %s: %s",
            statistic_id,
            type(err).__name__,
        )
        return None
    rows = result.get(statistic_id) if isinstance(result, Mapping) else None
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []
