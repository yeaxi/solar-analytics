"""Repair flows for Solar Analytics.

Solar Analytics surfaces user-actionable failure modes as Home Assistant
Repair issues. The coordinator :func:`_maintain_repair_issues` creates or
clears an issue every time the native binding status transitions. Two of the
failure modes (``canonical_actual_mismatch`` and ``binding_changed``) are
fixable by re-running the config flow's reconfigure step; the rest are
informational (non-fixable) and simply describe the situation so users know
what to do outside the integration (fix the Energy dashboard, upgrade HA,
restart the Forecast.Solar integration).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant

# Repair issue IDs raised by the coordinator when the native binding fails
# closed on the corresponding status. Each corresponds to a translation key
# under ``issues.<id>`` in ``strings.json`` / ``translations/*.json``.
FIXABLE_ISSUES = frozenset({"canonical_actual_mismatch", "binding_changed"})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Return the repair flow that fits ``issue_id``.

    Fixable issues use HA's ``ConfirmRepairFlow`` which shows the translated
    description and, on confirmation, closes the issue. The user is expected
    to open the integration's Reconfigure step afterwards; the flow itself
    does not attempt to open a config flow (Core does not currently support
    chaining a repair into a config flow reliably across all HA versions).
    Non-fixable issue IDs also route through ConfirmRepairFlow so the
    description is at least readable in the Repairs UI.
    """

    return ConfirmRepairFlow()
