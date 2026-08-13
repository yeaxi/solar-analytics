# Contributing to Solar Analytics

Thank you for taking a look. Solar Analytics is a small, focused custom
integration; contributions that align with its scope are very welcome, and
contributions that expand its scope are welcome only after a design
discussion in an issue first.

User-facing install and bug-report steps live in
[README.md](https://github.com/yeaxi/solar-analytics/blob/main/README.md).
The published docs site is
<https://yeaxi.github.io/solar-analytics/>.

## Scope guardrails

Before opening a PR, please confirm the change fits inside the project's
non-negotiable invariants (also documented in
[`AGENTS.md`](https://github.com/yeaxi/solar-analytics/blob/main/AGENTS.md)):

- **Read-only.** No `hass.services.async_call`, no `runtime.async_refresh()`
  on other integrations, no writes to the recorder, no HTTP to any provider
  outside of Home Assistant's own native Forecast.Solar path.
- **PV-only.** No boiler, heater, dehumidifier, accumulator, battery, or
  grid state anywhere in the code. This is a solar-forecast-vs-actual
  analytics integration, nothing else.
- **Fail-closed.** Missing, stale, non-numeric, non-finite, duplicate,
  gapped, reset, DST-ambiguous, and incomplete data must produce an
  explicit status enum, never a silently-coerced zero.
- **Bounded persistence.** New tables/columns must be additive and version
  checked. Result-row counts must not grow unboundedly with refresh cadence.

Two automated tests (`tests/test_read_only_invariant.py`) grep the shipping
source for forbidden patterns and will fail your PR if the invariants slip.

## Development setup

Home Assistant 2026.7 (the minimum this integration supports) runs on
Python 3.14. Use 3.14 locally so the same interpreter CI uses is the one
you test with.

```bash
git clone https://github.com/yeaxi/solar-analytics.git
cd solar-analytics
python3 -m pip install --user -r requirements-dev.txt
```

Run the local checks:

```bash
ruff check .
ruff format --check .
mypy
python3 -m compileall -q custom_components/solar_analytics tools scripts
python3 -m pytest -q
```

All five should pass before you push. CI runs the same commands on Python
3.14.

## Docs site

Preview the published docs locally:

```bash
python3 -m pip install --user -r requirements-docs.txt
mkdocs serve
```

`mkdocs build --strict` must pass before you push. CI runs it on every
pull request and publishes <https://yeaxi.github.io/solar-analytics/>
from `main`.

README.md, CONTRIBUTING.md, and AGENTS.md stay the source files. The
MkDocs pages include them. Architecture papers live under
`docs/architecture/`.

## Repository layout

- `custom_components/solar_analytics/` — the shipping
  integration. This is the sole source of truth for every module.
- `tests/` — deterministic hermetic tests. No `pytest-homeassistant-custom-component`
  dependency; the HA stack is stubbed at import time when needed.
- `tools/` — local read-only analyzers (soak checkpoint validator).
- `scripts/` — local read-only checks. `verify_import_idempotency.py` feeds a
  synthetic year of hourly statistics through the real import three times and
  fails if the row count or the total kWh moves.
- `docs/` — MkDocs source. Architecture papers are in `docs/architecture/`.

## Coding conventions

- Use `from __future__ import annotations` at the top of every module.
- Type-hint everything; `ruff check` runs pyupgrade rules and will nudge
  older syntax to modern.
- Prefer `dataclass(frozen=True, kw_only=True)` for value objects.
- Every user-visible config-flow field must have an inline description in
  `strings.json` explaining its meaning and any lineage effect.
- Add a `translations/en.json` and `translations/uk.json` entry for every
  new user-visible key. Other languages are welcome but not required.

## Commit style

- One logical change per commit; keep unrelated refactors in separate
  commits so a bisect stays useful.
- Write the imperative-mood one-liner subject followed by a body that
  explains the *why*, not just the *what*.

## Pull request checklist

The pull request template covers this, but for quick reference:

- Tests pass locally (`ruff check`, `ruff format --check`, `mypy`,
  `compileall`, `pytest`).
- `mkdocs build --strict` is clean if you touched docs.
- `CHANGELOG.md` has an entry under `## [Unreleased]`.
- `README.md` and `translations/*.json` updated if the change is
  user-facing.
- Read-only and PV-only invariants confirmed in the PR description.

## Reporting bugs

Open a bug report using the template under
`.github/ISSUE_TEMPLATE/bug_report.md`. Please attach the diagnostics JSON
downloaded from **Settings → Devices & Services → Solar Analytics →
three-dot menu → Download diagnostics** instead of pasting raw logs.

## Repository-owner one-time setup

Two HACS validators can only be satisfied by settings on the GitHub
repository itself, not by files in the tree. The owner needs to set them
once:

- **Description.** GitHub → repository → About → set to something like
  "Read-only, reusable Solar Analytics custom integration for Home
  Assistant".
- **Topics.** GitHub → repository → About → topics → add at least
  `home-assistant`, `hacs`, `custom-component`, `integration`,
  `solar`, `forecast-solar`.
- **GitHub Pages.** Settings → Pages → Build and deployment → Source:
  GitHub Actions. Needed once so the docs workflow can publish
  <https://yeaxi.github.io/solar-analytics/>.

Equivalent commands if you prefer the CLI:

```bash
gh repo edit yeaxi/solar-analytics \
  --description "Read-only, reusable Solar Analytics custom integration for Home Assistant" \
  --add-topic home-assistant,hacs,custom-component,integration,solar,forecast-solar
```

Pages source must be set in the GitHub UI. These are one-time
GitHub-side settings; they do not require a code change after the first
run.
