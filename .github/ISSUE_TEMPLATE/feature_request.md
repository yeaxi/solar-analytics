---
name: Feature request
about: Suggest a Solar Analytics improvement
title: "feat: <short description>"
labels: enhancement
---

## Problem
What are you trying to do that Solar Analytics does not currently support?

## Proposed change
What would you like the integration to do differently? Cite the file/entity/config-flow step you would touch, if you have looked at the code.

## Non-goals
Solar Analytics is deliberately PV-only and read-only:
- No control of physical devices (boilers, heaters, batteries, relays, grid).
- No calls to Home Assistant services.
- No provider HTTP requests outside of Home Assistant's native Forecast.Solar path.

If your request would violate any of these, please open a discussion first rather than a feature request.

## Alternatives considered
Other ways to solve the same problem, and why you prefer this one.
