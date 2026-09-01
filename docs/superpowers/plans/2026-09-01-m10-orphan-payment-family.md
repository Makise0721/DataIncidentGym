# M10 Orphan-Payment Fault Family Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Do not delegate work unless the user explicitly authorizes delegation for that execution turn.

**Goal:** Add exactly the three orphan-payment scenarios promised by M10 and carry them through deterministic injection, private verification, both diagnosis strategies, deterministic evaluation, recovery, and the unchanged six-file artifact contract, bringing P1 from 10/17 to 13/17.

**Architecture:** Extend the existing P1 vertical slice in place. A closed `ORPHAN_PAYMENT_ROWS` mutation inserts only two frozen payment batches whose `order_id` values have no matching `raw_orders` row. The already declared `raw_payments.order_id_to_raw_orders_id` profile fact proves the current orphan condition; the already declared `raw_orders.order_count_by_day` history and watermark distinguish a settled-window permanent orphan from a normally late order. M10 adds no tool, ProfileSpec change, free query surface, raw-row evidence, case-ID controller dispatch, or second diagnosis path.

**Tech Stack:** Python 3.12, Pydantic v2, psycopg 3 SQL composition, dbt Core/PostgreSQL Jaffle Shop fixture, PydanticAI FunctionModel, pytest, Ruff, uv, PowerShell 7.

---

## 0. Approved scope and fixed decisions

### Planning baseline and M9 audit result

- This plan is written against local `master` at `49452c94992a88f9a0400da12a17da924057ebf2`.
- The M9 candidate range is exactly `443c4874e12b2c561b0dc4552ddfe3c9a7fe1f22...49452c94992a88f9a0400da12a17da924057ebf2`, containing seven focused commits.
- Independent Standards and Spec reviews found no M9 finding. Unit tests were independently re-run as `199 passed`; Ruff, lock validation, candidate diff checks, the exact 20-cell matrix collection, and the exact eight-item collect-only M7 smoke were also rechecked.
- The supplied M9 release evidence records the database as healthy with 113 payments. During this planning audit, a new read-only database connection timed out after three seconds. That is an entry-environment condition, not evidence of product drift; Task 0 requires a bounded read-only preflight before any M10 mutation and must not silently reset or rebuild merely to make the check green.
- `docs/requirements.md`, `AGENTS.md`, `decision.md`, the older untracked plans, and reports are user-owned workspace material. They remain unstaged and unmodified by M10 unless the user separately changes that boundary.
- This plan file may be included in the first authorized M10 implementation commit. Creating this plan does not authorize implementation, real-model calls, commits, push, cleanup, or deletion.

### Exact M10 scenario matrix

| case_id | role | injected data | dbt result | exact affected model set | decisive public evidence | expected terminal |
|---|---|---|---|---|---|---|
| `orphan_payment_record` | `DEV_CONFIRMABLE` | Insert `(114, 1000, credit_card, 1000)` while `raw_orders.id=1000` is absent | dbt succeeds with no failed or skipped nodes | `model.jaffle_shop.customers`, `model.jaffle_shop.orders`, `model.jaffle_shop.stg_payments` | one declared payment→order relationship violation plus order history whose watermark has advanced beyond the public settled window | `CONFIRMED` |
| `orphan_payment_coupon_a` | `TEST_CONFIRMABLE` | Insert three unique coupon payments `(114,1000,1700)`, `(115,1001,1800)`, `(116,1002,200)` from the same settled alert window, plus the nullable `source_batch_note` distractor | dbt succeeds with no failed or skipped nodes | same three downstream models | three declared relationship violations, coupon group count 16, downstream lineage, and an order watermark beyond the public settled window | `CONFIRMED` |
| `orphan_payment_coupon_b` | `TEST_INSUFFICIENT` | Byte-for-byte the same three payment rows, alert, and distractor as variant A | dbt succeeds with no failed or skipped nodes | same three downstream models | current orphan profile remains visible, but order history is blocked and the ingestion watermark is not otherwise observable | `INSUFFICIENT_EVIDENCE` |

For the table above, the compact coupon tuples mean `(payment_id, order_id, amount)` and all use `payment_method="coupon"`.

The test pair must share the exact same `IncidentBrief`, mutations, database state, `direct_failure=null`, affected assets, distractor, and dbt result. It may differ only in private accepted explanations, `raw_orders` history access, unresolved gaps, required public evidence types, answerability, and terminal status.

### Frozen rows and aggregate facts

The healthy fixture has 113 `raw_payments` rows, 99 `raw_orders` rows with IDs 1 through 99, zero payment→order relationship violations, zero duplicate payment IDs, and zero duplicate `order_payment_amount` fingerprints.

| mode | inserted rows | injected payment count | relationship violations | id duplicates | fingerprint duplicates | channel fact |
|---|---|---:|---:|---:|---:|---|
| `SINGLE_REFERENCE` | `(114, 1000, credit_card, 1000)` | 114 | 1 | 0 | 0 | `credit_card=56` |
| `SETTLED_COUPON_WINDOW` | `(114,1000,coupon,1700)`, `(115,1001,coupon,1800)`, `(116,1002,coupon,200)` | 116 | 3 | 0 | 0 | `coupon=16` |

