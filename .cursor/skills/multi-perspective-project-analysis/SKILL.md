---
name: multi-perspective-project-analysis
description: Produce a detailed, evidence-based project analysis from five distinct identities — Product Owner, Engineer, Architect, QA, and Home Assistant Core team member — covering what's wrong, what could be done differently, fundamental issues, and what's good and should be left alone. Use when the user asks for a project analysis, review, audit, critique, or "look at the project from multiple angles / hats / perspectives / personas," or when they explicitly name any of those roles.
---
# Multi-Perspective Project Analysis

Use this skill when the user asks for a broad review of a project (or a well-scoped part of one) and wants opinions from multiple stakeholder roles rather than a single narrow answer. The default lineup is Product Owner, Engineer, Architect, QA, and Home Assistant Core team member. Swap the last role for another domain expert (e.g. "Django core", "Kubernetes SIG", "React core", "Rails core") only when the project clearly is not a Home Assistant integration and the user has not asked for the HA lens.

## Prime directive

Give the user a document they can act on. Every claim must be traceable to something in the repository — a file, a line range, a config value, a test, a dependency, a commit, an issue. If a section would only contain generic advice ("consider improving observability"), either make it concrete or delete it. Balanced honesty beats performative criticism: real strengths get named as clearly as real problems.

## Workflow

Follow these steps in order. Do not skip step 1; the quality of the analysis is bounded by how well you understand the project first.

### 1. Establish scope and ground truth

Before opening any persona, know what you're looking at.

- Confirm what "the project" means: the whole repo, a subpackage, a service, a Home Assistant integration under `custom_components/<domain>/`, etc. If ambiguous, pick the most inclusive reasonable scope and state it explicitly at the top of the report.
- Read the entry points and structural signals: `README`, `pyproject.toml` / `package.json` / `Cargo.toml`, `manifest.json` (for HA), `.github/workflows/`, `Dockerfile`, top-level `src/` or package layout, test directory layout, `CHANGELOG`, `docs/`.
- Skim the actual code, not just metadata. For a Home Assistant integration, at minimum read: `manifest.json`, `__init__.py`, `config_flow.py`, `coordinator.py` (or equivalent), one or two platform files (`sensor.py`, `switch.py`, …), `const.py`, `strings.json` / `translations/en.json`, and the test suite layout.
- Note versions of language, framework, and key dependencies. Note the CI matrix.
- Record anything that materially changes the analysis (monorepo vs single package, license, minimum supported runtime, target user, deployment model).

If the repository is not available (no checkout, missing files), say so explicitly and analyze only what is available. Do not fabricate structure.

Plan the work with a short todo list when the project is non-trivial. One todo per persona plus one for the synthesis is usually right.

### 2. Run each persona pass

For every persona, produce the same four subsections in this exact order:

1. **What's wrong** — concrete, cited defects and pain points visible today.
2. **What could be done differently** — realistic alternatives, with a one-line tradeoff each. Not a wishlist; each item should be something the persona would actually push for.
3. **Fundamental issues** — root causes and structural problems that generate the surface defects above. Distinguish these from cosmetic issues.
4. **What's good — leave it alone** — genuine strengths that changing would cost more than it gains. This section must exist and be non-empty unless the project is truly broken end-to-end; in that case, say so.

Cite specific paths, symbols, or line ranges for every non-trivial claim. Use the code-reference format (`startLine:endLine:filepath`) for existing code and inline backticks for file/symbol names in prose. If a claim is an inference rather than a fact, mark it ("appears to", "likely", "not confirmed").

Do not repeat the same finding across personas. If two personas would flag the same thing, put it under whichever persona owns it most naturally and reference it briefly from the other ("QA also cares about this — see Engineer §2").

#### 2a. Product Owner

Focus on user and product value, not code taste.

- Who is the user, and what job does the project do for them? Is that clear from the README, screenshots, and docs?
- Scope discipline: is the feature set coherent, or is the project drifting? What features are missing that the target user would expect on day one?
- Onboarding friction: install path, configuration, first successful run, error messages a normal user actually sees.
- Release hygiene: versioning scheme, changelog quality, cadence, deprecation warnings, upgrade notes.
- Feedback loop: issue templates, response latency in issues/PRs, discussions/forum presence, telemetry (or lack of it) — is the maintainer learning from users?
- Prioritization: does the current backlog / roadmap match user impact, or is it driven by whoever shouts loudest?
- Documentation for humans, not just for other developers.

