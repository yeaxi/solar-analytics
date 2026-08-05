# Autonomous Goal Set — Solar Analytics v2

Виконай проєкт автономно, без проміжних питань, використовуючи ці authoritative files:

1. Specification: `/Users/rdudka/solar_analytics/.hermes/plans/2026-08-03-solar-analytics-v2-specification.md`
2. Implementation plan: `/Users/rdudka/solar_analytics/.hermes/plans/2026-08-03-solar-analytics-v2-plan.md`
3. Detailed Goal: `/Users/rdudka/solar_analytics/.hermes/plans/2026-08-03-solar-analytics-v2-goal.md`
4. Repository: `/Users/rdudka/solar_analytics`
5. Existing evidence: `/Users/rdudka/solar_analytics/reports/live_verification_2026-08-02.md`

## Task

Реалізуй Solar Analytics v2 як standalone read-only Home Assistant custom integration:

- native Forecast.Solar Energy Dashboard contract і `wh_hours` — єдине authoritative forecast source;
- canonical actual PV: `sensor.garage_cerbo_gx_pv_energy` і `sensor.garage_cerbo_gx_pv_power`;
- full native horizon, fixed morning baseline, 10-year history, lineage, coverage, accuracy та diagnostics;
- fail-closed при будь-якій невизначеності;
- staged migration і подальше видалення legacy REST entity лише після виконання всіх migration gates.

## Operating rules

- Працюй українською в документації та звітах.
- Виконуй план послідовно, TDD/RED-GREEN, з реальними тестами й перевірками.
- Не вигадуй API, payload, provenance, hashes або live results.
- Після кожного значного етапу перевіряй код, тести, schema, docs і consistency.
- Оновлюй README/report реальними evidence, а не очікуваними результатами.
- Якщо є неоднозначність, обирай fail-closed поведінку й документуй blocker.
- Не розширюй scope на planner, energy automations, notifications, recommendations або physical control.

## Заборонено

- додавати в Solar Analytics planner, executor, notification automation або physical-control logic;
- Forecast.Solar config-entry mutation;
- REST/provider HTTP requests з Solar Analytics;
- native `async_request_refresh()`;
- notification або persistent-notification service calls;
- secrets, API keys, passwords, connection strings або credentials у коді/logs/docs;
- unqualified retroactive forecast backfill. Authorized historical backfill is permitted only through the amendment below: it must use explicit source/capture-mode provenance, a separate lineage, immutable new backfill records, and must never relabel or rewrite a scheduled native slot or legacy REST row as native history;
- silent source rebinding;
- structural changes до existing dashboards/Lovelace resources.

## Current execution authorization and ordering

## Historical backfill amendment — 2026-08-03

The user explicitly changed the execution requirement and authorized retroactive historical backfill. The implementation may ingest existing Home Assistant Recorder history into a separately identified `historical_backfill` capture mode, subject to these gates:

1. Canonical actual history must come only from `sensor.garage_cerbo_gx_pv_power` and `sensor.garage_cerbo_gx_pv_energy`, with Recorder timestamps, units, state classes, stale/gap handling, and counter reconciliation preserved.
2. Forecast history must be classified by provenance. A detailed native `wh_hours` profile is native-backfill only when its historical record contains an auditable native source identity/observation contract; scalar Forecast.Solar entities are not detailed profiles. Legacy REST history may be ingested only as `historical_legacy_rest` audit/backfill data and is never native-valid.
3. Backfill records receive a run ID, source kind, capture mode, source lineage, source timestamps, payload/row digest, and quality/exclusion reason. They never update `current_lineage_id`, never rewrite the five existing terminal snapshot slots, and never silently replace a missing morning baseline.
4. Backfill analytics are reported separately as retrospective/backfill diagnostics. They do not count toward the 72-hour native-only soak, native deployment gate, or native `accuracy_ready` status unless a later explicit amendment changes that rule with independent evidence.
5. Any SQLite schema/write requires local RED/GREEN tests, Recorder evidence, a pre-write SQLite/WAL backup and hash, migration/restore/integrity checks, a staged candidate, and post-write readback. No provider HTTP, refresh, notification, or physical call is allowed.

Користувач явно дозволив для цього execution run live `/config` writes, SQLite migration, controlled restart, entity-registry/dashboard-reference maintenance, permanent REST removal і необхідні bounded physical service calls. Цей дозвіл стосується лише виконання вже погодженого Solar Analytics проєкту; він не дозволяє додавати physical-control logic до інтеграції.

Виконуй ці дії **тільки останньою фазою**, після:

1. local implementation і RED/GREEN tests;
2. schema/backup/restore/rollback checks;
3. pinned real-HA compatibility gate;
4. read-only live deployment/readiness/log/SQLite verification;
5. quantified native soak;
6. consumer inventory та migration plan.

До останньої фази не виконуй physical service calls. У фінальній фазі кожен physical call має бути:

- мінімально необхідним для live verification або rollback;
- обмеженим точним target і коротким bounded scope;
- попередньо перевіреним через state/power/freshness readback;
- підтвердженим post-call readback;
- компенсованим `turn_off`/safe rollback, якщо тест щось увімкнув або стан став невідомим;
- зафіксованим у фінальному звіті з exact service, target, часом і результатом.

Не виконуй масових або непов’язаних фізичних дій. Якщо physical state/readback неочікуваний, stale або ambiguous — fail-closed, зупинись і збережи безпечний стан.

Не оголошуй проєкт завершеним без реального tool evidence. У фінальному звіті вкажи:

- змінені файли й SHA-256;
- тести та їхній фактичний output;
- native compatibility/source evidence;
- storage/lineage/coverage evidence;
- blockers і відкладені approval gates;
- які live/physical дії не виконувалися.