No other payment ID, missing order ID, payment method, amount, relation, row list, or mutation mode is accepted.

### Root-cause ontology added by M10

- `SOURCE_PERMANENT_ORPHAN_PAYMENT`: one or more source payments reference orders that are absent even though the order ingestion history and watermark have advanced through the public settled alert window.
- `NORMAL_LATE_ARRIVING_ORDER`: the payment can temporarily precede its matching order while the relevant order ingestion window is incomplete or its watermark/history is unavailable.

The development case and variant A accept only `SOURCE_PERMANENT_ORPHAN_PAYMENT`. Variant B privately records both codes as compatible explanations but returns no root-cause or affected-asset claim.

A positive relationship-violation count proves only that an orphan exists now. It is not sufficient by itself to prove permanence. `SOURCE_PERMANENT_ORPHAN_PAYMENT` requires the compatible history/watermark evidence in the same root-cause claim.

### Watermark/history contract

- Keep `profile_spec.v1`, `config/profiles/jaffle_shop.v1.json`, `get_relation_history(relation_name)`, and `RelationHistoryFact` unchanged.
- Reuse the already declared `raw_orders.order_count_by_day` series, whose `watermark_column` is `order_date` and whose healthy watermark is `2018-04-09`.
- Each M10 brief uses `logical_observed_at=2018-04-09T12:00:00Z` and a public `SETTLED_ORDER_WINDOW_END=2018-04-03` observation. The model can see both the public alert boundary and the returned history watermark.
- A permanent-orphan root requires a parseable, non-null watermark at or after the public settled-window end. A relationship violation without that temporal boundary remains compatible with normal late arrival.
- M10 does not add or infer an ingestion SLA. The within-SLA health control and final ProfileSpec freeze remain M11 work.

### Evidence boundary

| role | observable decisive facts | deliberately unavailable facts | required cited evidence types |
|---|---|---|---|
| `DEV_CONFIRMABLE` | successful dbt run, downstream lineage, source schema, `raw_payments` relationship profile, `raw_orders` history/watermark, and the public settled-window boundary | none needed after the source orphan and completed ingestion boundary are both observed | `DBT_RUN_RESULTS`, `DBT_LINEAGE`, `RELATION_SCHEMA`, `RELATION_DATA_PROFILE`, `RELATION_HISTORY` |
| `TEST_CONFIRMABLE` | same evidence classes plus harmless nullable-column schema drift and coupon distribution | raw rows remain unavailable; aggregate relation and history facts are sufficient | `DBT_RUN_RESULTS`, `DBT_LINEAGE`, `RELATION_SCHEMA`, `RELATION_DATA_PROFILE`, `RELATION_HISTORY` |
| `TEST_INSUFFICIENT` | successful dbt run, downstream lineage, the same schema drift, and the same current relationship-violation profile | `get_relation_history(raw_orders)` returns `RELATION_NOT_ALLOWED`; no independent ingestion watermark is observable | `DBT_RUN_RESULTS`, `DBT_LINEAGE`, `RELATION_SCHEMA`, `RELATION_DATA_PROFILE` |

Variant B must emit exactly:

```json
[
  {
    "evidence_kind": "RELATION_HISTORY",
    "subject": "raw_orders",
    "reason_code": "RELATION_NOT_ALLOWED"
  },
  {
    "evidence_kind": "INGESTION_WATERMARK",
    "subject": "raw_orders",
    "reason_code": "NOT_OBSERVABLE"
  }
]
```

`INGESTION_WATERMARK` is a typed declaration of missing evidence, not a seventh tool. The model may declare it only for an incident subject and cannot fabricate an `EvidenceRecord` for it.

### Required behavior changes

1. Add `PAYMENT_ORPHAN` and one closed `ORPHAN_PAYMENT_ROWS` mutation with exactly the two batches above.
2. Generalize the existing dbt-success anomaly verifier just enough to route duplicate-payment and orphan-payment validators by mutation type; do not add a parallel verifier.
3. Keep the existing ProfileSpec and six evidence tools. M10 uses the already declared relationship and history facts.
4. A permanent-orphan confirmation requires a successful run, a positive declared payment→order relationship violation, a `raw_orders` watermark at or after the public settled-window end, and downstream lineage for affected assets.
5. Variant B requires one blocked `get_relation_history(raw_orders)` attempt plus the non-tool `INGESTION_WATERMARK` declaration.
6. Static Skill and Diagnostic Kernel receive the same ontology and environmental facts. Kernel validates state transitions and claim bindings but does not manufacture the root cause.
7. ProfileSpec version, Diagnosis field names, budgets, tool schemas, and six artifact filenames remain unchanged.

### Non-goals

