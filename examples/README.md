# Examples

Optional companion artifacts for Solar Analytics. Nothing here is required
for the integration to run; every file is a starting point you can adapt to
your own dashboard, automations, or scripts.

## Files

- [`lovelace-example.yaml`](lovelace-example.yaml) — a minimal three-section
  Lovelace dashboard covering live status, current power, and coverage
  ratios. Uses only the entities Solar Analytics enables by default. Copy
  into a new dashboard (Settings → Dashboards → Add dashboard → from YAML)
  or paste individual cards into an existing dashboard.

If you have a Lovelace layout you would like to share back, PRs adding
files under this directory are welcome; please keep them installation-
agnostic (no per-house entity IDs beyond the ones Solar Analytics itself
registers) and add a short description here.