#### 2b. Engineer

Focus on day-to-day code quality and developer experience.

- Readability, naming, function length, module cohesion, dead code, TODO/FIXME density.
- Language idiom fit (Pythonic Python, idiomatic TypeScript, etc.). Type hints where appropriate; strict typing readiness.
- Test suite: does it exist, does it run, is it fast, is it deterministic, does it actually cover the important paths, or is it mostly happy-path smoke tests?
- Dependencies: pinned or floating; abandoned or actively maintained; unused entries; obvious security or licensing red flags.
- Dev experience: how long from `git clone` to a green test run and a working dev loop? Are there scripts (`Makefile`, `tox.ini`, `pre-commit`) that make the right thing easy?
- Error handling and logging: is failure legible, or does the program eat exceptions?
- Refactor hotspots: files/classes that keep appearing in every bug fix or that are obviously fighting the design.

#### 2c. Architect

Focus on structure, boundaries, and evolution.

- Module boundaries: what depends on what, and where are the cycles or leaky abstractions?
- Data flow and state ownership: who owns which piece of state, and where does it mutate?
- Extensibility model: plugins, hooks, config, subclassing — is the extension surface intentional or accidental?
- Concurrency and I/O model: sync vs async discipline, blocking calls in async paths, thread safety, resource cleanup.
- Cross-cutting concerns: configuration, secrets, logging, metrics, feature flags, error propagation — done consistently or reinvented per module?
- Performance and scale characteristics under realistic (not peak-marketing) load.
- Long-term evolution: what does this codebase look like at 3× its current size, and where will it break first?

#### 2d. QA

Focus on defect prevention, defect detection, and diagnosability.

- Test pyramid shape: unit vs integration vs end-to-end. Is any layer missing or over-invested?
- Coverage of failure modes: network errors, partial data, auth expiry, rate limits, malformed input, concurrent access, upgrade/downgrade paths.
- Flakiness sources: time, ordering, external services, filesystem, ports. Is there fixture/mocking discipline?
- Regression prevention: are bug fixes accompanied by tests that would have caught them?
- Manual test burden: what does a release actually require a human to click through? Is that documented?
- Observability in production: logs, metrics, traces, diagnostics endpoints — can a support person figure out what happened without shelling in?
- CI signal quality: are failures actionable, or is red the normal color of `main`?

#### 2e. Home Assistant Core team member (default fifth persona)

Focus on fit with Home Assistant's architecture, quality standards, and long-term maintainability inside the ecosystem. Swap this persona for another domain expert only when the project is clearly not a Home Assistant integration.

Check, at minimum:

- `manifest.json` correctness: `domain`, `name`, `codeowners`, `dependencies`, `requirements` (pinned), `iot_class`, `integration_type`, `config_flow: true` when applicable, `version` for custom integrations, `documentation` and `issue_tracker` URLs.
- Config flow: proper `config_entries.ConfigFlow`, unique IDs, reauth flow, reconfigure flow, options flow, discovery flows (DHCP/Zeroconf/SSDP/Bluetooth/USB) when relevant.
- Runtime setup: `async_setup_entry` / `async_unload_entry` symmetry, `runtime_data` usage on modern HA, no side effects at import time.
- Coordinator pattern: `DataUpdateCoordinator` (or push-based equivalent) instead of per-entity polling; update interval sanity; error handling with `UpdateFailed`; first refresh strategy.
- Entities: `unique_id` everywhere, `DeviceInfo` with stable identifiers, `EntityDescription` usage, correct `device_class` / `state_class` / `native_unit_of_measurement`, `entity_category` for diagnostics/config, availability logic, `_attr_has_entity_name = True`.
- Async discipline: no blocking I/O in the event loop, correct use of `hass.async_add_executor_job`, `aiohttp` session from `async_get_clientsession`, no bare `requests`, no `time.sleep`.
- Translations and strings: `strings.json` present, entity translation keys, no user-facing English hardcoded in Python.
- Diagnostics (`diagnostics.py`) and repairs (`repairs.py`) when the integration has failure modes users can hit.
- Services: registered with voluptuous schemas and translations; typed responses when appropriate.
- Deprecation and breaking changes: issue registry entries, clean migration paths for config entries (`async_migrate_entry`).
- Quality Scale alignment: which tier (bronze/silver/gold/platinum) does this actually meet, and what is the smallest set of changes to reach the next one?
- Type hints and `strict-typing` readiness; `hassfest` and `ruff` clean.
- Tests using `pytest-homeassistant-custom-component` (for custom integrations) or the core test helpers; snapshot tests where the ecosystem expects them; no network in tests.