- No raw-row tool, arbitrary SQL, seventh evidence tool, per-order lookup, event-log connector, user-defined mutation DSL, write-capable diagnosis tool, automatic repair, or case-ID controller dispatch.
- No ProfileSpec edit, new relationship metric, group-by filter, time-series type, or answer-specific profile entry.
- No custom dbt relationship test. M10 intentionally exercises a dbt-success/data-anomaly path.
- No edit to the pinned Jaffle Shop submodule.
- No change to the shared 8 model requests, 8 business tool calls, 2 output retries, or 300-second timeout.
- No change to `p1.diagnosis.v1` field names or the six artifact filenames.
- No controller tuning based on M7 real-model failures, no raised budget, no retry, no M8/M9/M10 real-model smoke, and no formal 94-run benchmark.
- No ten-cycle M10 loop on Windows. One focused real integration seam and the cumulative deterministic policy matrix are the M10 acceptance evidence. Existing native dbt exits remain environment-unverified unless a Python/business assertion also fails.
- No update to `docs/requirements.md`; sections 12.3 and 17 already define M10.
- No push. Commit and push remain separate authorization gates.
- No M11 silent-row-loss scenario or second health control.

### Acceptance criteria

- Exactly three M10 scenarios load, and `P1_M7_SCENARIO_IDS + P1_M8_SCENARIO_IDS + P1_M9_SCENARIO_IDS + P1_M10_SCENARIO_IDS` contains exactly 13 cases.
- The development case inserts exactly one orphan row; both test variants insert the exact same three coupon rows and `source_batch_note` distractor.
- All three M10 dbt builds succeed with no failed or skipped node and are privately classified as `EXPECTED_ANOMALY`.
- Private verification proves the exact inserted multiset, zero duplicate counts, exact relationship-violation and channel counts, unchanged `raw_orders` data, public profile/history scope, manifest-derived impact, and recovery to 113 payments/99 orders.
- The frozen `2018-04-09` watermark is parseable and is at or after the public `2018-04-03` settled-window end.
- The development case and variant A confirm `SOURCE_PERMANENT_ORPHAN_PAYMENT` only when the root claim cites run, profile, history, and compatible lineage evidence.
- Variant B can pass only as `INSUFFICIENT_EVIDENCE` with the two exact unresolved declarations and one matching blocked history-tool trace.
- Static Skill and Diagnostic Kernel retain the same six tools, public context, budgets, ontology, Diagnosis schema, evaluator, and artifact contract.
- The cumulative deterministic matrix is exactly 13 cases × 2 strategies = 26 passing cells, with six canonical artifacts per cell.
- The M7 real-model smoke remains collect-only and exactly eight items.
- Unit, focused integration, deterministic non-real-model E2E, Ruff, lock, diff, and build checks pass, subject only to the already documented Windows/dbt native-instability boundary.
- Final database state is healthy and the pinned submodule is unchanged.

## 1. Deep-module map

- `src/data_incident_gym/scenarios.py` owns the closed M10 catalog, orphan-batch allowlist, test-twin contract, history gap binding, and private scenario schema.
- `src/data_incident_gym/lab.py` owns transaction-safe payment-row insertion/deletion and lifecycle state checks.
- `src/data_incident_gym/lab_verifier.py` independently proves the exact live orphan state, dbt-success anomaly mode, manifest-derived impact, evidence scope, and recovery boundary.
- `config/profiles/jaffle_shop.v1.json` already declares the needed relationship and order history and remains unchanged in M10.
- `src/data_incident_gym/diagnosis.py` owns the two new unresolved-evidence vocabulary members.
- `src/data_incident_gym/diagnostic_agent.py` and both prompt files own the shared ontology and policy-neutral orphan reasoning.
- `src/data_incident_gym/diagnostic_kernel.py` owns history-gap binding, non-null watermark compatibility, and claim-evidence gates independent of private Ground Truth.
- `src/data_incident_gym/evaluation.py` independently compares public evidence with the private mutation and expected terminal.
- `tests/e2e/test_p1_policy_matrix.py` remains the single cumulative deterministic policy seam.

## Task 0: Establish the bounded M10 entry gate

**Files:**

- Read only: `AGENTS.md`
- Read only: `docs/requirements.md`
- Read only: `docs/superpowers/plans/2026-09-01-m10-orphan-payment-family.md`
- Read only: `config/profiles/jaffle_shop.v1.json`
- Read only: `third_party/jaffle_shop/seeds/raw_orders.csv`
- Read only: `third_party/jaffle_shop/seeds/raw_payments.csv`

**Step 1: Reconfirm the implementation base and dirty-file boundary.**

Run:

```powershell
git rev-parse HEAD
git status --short --branch
git diff --cached --name-status
git submodule status
git log --oneline 443c487..HEAD
```

Expected:

- `HEAD` is `49452c94992a88f9a0400da12a17da924057ebf2`, unless the user explicitly supplies a later approved base.
- The index is empty.
- Existing user materials remain visible and unstaged.
- The Jaffle Shop submodule is `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb` and clean.

If the base changed, stop and re-audit the delta before executing the rest of this plan. Do not silently transplant line numbers or expected counts.

**Step 2: Run only non-product preflight checks.**

Run:

```powershell
uv sync --frozen
uv run pytest tests/unit -q -p no:cacheprovider
uv run ruff check . --no-cache
uv lock --check
uv run pytest tests/e2e/test_p1_policy_matrix.py --collect-only -q -p no:cacheprovider
uv run pytest tests/e2e/test_real_model_m7_smoke.py --collect-only -q -p no:cacheprovider
```

Expected before M10 edits: `199 passed`, Ruff and lock pass, exactly 20 matrix cells collect, and exactly eight M7 smoke items collect. `--collect-only` must not be replaced with a real-model execution.

**Step 3: Probe database reachability without resetting it.**

Use a read-only connection with a three-second connect timeout and transaction read-only mode. Verify only:

- `analytics.raw_payments` has 113 rows;
- `analytics.raw_orders` has 99 rows;
- `raw_payments` has exactly `id, order_id, payment_method, amount`;
- no active M10 inserted rows exist.

If PostgreSQL is unreachable, stop implementation and report `ENTRY_ENVIRONMENT_UNAVAILABLE`. Do not call `pipeline build`, `lab reset`, Docker startup, or a seed merely to convert the probe into a pass without the user-authorized implementation environment being ready.

**Step 4: Record the unchanged baseline.**

Do not commit anything in Task 0. This task is a gate, not a code change.

## Task 1: Freeze the 13/17 catalog and orphan evidence boundary

**Files:**

- Modify: `src/data_incident_gym/scenarios.py`
- Create: `config/scenarios/orphan_payment_record.json`
- Create: `config/scenarios/orphan_payment_coupon_a.json`
- Create: `config/scenarios/orphan_payment_coupon_b.json`
- Modify: `tests/unit/test_scenarios.py`
- Modify: `tests/unit/test_p1_isolation.py`
- Include if authorized: `docs/superpowers/plans/2026-09-01-m10-orphan-payment-family.md`

**Step 1: Write the catalog and closed-batch tests first.**

Add focused tests that assert:

```python
assert P1_M10_SCENARIO_IDS == (
    "orphan_payment_record",
    "orphan_payment_coupon_a",
    "orphan_payment_coupon_b",
)
assert len(
    P1_M7_SCENARIO_IDS
    + P1_M8_SCENARIO_IDS
    + P1_M9_SCENARIO_IDS
    + P1_M10_SCENARIO_IDS
) == 13
```

Also assert:

- the two test variants have identical brief, mutations, affected assets, direct failure, and distractor;
- variant A exposes profile `raw_payments` and history `raw_orders`;
- variant B exposes profile `raw_payments`, exposes no history, and declares exactly the two frozen gaps;
- the two mutation modes resolve to the exact rows in the frozen table above;
- any changed ID, order ID, amount, channel, row count, or mode is rejected;
- M10 public briefs contain no variant role, answerability, accepted root, expected status, private path, mutation payload, or case ID dispatch hint.

Run:

```powershell
uv run pytest tests/unit/test_scenarios.py tests/unit/test_p1_isolation.py -q
```

Expected: RED because the M10 catalog, mutation, gap kinds, and scenarios do not exist.

**Step 2: Add the smallest closed scenario schema.**

In `scenarios.py`:

- add `P1_M10_SCENARIO_IDS` and append it to `SUPPORTED_SCENARIO_IDS`;
- add `FaultFamily.PAYMENT_ORPHAN`;
- add `OrphanPaymentRowsMutation` with discriminator `kind="ORPHAN_PAYMENT_ROWS"`, `relation="raw_payments"`, the two literal modes, inserted IDs, and missing-order IDs;
- add one private `_M10_ORPHAN_BATCHES` allowlist and `orphan_payment_rows()` resolver;
- add the mutation to `ScenarioMutation`;
- permit dbt-success insufficient scenarios for `PAYMENT_DUPLICATE` and `PAYMENT_ORPHAN`, without weakening other families;
- validate that every M10 role has exactly one orphan mutation, test roles have exactly one matching nullable-column distractor, and no other family can use the mutation;
- extend `ObservableEvidenceGap` only with `RELATION_HISTORY -> get_relation_history` and `INGESTION_WATERMARK -> None`.

Do not introduce a generic mutation registry or arbitrary row payload.

**Step 3: Add the three frozen JSON scenarios.**

Use `logical_observed_at="2018-04-09T12:00:00Z"`. All three briefs include a public `SETTLED_ORDER_WINDOW_END` observation with value `2018-04-03`. Test A and B must use identical public observations describing a coupon orphan alert for that settled window. Do not embed accepted roots, raw inserted rows, or expected terminal in the public brief.

All three use `direct_failure: null`, `expected_status` from the matrix, and the manifest-derived downstream asset set. The test pair uses the same `ADD_NULLABLE_COLUMN raw_payments.source_batch_note` mutation and matching distractor declaration.

**Step 4: Prove the existing ProfileSpec is sufficient and unchanged.**

Add no profile metric. The scenario tests must assert that M10 requests `raw_payments` current profile and `raw_orders` history, and the existing profile contract tests must remain green without editing `config/profiles/jaffle_shop.v1.json`.

**Step 5: Make the focused tests green and check schemas.**

Run:

```powershell
uv run pytest tests/unit/test_scenarios.py tests/unit/test_profiles.py tests/unit/test_p1_isolation.py -q
uv run ruff check src/data_incident_gym/scenarios.py tests/unit/test_scenarios.py tests/unit/test_p1_isolation.py --no-cache
```