Flag things the HA Core team would actually block a PR on ("import at module top pulls network", "sync HTTP in event loop", "no `unique_id` on entities") separately from stylistic preferences.

### 3. Synthesize

After the five persona passes, produce a short synthesis section that is worth reading on its own:

- **Cross-cutting themes**: two to five patterns that showed up in more than one persona. This is where root causes live.
- **Prioritized action list**: an ordered list of the highest-leverage changes. For each item give: what to do, why it matters, rough effort ("small / medium / large / invasive"), and which persona(s) it satisfies. Do not estimate calendar time.
- **Do-not-touch list**: things that are already good and would cost more to change than to keep. Be explicit; this protects the user from well-meaning future rewrites.
- **Open questions**: things you could not determine from the available material and that the user should answer before acting on the report.

### 4. Deliver

Structure the final response with clear headings so the user can jump to any persona directly:

    # Project analysis: <project name or scope>

    ## 0. Scope & ground truth
    ## 1. Product Owner
    ### 1.1 What's wrong
    ### 1.2 What could be done differently
    ### 1.3 Fundamental issues
    ### 1.4 What's good — leave it alone
    ## 2. Engineer
    ...
    ## 5. Home Assistant Core team member
    ...
    ## 6. Synthesis
    ### 6.1 Cross-cutting themes
    ### 6.2 Prioritized actions
    ### 6.3 Do not touch
    ### 6.4 Open questions

Keep prose tight. Bullets over paragraphs. Every non-obvious claim gets a citation.

## Ground rules

- **Evidence over vibes.** No claim without a file, path, symbol, config value, or test to point at. If you cannot cite it, either go find the evidence or drop the claim.
- **Concrete alternatives.** "Consider improving X" is not an alternative. "Replace the per-entity polling in `sensor.py` with a `DataUpdateCoordinator` shared across platforms" is.
- **Distinguish fact from inference.** Use "is" for things you verified in the code, "appears to" / "likely" for reasonable inferences, and mark unknowns as unknown.
- **Distinguish opinion from standard.** A HA Core team member blocking on "sync HTTP in the event loop" is a standard. Preferring `attrs` over `dataclasses` is an opinion. Label opinions as such.
- **No fabricated features.** Do not describe code, tests, or docs that do not exist. If the tests directory is empty, say so; do not invent coverage numbers.
- **Balanced.** The "what's good" section is mandatory unless the project is genuinely broken end-to-end. Do not manufacture criticism to look thorough, and do not manufacture praise to look kind.
- **No calendar estimates.** Characterize effort structurally (small / medium / large / invasive, which subsystems change, what risks apply) rather than in days or weeks.
- **Respect the maintainer.** Address root causes, not the person. Assume prior decisions had reasons even when you disagree with the result.
- **Prioritize.** A report with 80 undifferentiated findings is not actionable. If you produce many findings, the synthesis section must rank them.

## When to decline or narrow

- If the project is huge and the user has not scoped the request, pick a defensible scope (e.g. "the `foo` integration in `custom_components/foo/`") and state it, rather than skimming everything shallowly.
- If the repository is not available in this session, deliver whatever partial analysis the available material supports and clearly mark the rest as blocked on access.
- If the user asked for only some of the personas ("just the architect and QA passes"), run only those and skip the synthesis' cross-cutting section if it would be thin — do not pad.