Expected: PASS.

**Step 6: Commit only the authorized Task 1 files.**

If local commits are authorized for the implementation turn:

```powershell
git add src/data_incident_gym/scenarios.py config/scenarios/orphan_payment_record.json config/scenarios/orphan_payment_coupon_a.json config/scenarios/orphan_payment_coupon_b.json tests/unit/test_scenarios.py tests/unit/test_p1_isolation.py docs/superpowers/plans/2026-09-01-m10-orphan-payment-family.md
git diff --cached --check
git commit -m "feat: define M10 orphan payment scenarios"
```

Verify user-owned pre-existing files are still unstaged.

## Task 2: Implement transaction-safe orphan injection and exact recovery

**Files:**

- Modify: `src/data_incident_gym/lab.py`
- Modify: `tests/unit/test_lab.py`

**Step 1: Add RED lifecycle tests.**

Cover only the accepted behavior:

- healthy → inject → injected → restore → healthy for each frozen batch;
- totals are exactly 113→114→113 and 113→116→113;
- each inserted row is absent before injection, present exactly once after injection, and absent after recovery;
- insertion rowcount mismatch rolls back the transaction;
- deletion rowcount mismatch rolls back the transaction;
- unknown partial/drifted state is rejected rather than overwritten or broadly deleted;
- existing M9 exact-duplicate and semantic-duplicate lifecycle tests remain green.

Run:

```powershell
uv run pytest tests/unit/test_lab.py -q
```

Expected: RED on missing orphan lifecycle behavior.

**Step 2: Deepen only the existing payment-row seam.**

Extract the current exact SQL mechanics into private helpers that accept a frozen tuple of `PaymentRow` values:

- insert all rows in one transaction and require exact `cursor.rowcount`;
- delete only the exact frozen row multiset in one transaction and require the exact deleted count;
- keep parameterized SQL and `sql.Identifier` composition;
- keep M9 exact-duplicate semantics intact.

Use those helpers from the existing duplicate-payment wrappers and the new orphan-payment path. Do not add a general mutation framework, ORM, arbitrary SQL builder, or compatibility path.

**Step 3: Add orphan state integration to the existing lifecycle.**

Add a private `_orphan_payment_state()` returning only `HEALTHY`, `INJECTED`, or `DRIFTED`, then wire it into:

- `_ensure_healthy_for_prepare()`;
- `_apply_mutations()`;
- `_restore_mutations()`;
- `_verify_restored()`;
- `_validate_prepared_state()`.

The state is `INJECTED` only when the total and every frozen row multiplicity match exactly. Recovery must never delete by broad `order_id`, channel, or ID range.

**Step 4: Run focused and regression tests.**

```powershell
uv run pytest tests/unit/test_lab.py -q
uv run pytest tests/unit/test_scenarios.py -q
uv run ruff check src/data_incident_gym/lab.py tests/unit/test_lab.py --no-cache
```

Expected: PASS, including all existing M8/M9 transaction guards.

**Step 5: Commit if authorized.**

```powershell
git add src/data_incident_gym/lab.py tests/unit/test_lab.py
git diff --cached --check
git commit -m "feat: inject and recover orphan payments"
```

## Task 3: Prove the private environment and focused real-database seam

**Files:**

- Modify: `src/data_incident_gym/lab_verifier.py`
- Modify: `tests/unit/test_lab_verifier.py`
- Modify: `tests/integration/test_incident_lab.py`

**Step 1: Write RED private-verifier tests.**

Add focused tests for:

- exact private row totals and inserted multiset;
- relationship violation counts 1/3;
- duplicate key and fingerprint counts remain zero;
- channel counts 56/16;
- `raw_orders` remains 99 rows and watermark remains `2018-04-09`;
- variant A snapshot contains `raw_orders.order_count_by_day` history with watermark `2018-04-09`;
- variant B snapshot contains no `raw_orders` history while retaining `raw_payments` current profile;
- any off-by-one violation, wrong channel count, duplicate count, public-scope leak, skipped node, failed node, or unexpected dbt exit is rejected.

Run:

```powershell
uv run pytest tests/unit/test_lab_verifier.py -q
```

Expected: RED.

**Step 2: Generalize the one dbt-success anomaly branch.**

In `ScenarioVerifier.verify()` keep one `scenario.direct_failure is None` branch. Select the single supported source-data mutation by type:

- `DuplicatePaymentRowsMutation` → existing duplicate validator;
- `OrphanPaymentRowsMutation` → new orphan validator;
- anything else → closed failure.

Both paths must require dbt exit 0, no failed/skipped nodes, exact manifest descendants, matching baseline schema plus declared distractor, and a frozen private mutation. Do not dispatch on `incident_case_id` in Agent/controller code. A small private expected-value table inside `lab_verifier.py` is allowed because the verifier is Ground Truth territory.

**Step 3: Implement exact orphan verification.**

Query only aggregate/private verification facts with parameterized SQL. Compare them with the frozen table, then compare the public profile/history snapshots with the scenario evidence allowlist. Variant B must prove absence of the history snapshot rather than substitute an empty history fact.

Return the existing `ScenarioVerificationStatus.EXPECTED_ANOMALY`; add no new status.

**Step 4: Add one focused PostgreSQL integration test.**

One test function may loop over the three M10 cases. For each case:

1. reset to healthy;
2. prepare the exact mutation;
3. build without seed;
4. assert `EXPECTED_ANOMALY`, dbt success, exact profile/history visibility, and private verification;
5. restore in `finally`;
6. assert 113 payments, 99 orders, no inserted rows, no distractor column, and healthy baseline fingerprint.

Run:

```powershell
uv run pytest tests/integration/test_incident_lab.py -k orphan_payment -q
```

Expected: one focused test passes. A native dbt process exit without a Python/business assertion is `environment-unverified`; any wrong count, scope, status, or recovery assertion is a product failure.

**Step 5: Run focused checks and commit if authorized.**

```powershell
uv run pytest tests/unit/test_lab_verifier.py tests/unit/test_lab.py -q
uv run ruff check src/data_incident_gym/lab_verifier.py tests/unit/test_lab_verifier.py tests/integration/test_incident_lab.py --no-cache
git add src/data_incident_gym/lab_verifier.py tests/unit/test_lab_verifier.py tests/integration/test_incident_lab.py
git diff --cached --check
git commit -m "feat: verify orphan payment incidents"
```

## Task 4: Extend the shared diagnosis protocol and Diagnostic Kernel

**Files:**

- Modify: `src/data_incident_gym/diagnosis.py`
- Modify: `src/data_incident_gym/diagnostic_agent.py`
- Modify: `src/data_incident_gym/diagnostic_kernel.py`
- Modify: `src/data_incident_gym/prompts/static_skill.md`
- Modify: `src/data_incident_gym/prompts/diagnostic_kernel.md`
- Modify: `tests/unit/test_diagnosis.py`
- Modify: `tests/unit/test_diagnostic_agent.py`
- Modify: `tests/unit/test_diagnostic_kernel.py`
- Modify: `tests/unit/test_policy_fairness.py`

**Step 1: Add RED schema, parity, and gate tests.**

Test:

- both new root codes are accepted by the shared ontology;
- `RELATION_HISTORY` accepts only a matching blocked `get_relation_history` gap;
- `INGESTION_WATERMARK` accepts only `NOT_OBSERVABLE` and an incident subject;
- Static and Kernel prompts contain the same root semantics and neither contains M10 case IDs, expected counts, statuses, or private rows;
- a permanent-orphan confirmation succeeds only with successful-run, positive relationship, non-null order history/watermark, and downstream lineage evidence;
- relationship evidence without history is rejected;
- history without a positive relationship violation is rejected;
- missing, malformed, or empty watermark/history evidence is rejected;
- variant B derives the blocked `RELATION_HISTORY` declaration and accepts the bound non-tool watermark declaration;
- all prior exact/semantic duplicate, NULL, schema, and health gates stay green.

Run:

```powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_policy_fairness.py -q
```

Expected: RED.

**Step 2: Extend only the existing shared contracts.**

- Add the two root codes to the single ontology used by both strategies.
- Add `RELATION_HISTORY` and `INGESTION_WATERMARK` to `UnresolvedEvidence` without changing field names or schema version.
- Require `INGESTION_WATERMARK` to use `NOT_OBSERVABLE`.
- Extend Kernel blocked-gap binding and derived unresolved projection for `COMPARE_HISTORY`.
- Bind the non-tool watermark declaration to an incident subject.

Do not add a seventh tool or a policy-specific output schema.

**Step 3: Add generic permanent-orphan evidence gates.**

The Kernel helper must inspect public evidence types and declared profile/history names, not scenario IDs or private expected counts. Require:

- exactly one successful `DbtRunResultsFact` in the root claim;
- a `RelationDataProfileFact` on an incident relation with a positive `order_id_to_raw_orders_id` violation;
- a `RelationHistoryFact` on the incident order relation containing `order_count_by_day`, `watermark_column="order_date"`, a parseable non-null watermark, and at least one history point;
- affected-asset claims bound to compatible downstream lineage.

The evaluator, not the Kernel, owns exact counts and private expected rows.

**Step 4: Update both prompts symmetrically.**

Add the same policy-neutral rule to both prompts:

> A current payment-to-order relationship violation proves an orphan state, not permanence. Confirm a permanent orphan only when order history and its watermark show ingestion has advanced through the public settled window. If that boundary is unavailable, retain permanent-orphan and normal-late-arrival alternatives and return insufficient evidence.

Keep the Static playbook procedural and the Kernel prompt state-oriented. Do not copy Kernel intent syntax into the Static prompt.

**Step 5: Run focused tests and commit if authorized.**

```powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_policy_fairness.py -q
uv run ruff check src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/diagnostic_kernel.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_policy_fairness.py --no-cache
git add src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/diagnostic_kernel.py src/data_incident_gym/prompts/static_skill.md src/data_incident_gym/prompts/diagnostic_kernel.md tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_policy_fairness.py
git diff --cached --check
git commit -m "feat: add orphan payment diagnosis protocol"
```

## Task 5: Add deterministic M10 scoring without leaking Ground Truth

**Files:**

- Modify: `src/data_incident_gym/evaluation.py`
- Modify: `tests/unit/test_evaluation.py`

**Step 1: Write RED evaluator tests.**

Create deterministic `DiagnosisRunResult` fixtures and prove:

- dev passes with exact relationship count 1 and a watermark at or after the public settled-window end;
- variant A passes with exact relationship count 3, zero duplicate counts, coupon 16, the same valid temporal boundary, exact root, and exact assets;
- wrong violation count, any duplicate count, wrong channel count, missing run, skipped/failed node, missing history, wrong history name, absent/malformed watermark, watermark before the settled-window end, or missing lineage fails;
- variant B passes only with the exact two unresolved declarations and exactly one matching blocked history-tool event;
- variant B fails if it confirms either compatible cause, cites fabricated history, omits the non-tool gap, or adds a different gap;
- existing M7/M8/M9 evaluations remain unchanged.

Run:

```powershell
uv run pytest tests/unit/test_evaluation.py -q
```

Expected: RED.

**Step 2: Add an orphan-specific private evaluator branch.**

Detect `OrphanPaymentRowsMutation` from the private scenario, then require exact facts derived from the frozen mutation:

- run succeeded, failed/skipped empty;
- key and fingerprint duplicate counts are zero;
- declared relationship violation equals the number of frozen inserted rows;
- declared channel count matches the frozen batch;
- `SOURCE_PERMANENT_ORPHAN_PAYMENT` is the selected root;
- root claim cites the run, profile, and compatible history evidence;
- each asset claim cites compatible downstream lineage.

Do not use case IDs in public controller logic. Private scenario/mutation inspection is allowed only inside the evaluator.

**Step 3: Compare the public settled window with the returned watermark.**

Read the frozen public `SETTLED_ORDER_WINDOW_END` observation from the private scenario's `IncidentBrief`, parse it as a date, and require the `raw_orders.order_count_by_day` watermark date to be equal or later. Reject missing, malformed, or earlier values. Do not import Kernel internals or let the Kernel read private evaluator data.

**Step 4: Extend insufficiency trace matching.**

`RELATION_HISTORY` must bind to one `get_relation_history(raw_orders)` trace with `RELATION_NOT_ALLOWED`. `INGESTION_WATERMARK` has no tool call and is checked only as an exact typed declaration.

**Step 5: Run tests and commit if authorized.**

```powershell
uv run pytest tests/unit/test_evaluation.py tests/unit/test_diagnostic_kernel.py -q
uv run ruff check src/data_incident_gym/evaluation.py tests/unit/test_evaluation.py --no-cache
git add src/data_incident_gym/evaluation.py tests/unit/test_evaluation.py
git diff --cached --check
git commit -m "feat: score orphan payment evidence"
```

## Task 6: Close the 26-cell deterministic policy and artifact matrix

**Files:**

- Modify: `tests/e2e/test_p1_policy_matrix.py`
- Modify: `tests/unit/test_artifacts.py`
- Modify: `README.md`

**Step 1: Extend the matrix constants and RED scripted responses.**

Set:

```python
MATRIX_CASES = (
    P1_M7_SCENARIO_IDS
    + P1_M8_SCENARIO_IDS
    + P1_M9_SCENARIO_IDS
    + P1_M10_SCENARIO_IDS
)
assert len(MATRIX_CASES) == 13
assert len(MATRIX_CASES) * len(MATRIX_STRATEGIES) == 26
```

Add one evidence-driven FunctionModel path for the orphan family. It may branch on returned public evidence shape and allowed incident subjects, but it must not read `ScenarioSpec`, private verification, Ground Truth, case IDs, expected terminals, or fixed evaluator answers.

For confirmable cells, both strategies must collect run results, payment profile, order history, and downstream lineage before confirming. For insufficient cells, both must attempt order history exactly once, preserve both hypotheses, and emit the exact gaps.

**Step 2: Assert the exact M10 outcomes.**

Add M10 expectations only in the test oracle:

- dev and A: `CONFIRMED`, permanent-orphan root, exact three-model set;
- B: `INSUFFICIENT_EVIDENCE`, null root, empty public affected assets, exact two gaps.

Keep existing matrix assertions for evaluator pass, one terminal Kernel state, read-only tool allowlist, recovery, and exactly `ARTIFACT_FILENAMES`.

**Step 3: Prove the six-file artifact contract is unchanged.**

The filenames remain:

```text
run.json
diagnosis.json
evidence.jsonl
trace.jsonl
evaluation.json
report.md
```

Add only focused serialization/regression assertions needed for the new unresolved kinds and history citations. Do not create an M10 artifact schema or seventh file.

**Step 4: Update README with bounded M10 status.**

State only:

- P1 deterministic infrastructure now covers 13/17 scenarios;
- M10 adds permanent-orphan confirmable variants and a late-arrival/history-insufficient twin;
- the deterministic matrix is 26 cells;
- the latest real-model observation remains historical M7 1/8; M8/M9/M10 have no real-model result;
- Windows/dbt long-loop stability remains environment-unverified where reproduced.

Do not claim model-quality improvement, Kernel superiority, production readiness, or a passed M10 smoke.

**Step 5: Run the deterministic matrix and focused regressions.**

```powershell
uv run pytest tests/e2e/test_p1_policy_matrix.py -q
uv run pytest tests/unit/test_artifacts.py tests/unit/test_policy_fairness.py -q
uv run pytest tests/e2e/test_real_model_m7_smoke.py --collect-only -q -p no:cacheprovider
```

Expected: exactly 26 matrix cells pass, focused unit tests pass, and exactly eight smoke items collect. Do not run them.

**Step 6: Commit if authorized.**

```powershell
git add tests/e2e/test_p1_policy_matrix.py tests/unit/test_artifacts.py README.md
git diff --cached --check
git commit -m "test: close M10 deterministic policy matrix"
```

## Task 7: Run the M10 release gate without real-model calls or push

**Files:**

- No product edits expected.
- Record results only in the user-authorized workspace handoff location; do not stage `decision.md` or reports unless separately requested.

**Step 1: Run focused gates first.**

```powershell
uv run pytest tests/unit -q
uv run pytest tests/integration/test_incident_lab.py -k orphan_payment -q
uv run pytest tests/e2e/test_p1_policy_matrix.py -q
uv run ruff check . --no-cache
uv lock --check
git diff --check
uv build
```

Any Python assertion, wrong terminal, wrong fact count, evidence mismatch, artifact mismatch, or unhealthy recovery blocks M10.

**Step 2: Run the ordinary non-real-model suites once.**

```powershell
uv run pytest tests/integration -q
uv run pytest tests/e2e -m 'not real_model' -q
```

If either command ends in a known Windows/dbt native exit such as `3221225477`/`0xC0000005` or `3221226505` without a Python/business assertion, record the exact command, exit, passed/failed counts, and failing phase as `environment-unverified`. Do not relabel it as pass and do not modify product code merely to hide it.

**Step 3: Prove real-model scope stayed frozen.**

```powershell
uv run pytest tests/e2e/test_real_model_m7_smoke.py --collect-only -q -p no:cacheprovider
```

Expected: exactly eight items. Do not run `-m real_model`, do not call the endpoint, do not retry, and do not add M10 cases to this historical smoke.

**Step 4: Restore and inspect final database state.**

Use the authorized lab recovery path in `finally`, then perform a read-only inspection. Require:

- 113 `raw_payments` rows;
- 99 `raw_orders` rows;
- no IDs 114–116 inserted by M10;
- no `source_batch_note` column;
- zero payment→order relationship violations;
- healthy baseline fingerprint.

If recovery itself fails or the counts differ, M10 is blocked; that is not an environment-unverified pass.

**Step 5: Inspect the final repository boundary.**

```powershell
git status --short --branch
git diff --cached --name-status
git submodule status
git log --oneline 49452c9..HEAD
git diff --check 49452c9...HEAD
```

Expected:

- only planned M10 commits appear after `49452c9`;
- the index is empty;
- pre-existing user files remain unstaged;
- generated artifacts, `.dig`, credentials, and build output are not committed;
- submodule commit and worktree are unchanged.

**Step 6: Stop at the release gate.**

Report separately:

- implemented behavior;
- exact deterministic test results;
- focused integration result;
- full integration/E2E result and any environment-unverified native exit;
- exact 26-cell matrix result;
- exact eight-item collect-only smoke result;
- final database recovery evidence;
- current HEAD and commit list;
- unchanged dirty/user-owned file list;
- that real-model execution and push still require separate authorization.

Do not push, run a real model, start M11, delete ignored validation helpers, or edit user notes.

## Final M10 acceptance checklist

- [ ] Entry base and dirty-file boundary were revalidated before edits.
- [ ] Exactly three M10 scenarios exist and cumulative P1 coverage is 13/17.
- [ ] The closed orphan mutation accepts only the two frozen batches.
- [ ] All M10 cases are dbt-success `EXPECTED_ANOMALY` scenarios.
- [ ] Test variants A and B have identical data, alert, distractor, and affected assets.
- [ ] Current orphan evidence is separated from permanence evidence.
- [ ] `raw_orders.order_count_by_day` and its existing watermark are reused without a ProfileSpec change.
- [ ] The evaluator proves the watermark is at or after the public settled-window end.
- [ ] Dev and A confirm only from compatible run, relationship profile, history, watermark, and lineage evidence.
- [ ] B can pass only with the exact blocked-history and missing-watermark declarations.
- [ ] No seventh tool, raw-row access, free SQL, case-ID dispatch, or ProfileSpec answer field was added.
- [ ] Static Skill and Diagnostic Kernel share ontology, tools, budgets, public context, Diagnosis schema, evaluator, and artifacts.
- [ ] The cumulative deterministic matrix is exactly 26/26 and every cell writes six canonical artifacts.
- [ ] The historical M7 smoke remains exactly eight collect-only items; M8/M9/M10 model quality is unmeasured.
- [ ] Database state and pinned submodule are healthy after verification.
- [ ] No generated artifact, secret, user-owned note, or unrelated dirty file is staged.
- [ ] No real-model call, push, or M11 work occurs without later explicit authorization.
