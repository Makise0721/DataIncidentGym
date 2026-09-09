# M11 Silent Payment Drop and Benchmark Freeze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Do not delegate work unless the user explicitly authorizes delegation for that execution turn.

**Goal:** Add the final silent-payment-drop fault family and the second SLA health control, bring the P1 catalog from 13/17 to 17/17, and freeze every result-affecting input plus an executable 106-cell Benchmark Manifest without running any real model.

**Architecture:** Extend the existing vertical slice in place. A closed `DELETE_PAYMENT_ROWS` mutation removes only two frozen fixture batches; public aggregate profiles expose current payment/order integrity, fixed histories expose the affected partition and order watermark, and the confirmable/insufficient twins differ only in history/watermark observability. The existing order history receives one 24-hour SLA, and health validation treats the watermark bucket as an incomplete current partition that must pass the SLA gate instead of falling back to a historical range. After the 34-cell deterministic main-policy matrix is green, add only the execution variants and suite machinery already required by the approved 94-run protocol, freeze their identities in `p1-formal-v1`, and leave all actual model calls to M12.

**Tech Stack:** Python 3.12, Pydantic v2, psycopg 3 SQL composition, dbt Core/PostgreSQL Jaffle Shop fixture, PydanticAI FunctionModel, Typer, pytest, Ruff, uv, PowerShell 7.

---

## 0. Approved scope and fixed decisions

### Planning baseline and M10 handoff

- This plan is written against `master` at exact `ae94dbc0fe5f3861afb8fca0a3f0d35602e22720`, aligned with `origin/master`.
- The user supplied and the release report records Ubuntu Actions run `33467202194` as green for M10, including the exact 26/26 main-policy matrix. M10 is therefore the released base, not a local candidate to be reworked.
- A fresh read-only planning check passed with `241 passed`, Ruff green, `uv lock --check` green, exactly 26 current matrix cells collected, and exactly eight M7 development-smoke cells collected. No database reset, build, model call, commit, or push was performed while writing this plan.
- `docs/requirements.md`, `AGENTS.md`, `decision.md`, older untracked plans, and `docs/superpowers/reports/` are user-owned workspace material. They remain unstaged and unmodified by M11. `README.md` is a tracked product document and may be updated only in the final M11 documentation step.
- The Jaffle Shop submodule remains pinned at `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb` and must not be edited.
- Creating this plan authorizes no implementation, real-model call, commit, push, cleanup, deletion, M12 execution, or benchmark conclusion.

### Exact M11 scenario matrix

| case_id | role | mutation or healthy fact | decisive public evidence | expected terminal |
|---|---|---|---|---|
| `silent_payment_drop_record` | `DEV_CONFIRMABLE` | Delete payment `111`, the only payment for order `97`; the 2018-04-07 payment partition becomes `1` instead of `2` | successful dbt run, payment row count `112`, one order-without-payment violation, payment history point `2018-04-07=1`, order watermark `2018-04-09`, and downstream lineage | `CONFIRMED` / `SOURCE_PAYMENT_INGESTION_LOSS` |
| `silent_payment_drop_partition_a` | `TEST_CONFIRMABLE` | Delete payments `89` and `92`, the only payments for orders `78` and `80`; the 2018-03-23 partition becomes `3` instead of `5`; add nullable `source_batch_note` distractor | successful dbt run, payment row count `111`, two order-without-payment violations, payment history point `2018-03-23=3`, order watermark after the settled partition, and downstream lineage | `CONFIRMED` / `SOURCE_PAYMENT_INGESTION_LOSS` |
| `silent_payment_drop_partition_b` | `TEST_INSUFFICIENT` | Byte-for-byte the same two deleted rows, alert, database state, and distractor as variant A | current payment/order profiles remain visible, but both payment history and order history/watermark are unavailable | `INSUFFICIENT_EVIDENCE` |
| `order_volume_within_sla` | `NO_INCIDENT_CONTROL` | No mutation; at `2018-04-09T12:00:00Z`, the current `2018-04-09` order partition has one row and the watermark is `2018-04-09` under a 24-hour SLA | successful dbt run plus matching order profile/history; logical lag is exactly 12 hours and therefore within `86400` seconds | `NO_INCIDENT` |

The two test variants must share the exact same `IncidentBrief`, `DELETE_PAYMENT_ROWS` mutation, nullable-column distractor, affected assets, dbt-success result, private database state, and public current profiles. They may differ only in accepted explanations, history allowlist, unresolved gaps, required evidence types, answerability, and expected terminal.

### Frozen fixture rows and aggregate facts

The healthy fixture has 113 `raw_payments`, 99 `raw_orders`, payment-method counts `bank_transfer=33`, `coupon=13`, `credit_card=55`, `gift_card=12`, and zero orders without a payment.

```text
SOURCE_BATCH:
  (111, 97, bank_transfer, 1400) on order_date 2018-04-07

SETTLED_PARTITION:
  (89, 78, bank_transfer, 2600) on order_date 2018-03-23
  (92, 80, gift_card, 300) on order_date 2018-03-23
```

| mode | injected payment count | target history point | order-without-payment violations | affected channel facts |
|---|---:|---|---:|---|
| `SOURCE_BATCH` | 112 | `2018-04-07=1` | 1 | `bank_transfer=32` |
| `SETTLED_PARTITION` | 111 | `2018-03-23=3` | 2 | `bank_transfer=32`, `gift_card=11` |

No other payment ID, row value, relation, deletion mode, partition, target count, or mutation payload is accepted. Exact rows stay private to ScenarioSpec, lab, and evaluator; diagnosis-plane code and prompts may contain neither these IDs nor these frozen counts.

### Exact public alerts

`silent_payment_drop_record` exposes these sanitized observations:

```json
[
  {"kind":"CURRENT_PERIOD_COUNT","subject":"raw_payments/payment_count_by_order_date/2018-04-07","value":"1"},
  {"kind":"EXPECTED_PERIOD_COUNT","subject":"raw_payments/payment_count_by_order_date/2018-04-07","value":"2"},
  {"kind":"CURRENT_RELATION_COUNT","subject":"raw_payments","value":"112"},
  {"kind":"SETTLED_PAYMENT_WINDOW_END","subject":"raw_orders","value":"2018-04-07"}
]
```

Both test variants expose the same observations:

```json
[
  {"kind":"CURRENT_PERIOD_COUNT","subject":"raw_payments/payment_count_by_order_date/2018-03-23","value":"3"},
  {"kind":"EXPECTED_PERIOD_COUNT","subject":"raw_payments/payment_count_by_order_date/2018-03-23","value":"5"},
  {"kind":"CURRENT_RELATION_COUNT","subject":"raw_payments","value":"111"},
  {"kind":"SETTLED_PAYMENT_WINDOW_END","subject":"raw_orders","value":"2018-03-23"}
]
```

All three briefs use `logical_observed_at=2018-04-09T12:00:00Z` and subjects `seed.jaffle_shop.raw_payments`, `raw_payments`, `raw_orders`, and `payment_count_by_order_date`. The alert-provided expected count is a public operational baseline, not Ground Truth. Confirmation still requires independently returned current profile, history, watermark, successful-run, and lineage EvidenceRecords.

`order_volume_within_sla` exposes `CURRENT_PERIOD_COUNT` for `raw_orders/order_count_by_day/2018-04-09` with value `1`, and a comparison observation for `2018-03-26` with value `3`. It uses `logical_observed_at=2018-04-09T12:00:00Z`.

### Root-cause ontology and evidence boundary

- `SOURCE_PAYMENT_INGESTION_LOSS`: expected source payment events are absent in a settled partition; the current payment aggregate is below the public expected count, corresponding orders have no payment, and the order watermark has passed the partition boundary.
- `NORMAL_BUSINESS_PAYMENT_DECLINE`: a lower payment count is a genuine business-volume change or legitimately unpaid order population rather than an ingestion loss.

The development case and variant A accept only `SOURCE_PAYMENT_INGESTION_LOSS`. Variant B privately retains both codes and returns no root-cause or affected-asset claim.

Variant B must emit exactly these three unresolved declarations and produce exactly one matching failed history-tool call for each tool-backed declaration:

```json
[
  {"evidence_kind":"RELATION_HISTORY","subject":"raw_payments","reason_code":"RELATION_NOT_ALLOWED"},
  {"evidence_kind":"RELATION_HISTORY","subject":"raw_orders","reason_code":"RELATION_NOT_ALLOWED"},
  {"evidence_kind":"INGESTION_WATERMARK","subject":"raw_orders","reason_code":"NOT_OBSERVABLE"}
]
```

`INGESTION_WATERMARK` remains a non-tool missing-evidence declaration. M11 adds no seventh tool, raw-row access, free SQL, filtered query interface, or answer-specific ProfileSpec field.

### Final ProfileSpec and SLA semantics

- Keep `profile_spec.v1` and the existing six main-policy tool schemas.
- Add one generic reverse relationship to the `raw_orders` profile: `id_to_raw_payments_order_id`, with local `raw_orders.id` and referenced `raw_payments.order_id`. This reports orders with no payment and is not tied to a case ID or expected answer.
- Keep the existing `raw_payments.payment_count_by_order_date` history unchanged.
- Set `raw_orders.order_count_by_day.sla_seconds` from `null` to exactly `86400`; retain `watermark_column=order_date`, periodicity `DAY_OF_WEEK`, and the existing point set.
- Do not reinterpret an EvidenceRecord's wall-clock `observed_at` as fixture event time. SLA lag is calculated from the public `IncidentBrief.logical_observed_at` and the history watermark.
- If the claimed bucket equals the history watermark bucket, it is the current incomplete partition: it must have a declared SLA and satisfy `0 <= logical_observed_at - watermark <= sla_seconds`. It must not fall back to the historical range.
- If the claimed bucket precedes the watermark, it is historical: the existing same-period range rule remains in force. This keeps `order_volume_pattern_a` a historical-range control and makes `order_volume_within_sla` an actual SLA control.

### Exact formal suite frozen by M11

The formal test scenario order is fixed as:

```text
schema_type_change_order_customer_a
schema_type_change_order_customer_b
required_null_order_customer_a
required_null_order_customer_b
duplicate_payment_coupon_a
duplicate_payment_coupon_b
orphan_payment_coupon_a
orphan_payment_coupon_b
silent_payment_drop_partition_a
silent_payment_drop_partition_b
order_volume_pattern_a
order_volume_within_sla
```

The Benchmark Manifest contains 106 predeclared cells:

- 72 main real-model cells: 12 scenarios × `STATIC_SKILL`/`DIAGNOSTIC_KERNEL` × three repeats.
- 12 `NO_TOOL` real-model cells: every formal scenario once.
- Five `KERNEL_NO_LINEAGE` real-model cells: the five `TEST_CONFIRMABLE` scenarios once.
- Five `KERNEL_NO_SCHEMA` real-model cells: the same five `TEST_CONFIRMABLE` scenarios once.
- 12 `FIXED_RULE` cells: every formal scenario once with zero model requests.

Thus the manifest validates exactly 94 model-backed cells and 12 no-model cells. Development cases, P0 regression, doctor probe, M7 smoke, and any M8-M11 development checks are excluded.

The fixed manifest identity and path are:

```text
manifest_id: p1-formal-v1
path: config/benchmark/p1-formal-v1.json
```

Every run ID is the first 32 lowercase hexadecimal characters of SHA-256 over the UTF-8 string:

```text
p1-formal-v1:<sequence>:<incident_case_id>:<strategy>:<repeat_index>
```

The manifest generator writes every cell explicitly and rejects any collision. A future invalidated generation uses `p1-formal-v2`; `p1-formal-v1.json` is never overwritten or silently edited.

### Execution ordering

For main repeats 1, 2, and 3, rotate the 12-scenario order by 0, 4, and 8 positions respectively. Within each rotated list, use Static then Kernel when `position + repeat_index` is odd, otherwise Kernel then Static. Append the 12 no-tool cells in reverse formal-scenario order, then interleave the two ablations per confirmable family, then append the 12 fixed-rule cells in formal-scenario order. Persist the resulting explicit sequence numbers 1 through 106 in the manifest.

### Freeze-commit rule

Git commit hashes cannot safely self-reference a file contained in the same commit. Therefore:

1. The result-affecting M11 implementation is committed first as the immutable implementation revision.
2. Ubuntu CI must pass on that exact revision before `p1-formal-v1` is generated.
3. The manifest records that implementation revision, all result-input hashes, and all 106 cells.
4. The manifest is then committed alone in one packaging commit.
5. Formal M12 execution uses a clean checkout of the packaging commit. The verifier requires that the implementation revision is its ancestor and that the only tracked diff since it is `config/benchmark/p1-formal-v1.json`; it also recomputes all result-input hashes.

Single-run `metadata.json` continues to record the actual clean checkout revision and the manifest SHA-256. The suite manifest independently records the bound implementation revision. This avoids a false self-referential commit claim while preserving a durable checked-in manifest.

### Non-goals

- No real-model smoke, doctor model probe, formal benchmark cell, M12 summary, aggregate metric, confidence interval, strategy winner, or README performance number.
- No model/provider switch, sampling change, prompt tuning based on M7 outputs, budget increase, retry, replacement sample, or extra run.
- No user-defined fault DSL, raw-row evidence tool, free SQL, write-capable diagnosis tool, automatic repair, Airflow, OpenLineage service, Web UI, or multi-agent diagnosis.
- No new evidence tool or main-policy tool-schema change. Auxiliary ablations may only remove declared tools.
- No fixture/submodule edit and no new dbt model or test.
- No update to `docs/requirements.md`, `AGENTS.md`, `decision.md`, or existing reports.
- No ten-cycle M11 loop on Windows. Use focused integration, the cumulative 34-cell deterministic matrix, and Ubuntu CI; classify only known native dbt/Python exits as environment-unverified when no Python/business assertion fails.
- No push without separate explicit authorization.

### Acceptance criteria

- Exactly four M11 scenarios load; the P1 catalog is exactly 17 and the overall supported catalog is 18 including the P0 regression.
- Both test variants produce identical injected state and current profiles; only variant A exposes payment/order histories and watermark.
- All three silent-drop dbt builds succeed with no failed/skipped nodes and are privately classified `EXPECTED_ANOMALY`; the health control is `HEALTHY_CONTROL`.
- Private verification proves exact deleted rows, exact remaining counts/groups/history points, exact reverse-relationship violations, manifest-derived affected assets, evidence scope, and healthy restoration to 113 payments/99 orders/zero reverse violations.
- Main Static and Kernel policies share exactly the same six tools, public context, model configuration, `8/8/2/300` budget, Diagnosis schema, evaluator, and six-file artifact contract.
- Confirmable silent-drop roots require compatible run, both current profiles, both histories/watermark, public alert comparison, and lineage. Neither a low count alone nor a successful dbt run can confirm ingestion loss.
- The insufficient twin passes only with the exact three declarations and two matching blocked tool traces.
- The historical-range health control and current-partition SLA health control both pass for the intended reason; expired/missing SLA and wall-clock EvidenceRecord time cannot pass the SLA case.
- The cumulative main-policy matrix is exactly 17 × 2 = 34 passing cells, each with six canonical artifacts.
- The final main allowlist remains exactly six tools. `NO_TOOL`, both Kernel ablations, and `FIXED_RULE` are reachable only through the benchmark runner, not the ordinary `diagnose` or `eval run` CLI.
- `p1-formal-v1.json` contains exactly 106 unique run IDs, 94 model-backed cells, 12 fixed-rule cells, 72 main cells, the exact scenario/strategy counts, execution order, model configuration, budgets, scenario/ProfileSpec/prompt/controller/tool/evaluator hashes, and the bound implementation revision.
- Formal runner tests prove preassigned IDs, doctor-before-start, append-only no-retry progress, six-file failure artifacts, manifest-hash propagation, fresh sessions, and refusal on any result-input drift without calling a real model.
- M7 smoke remains collect-only with exactly eight items. Manifest validation proves exactly 94 model-backed cells; no formal model cell is executed in M11.
- Unit, focused integration, non-real-model E2E, Ruff, lock, diff, build, exact-head Ubuntu CI, final database health, and submodule cleanliness pass.

## 1. Deep-module map

- `src/data_incident_gym/scenarios.py` owns the 17-case catalog, closed deletion batches, role/evidence contracts, and private scenario schema.
- `config/scenarios/*.json` owns exact ScenarioSpecs; the two test twins are compared structurally in tests.
- `config/profiles/jaffle_shop.v1.json` owns the final generic reverse relationship and 24-hour order SLA.
- `src/data_incident_gym/profiles.py` owns aggregate/watermark parsing, but not scenario answers.
- `src/data_incident_gym/lab.py` owns transaction-safe delete/reinsert lifecycle and preassigned run IDs.
- `src/data_incident_gym/lab_verifier.py` independently proves exact live facts and public/private evidence scope.
- `src/data_incident_gym/diagnostic_agent.py` and `src/data_incident_gym/prompts/` own the shared ontology, main policies, and model-backed auxiliary variants.
- `src/data_incident_gym/diagnostic_kernel.py` owns public evidence sufficiency, SLA/range gates, and Kernel state semantics without importing ScenarioSpec.
- `src/data_incident_gym/evaluation.py` independently scores frozen DiagnosisRunResult/evidence/trace against private ScenarioSpec.
- `src/data_incident_gym/fixed_rule.py` owns one explicit, public-evidence-only, no-model rules baseline.
- `src/data_incident_gym/benchmark_manifest.py` owns manifest schema, exact schedule, hashes, freeze, and drift validation.
- `src/data_incident_gym/benchmark_runner.py` owns doctor-before-start, one-pass sequencing, progress ledger, failure materialization, and resume-without-retry behavior.
- `src/data_incident_gym/artifacts.py` remains the only six-file writer and receives the frozen manifest digest.
- `tests/e2e/test_p1_policy_matrix.py` remains the single cumulative 34-cell deterministic main-policy seam.

## Task 0: Establish the bounded M11 entry gate

**Files:**

- Read only: `AGENTS.md`
- Read only: `docs/requirements.md`
- Read only: `docs/superpowers/plans/2026-09-01-m11-silent-payment-drop-benchmark-freeze.md`
- Read only: `docs/superpowers/reports/2026-09-01-m10-release.md`
- Read only: `config/profiles/jaffle_shop.v1.json`
- Read only: `third_party/jaffle_shop/seeds/raw_orders.csv`
- Read only: `third_party/jaffle_shop/seeds/raw_payments.csv`

- [ ] **Step 1: Reconfirm the approved base and user-owned dirty boundary.**

Run:

```powershell
git rev-parse HEAD
git status --short --branch
git diff --cached --name-status
git submodule status
git log -1 --oneline
```

Expected: exact base `ae94dbc0fe5f3861afb8fca0a3f0d35602e22720`, empty index, the known user materials still unstaged, and clean pinned submodule `36bde6c...`. If HEAD or the product diff changed, stop and re-audit instead of transplanting this plan.

- [ ] **Step 2: Re-run only no-network, no-database entry checks.**

```powershell
uv sync --frozen
uv run pytest tests/unit -q -p no:cacheprovider
uv run ruff check . --no-cache
uv lock --check
uv run pytest tests/e2e/test_p1_policy_matrix.py --collect-only -q -p no:cacheprovider
uv run pytest tests/e2e/test_real_model_m7_smoke.py --collect-only -q -p no:cacheprovider
```

Expected before M11 edits: `241 passed`, Ruff/lock green, exactly 26 matrix cells, and exactly eight M7 smoke cells. Do not replace either collect-only command with execution.

- [ ] **Step 3: Probe the database read-only without resetting it.**

Use a three-second diagnostic-role connection, `SET TRANSACTION READ ONLY`, and bounded scalar queries. Confirm 113 payments, 99 orders, the exact four payment columns, and the presence of all three frozen source rows. If PostgreSQL is unavailable, report `ENTRY_ENVIRONMENT_UNAVAILABLE`; do not seed/reset merely to manufacture a pass.

- [ ] **Step 4: Leave Task 0 with no changes.**

Do not stage or commit anything. Task 0 is a gate.

## Task 1: Freeze the 17/17 ScenarioSpec and final ProfileSpec

**Files:**

- Modify: `src/data_incident_gym/scenarios.py`
- Modify: `config/profiles/jaffle_shop.v1.json`
- Create: `config/scenarios/silent_payment_drop_record.json`
- Create: `config/scenarios/silent_payment_drop_partition_a.json`
- Create: `config/scenarios/silent_payment_drop_partition_b.json`
- Create: `config/scenarios/order_volume_within_sla.json`
- Modify: `tests/unit/test_scenarios.py`
- Modify: `tests/unit/test_profiles.py`
- Modify: `tests/unit/test_p1_isolation.py`

- [ ] **Step 1: Add focused RED contract tests.**

Test exactly:

```python
P1_M11_SCENARIO_IDS == (
    "silent_payment_drop_record",
    "silent_payment_drop_partition_a",
    "silent_payment_drop_partition_b",
    "order_volume_within_sla",
)
len(P1_SCENARIO_IDS) == 17
len(SUPPORTED_SCENARIO_IDS) == 18
```

Also assert the two test variants have identical `incident_brief`, mutations, affected assets, and distractor; reject any changed ID/order/value/mode/payload; and prove none of the private IDs `89`, `92`, or `111` appears in diagnosis-plane Python or prompts.

Run:

```powershell
uv run pytest tests/unit/test_scenarios.py tests/unit/test_profiles.py tests/unit/test_p1_isolation.py -q -p no:cacheprovider
```

Expected: RED because the M11 catalog, mutation, profile facts, and files do not exist.

- [ ] **Step 2: Add the closed mutation and role rules.**

Implement the minimal shape:

```python
class DeletePaymentRowsMutation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["DELETE_PAYMENT_ROWS"]
    purpose: Literal["FAULT"]
    relation: Literal["raw_payments"]
    mode: Literal["SOURCE_BATCH", "SETTLED_PARTITION"]
    deleted_payment_ids: tuple[StrictInt, ...]
```

Back it with one closed mapping from the two modes/ID tuples to the exact rows declared above. Add `PAYMENT_SILENT_DROP`, permit dbt-success insufficiency for this family, require `SOURCE_BATCH` only for the development role, require `SETTLED_PARTITION` plus exactly one matching nullable-column distractor for both test roles, and reject `DELETE_PAYMENT_ROWS` in every other family.

- [ ] **Step 3: Write the four exact ScenarioSpecs.**

Use `direct_failure=null` and affected assets:

```json
[
  "model.jaffle_shop.customers",
  "model.jaffle_shop.orders",
  "model.jaffle_shop.stg_payments"
]
```

For the development case and A, expose both `raw_payments` and `raw_orders` profiles/histories. For B, expose both current profiles but no histories and declare the exact three gaps above. For both health controls, use only `NO_MUTATION` and no root/affected assets.

- [ ] **Step 4: Finalize the scenario-agnostic ProfileSpec.**

Add this relationship under `raw_orders.relationships`:

```json
{
  "name": "id_to_raw_payments_order_id",
  "local_columns": ["id"],
  "referenced_relation": "raw_payments",
  "referenced_columns": ["order_id"]
}
```

Change only `raw_orders.order_count_by_day.sla_seconds` to `86400`. Keep version, maxima, relation list, existing metrics, join path, and six-tool contract unchanged.

- [ ] **Step 5: Make the focused tests GREEN and audit leakage.**

```powershell
uv run pytest tests/unit/test_scenarios.py tests/unit/test_profiles.py tests/unit/test_p1_isolation.py -q -p no:cacheprovider
rg -n '89|92|111|silent_payment_drop_' src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/diagnostic_kernel.py src/data_incident_gym/prompts
```

Expected: tests pass; the leakage search has no match.

- [ ] **Step 6: Commit only the scenario/profile slice when implementation commits are authorized.**

```powershell
git add src/data_incident_gym/scenarios.py config/profiles/jaffle_shop.v1.json config/scenarios/silent_payment_drop_record.json config/scenarios/silent_payment_drop_partition_a.json config/scenarios/silent_payment_drop_partition_b.json config/scenarios/order_volume_within_sla.json tests/unit/test_scenarios.py tests/unit/test_profiles.py tests/unit/test_p1_isolation.py docs/superpowers/plans/2026-09-01-m11-silent-payment-drop-benchmark-freeze.md
git diff --cached --check
git commit -m "feat: freeze final P1 scenario catalog"
```

Do not stage user-owned materials.

## Task 2: Implement exact delete/restore lifecycle and private verification

**Files:**

- Modify: `src/data_incident_gym/lab.py`
- Modify: `src/data_incident_gym/lab_verifier.py`
- Modify: `tests/unit/test_lab.py`
- Modify: `tests/unit/test_lab_verifier.py`
- Modify: `tests/integration/test_incident_lab.py`

- [ ] **Step 1: Write lifecycle RED tests.**

Cover only the two allowed modes and critical failure guard:

- healthy → delete exact rows → injected;
- injected → reinsert exact rows → healthy;
- wrong total, missing target, duplicate target, or partial deletion is `DRIFTED` and rejected;
- SQL is identifier-composed, values are bound parameters, and transaction rowcount must equal the frozen batch length;
- reverse mutation ordering restores rows and then lets the existing full-refresh reset prove the baseline.

```powershell
uv run pytest tests/unit/test_lab.py tests/unit/test_lab_verifier.py -q -p no:cacheprovider
```

Expected: RED on the new behavior.

- [ ] **Step 2: Add the smallest lifecycle path.**

Reuse `_payment_row_count`, `_payment_row_total`, `_delete_payment_rows`, and `_insert_payment_rows`. Add only `_silent_payment_drop_state`, `_delete_source_payments`, and `_restore_source_payments`, then route `DeletePaymentRowsMutation` through the existing prepare/apply/build/restore/verify methods. Do not create a second lab or generic mutation framework.

- [ ] **Step 3: Extend schema projection and anomaly routing.**

In `_validate_mutation_schema`, subtract `len(deleted_payment_ids)`. In the dbt-success anomaly branch, include `DeletePaymentRowsMutation` in the existing source-seed impact derivation and dispatch to one `_validate_silent_payment_drop` method.

- [ ] **Step 4: Privately prove exact M11 facts.**

The verifier must query only on the lab/private connection and assert:

- every frozen target row is absent and the other frozen/sentinel rows remain;
- exact total, target partition count, channel counts, and reverse relationship violations;
- key/fingerprint duplicate counts remain zero;
- dbt exit is zero with no failed/skipped nodes;
- affected assets come from the manifest seed anchor, not ScenarioSpec alone;
- public current/history relations exactly match the observable contract;
- A contains both history series and a parseable `2018-04-09` order watermark; B contains neither history snapshot;
- health control snapshot equals the healthy baseline and contains `sla_seconds=86400`.

- [ ] **Step 5: Run focused unit and one real integration seam.**

```powershell
uv run pytest tests/unit/test_lab.py tests/unit/test_lab_verifier.py -q -p no:cacheprovider
uv run pytest tests/integration/test_incident_lab.py -k 'silent_payment_drop or order_volume_within_sla' -q -p no:cacheprovider
```

Expected: unit tests pass; focused integration has four passing cases and leaves 113 payments, 99 orders, and zero order-without-payment violations.

- [ ] **Step 6: Commit the lifecycle slice when authorized.**

```powershell
git add src/data_incident_gym/lab.py src/data_incident_gym/lab_verifier.py tests/unit/test_lab.py tests/unit/test_lab_verifier.py tests/integration/test_incident_lab.py
git diff --cached --check
git commit -m "feat: reproduce silent payment loss"
```

## Task 3: Add evidence-compatible silent-drop and SLA gates

**Files:**

- Modify: `src/data_incident_gym/profiles.py`
- Modify: `src/data_incident_gym/diagnostic_agent.py`
- Modify: `src/data_incident_gym/diagnostic_kernel.py`
- Modify: `src/data_incident_gym/evaluation.py`
- Modify: `src/data_incident_gym/prompts/static_skill.md`
- Modify: `src/data_incident_gym/prompts/diagnostic_kernel.md`
- Modify: `tests/unit/test_diagnostic_agent.py`
- Modify: `tests/unit/test_diagnostic_kernel.py`
- Modify: `tests/unit/test_evaluation.py`

- [ ] **Step 1: Write focused RED gate tests.**

Add tests proving:

- the new root is rejected when any of successful-run, current payment profile, reverse order profile, target payment-history point, settled order watermark, public expected/current comparison, or downstream lineage is missing/incompatible;
- no diagnosis-plane test uses case IDs or private row IDs;
- B can bind two blocked history gaps plus one non-tool watermark gap;
- `order_volume_pattern_a` passes the historical range path;
- `order_volume_within_sla` passes only the current-watermark-bucket SLA path;
- an absent SLA, negative lag, lag above 86400, mismatched bucket, wall-clock EvidenceRecord time, or fallback to range is rejected.

```powershell
uv run pytest tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_evaluation.py -q -p no:cacheprovider
```

Expected: RED on the new ontology and gates.

- [ ] **Step 2: Add shared, deterministic watermark parsing.**

Add one small helper in `profiles.py` that parses date-only or ISO watermark strings as UTC and returns a timezone-aware value. Both Kernel and evaluator use it; neither uses EvidenceRecord wall-clock time for SLA calculations.

- [ ] **Step 3: Add the two ontology members and bump frozen policy versions.**

Append `SOURCE_PAYMENT_INGESTION_LOSS` and `NORMAL_BUSINESS_PAYMENT_DECLINE` to the shared ontology. Update both prompts with the generic evidence rule and insufficient alternative. Bump Static and Kernel prompt versions to `p1.static.v5` and `p1.kernel.v5`, and controller protocol to `p1.controller.v4`. Keep the base safety prompt and Diagnosis field names unchanged.

- [ ] **Step 4: Give Kernel only the public alert facts it needs.**

Pass `IncidentBrief.logical_observed_at` and sanitized observations into `DiagnosticKernel.start`. Do not import `scenarios.py`. Implement a generic `_silent_drop_root_supported` that parses `CURRENT_PERIOD_COUNT`, `EXPECTED_PERIOD_COUNT`, and `SETTLED_PAYMENT_WINDOW_END`, then checks compatible public EvidenceRecords. It must not branch on case ID, run ID, filename, frozen row ID, fixed healthy total, or test order.

- [ ] **Step 5: Implement current-partition SLA versus historical-range health.**

Use this semantic branch in both Kernel and evaluator:

```python
is_current_partition = current.bucket == history_series.watermark_value
if is_current_partition:
    require_declared_sla_and_logical_lag()
else:
    require_prior_same_period_range()
```

The same health claim still requires successful-run, matching current profile, matching history, exact alert target, and exact current point.

- [ ] **Step 6: Extend the private evaluator independently.**

Evaluator code may inspect `DeletePaymentRowsMutation` and exact ScenarioSpec values. It must require the exact public/private facts for `SOURCE_PAYMENT_INGESTION_LOSS`, validate affected assets only through compatible lineage EvidenceRecords, and keep B's exact gap/trace checks. Do not reuse Ground Truth inside Kernel/controller.

- [ ] **Step 7: Make all focused tests GREEN and re-run isolation.**

```powershell
uv run pytest tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_evaluation.py tests/unit/test_p1_isolation.py -q -p no:cacheprovider
uv run ruff check src/data_incident_gym/profiles.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/diagnostic_kernel.py src/data_incident_gym/evaluation.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_evaluation.py --no-cache
```

Expected: all pass and no leakage finding.

- [ ] **Step 8: Commit the diagnosis/evaluator slice when authorized.**

```powershell
git add src/data_incident_gym/profiles.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/diagnostic_kernel.py src/data_incident_gym/evaluation.py src/data_incident_gym/prompts/static_skill.md src/data_incident_gym/prompts/diagnostic_kernel.md tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_evaluation.py
git diff --cached --check
git commit -m "feat: gate silent loss and SLA health"
```

## Task 4: Close the 34-cell deterministic main-policy matrix

**Files:**

- Modify: `tests/e2e/test_p1_policy_matrix.py`
- Read only: `src/data_incident_gym/artifacts.py`
- Read only: `src/data_incident_gym/evaluation_runner.py`
- Read only: `tests/unit/test_artifacts.py`
- Read only: `tests/unit/test_evaluation_runner.py`

- [ ] **Step 1: Extend the existing FunctionModel seam, not a second matrix.**

Add `P1_M11_SCENARIO_IDS` to `MATRIX_CASES`, assert exactly 17 cases/34 cells, and implement scripted evidence acquisition within the existing shared `8/8/2/300` limits:

- silent confirmable: run, downstream lineage, payment profile, order profile, payment history, order history, and optional schema distractor;
- silent insufficient: the same current evidence plus exactly one failed attempt for each blocked history;
- SLA health: run, order profile, and order history.

- [ ] **Step 2: Assert exact diagnoses and six-file bundles.**

For both main strategies assert terminal/root/assets/gaps, Kernel final state, evidence compatibility, recovery, and exact `ARTIFACT_FILENAMES`. Keep Static and Kernel tool schema hashes equal.

- [ ] **Step 3: Run the exact cumulative matrix.**

```powershell
uv run pytest tests/e2e/test_p1_policy_matrix.py --collect-only -q -p no:cacheprovider
uv run pytest tests/e2e/test_p1_policy_matrix.py -q -p no:cacheprovider
```

Expected: exactly 34 collected and 34 passed. Any Windows native dbt crash is recorded separately and rerun only on Ubuntu exact HEAD; a Python assertion failure is a product failure and must be fixed before proceeding.

- [ ] **Step 4: Recheck artifact/evaluation runner compatibility.**

```powershell
uv run pytest tests/unit/test_artifacts.py tests/unit/test_evaluation_runner.py -q -p no:cacheprovider
```

Expected: pass; six filenames and existing development-run `benchmark_manifest_sha256=null` behavior remain unchanged.

- [ ] **Step 5: Commit the matrix slice when authorized.**

```powershell
git add tests/e2e/test_p1_policy_matrix.py
git diff --cached --check
git commit -m "test: close the 34-cell P1 matrix"
```

## Task 5: Implement only the frozen auxiliary benchmark policies

**Files:**

- Modify: `src/data_incident_gym/diagnosis.py`
- Modify: `src/data_incident_gym/diagnostic_agent.py`
- Create: `src/data_incident_gym/prompts/no_tool.md`
- Create: `src/data_incident_gym/fixed_rule.py`
- Modify: `src/data_incident_gym/evaluation.py`
- Modify: `src/data_incident_gym/artifacts.py`
- Modify: `tests/unit/test_diagnosis.py`
- Modify: `tests/unit/test_diagnostic_agent.py`
- Create: `tests/unit/test_fixed_rule.py`
- Modify: `tests/unit/test_policy_fairness.py`

- [ ] **Step 1: Add RED taxonomy and surface tests.**

Freeze these six execution strategies:

```python
STATIC_SKILL
DIAGNOSTIC_KERNEL
NO_TOOL
KERNEL_NO_LINEAGE
KERNEL_NO_SCHEMA
FIXED_RULE
```

Also freeze:

```python
MAIN_STRATEGIES = (STATIC_SKILL, DIAGNOSTIC_KERNEL)
MODEL_STRATEGIES = (STATIC_SKILL, DIAGNOSTIC_KERNEL, NO_TOOL, KERNEL_NO_LINEAGE, KERNEL_NO_SCHEMA)
KERNEL_STRATEGIES = (DIAGNOSTIC_KERNEL, KERNEL_NO_LINEAGE, KERNEL_NO_SCHEMA)
```

Ordinary CLI tests must still expose only Static and Kernel.

- [ ] **Step 2: Parameterize the existing model runner narrowly.**

Use exact registered tool sets:

```text
STATIC_SKILL: all six
DIAGNOSTIC_KERNEL: all six
NO_TOOL: none
KERNEL_NO_LINEAGE: all except get_dbt_lineage
KERNEL_NO_SCHEMA: all except get_relation_schema
```

Main policies retain identical prompt-independent tool schemas. Kernel-state validation applies to all three Kernel strategies. `NO_TOOL` uses `p1.no-tool.v1`, receives the shared public brief/runtime and Diagnosis schema, and has no hidden evidence channel. All model strategies retain the same model, provider defaults, structured validation rules, and `8/8/2/300` ceilings; one manifest cell remains one real-model run even if the controller internally uses more than one provider request within that ceiling.

- [ ] **Step 3: Implement one public-evidence-only fixed rule runner.**

`FixedRuleRunner` must use `ObservableRunContext` and `EvidenceTools`, never ScenarioSpec/Ground Truth. It calls at most eight tools and applies this explicit priority:

1. Read run results.
2. For failed runs, read node error/upstream lineage and any observable source schema/profile; map only compatible schema-type/rename or source-NULL facts, otherwise refuse.
3. For successful runs, read all alert-subject current profiles/histories and downstream lineage; map positive declared key/fingerprint duplicates, settled orphan, settled silent loss, or the same health range/SLA predicate, otherwise refuse.
4. A blocked decisive fact yields `INSUFFICIENT_EVIDENCE`, never a guessed confirmation or health result.

It emits a normal `DiagnosisRunResult`, trace, evidence inventory, policy identity `p1.fixed-rule.v1`, zero model/tokens, and the same evaluator/artifact contract.

- [ ] **Step 4: Prove no private routing and fair main surfaces.**

Tests must assert no auxiliary or fixed-rule implementation imports `scenarios.py`, contains case IDs/private values, or becomes available through ordinary `diagnose`/`eval run`. Main Static/Kernel identities, budgets, tool schemas, and output schemas stay equal except for their approved strategy prompt/controller hashes.

- [ ] **Step 5: Run focused tests.**

```powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_fixed_rule.py tests/unit/test_policy_fairness.py tests/unit/test_p1_isolation.py -q -p no:cacheprovider
```

Expected: pass with zero external request.

- [ ] **Step 6: Commit the auxiliary-policy slice when authorized.**

```powershell
git add src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/prompts/no_tool.md src/data_incident_gym/fixed_rule.py src/data_incident_gym/evaluation.py src/data_incident_gym/artifacts.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_fixed_rule.py tests/unit/test_policy_fairness.py tests/unit/test_p1_isolation.py
git diff --cached --check
git commit -m "feat: add frozen benchmark controls"
```

## Task 6: Build the immutable Benchmark Manifest contract

**Files:**

- Create: `src/data_incident_gym/benchmark_manifest.py`
- Create: `tests/unit/test_benchmark_manifest.py`
- Modify: `src/data_incident_gym/diagnostic_agent.py`
- Modify: `src/data_incident_gym/artifacts.py`
- Modify: `src/data_incident_gym/evaluation_runner.py`
- Modify: `src/data_incident_gym/lab.py`
- Modify: `tests/unit/test_artifacts.py`
- Modify: `tests/unit/test_evaluation_runner.py`
- Create later at the freeze gate: `config/benchmark/p1-formal-v1.json`

- [ ] **Step 1: Write RED manifest-schema and denominator tests.**

Define strict frozen models for manifest identity, sanitized model configuration, budget, contract hashes, scenario catalog, policy identities, and run cells. Reject duplicate JSON keys, unknown fields, non-contiguous sequence numbers, run-ID mismatch/collision, wrong scenario roles, wrong counts, changed order, secrets, missing hashes, a dirty result-input set, or an existing output path.

- [ ] **Step 2: Extract pure policy-identity construction.**

Factor only enough of `DiagnosisRunner` to compute prompt/controller/tool/output-schema identities without a run context or network call. Runtime runners and manifest builder must use the same function; do not copy hash formulas.

- [ ] **Step 3: Implement the exact schedule generator.**

Generate all 106 cells using the approved order/rotation/alternation rules and run-ID formula. Validation must assert:

```text
total=106
model_backed=94
fixed_rule=12
main=72
no_tool=12
kernel_no_lineage=5
kernel_no_schema=5
unique_run_ids=106
```

- [ ] **Step 4: Freeze all result-affecting identities.**

Manifest content includes all 17 ScenarioSpec digests, the 12 formal IDs, ProfileSpec digest/version, ScenarioSpec/Diagnosis JSON-schema hashes, evaluator version/hash, prompt/controller/tool-schema hashes for every strategy, `mimo-v2.5`, sanitized endpoint, empty model-setting overrides (the currently effective provider defaults), exact budgets, implementation revision, and explicit sequence.

No API key, database password, user path, environment dump, or Ground Truth body enters the manifest.

- [ ] **Step 5: Propagate the manifest digest to every formal six-file bundle.**

Add an optional, validated `benchmark_manifest_sha256` input to `EvaluationRunner`/`ArtifactRun`/`ArtifactWriter`. Development runs continue to write `null`; formal cells must write the exact non-null digest. Also allow `IncidentLab.build`/`EvaluationRunner.run` to accept the predeclared 32-hex run ID instead of generating a replacement.

- [ ] **Step 6: Run focused tests without generating the formal file yet.**

```powershell
uv run pytest tests/unit/test_benchmark_manifest.py tests/unit/test_artifacts.py tests/unit/test_evaluation_runner.py tests/unit/test_diagnostic_agent.py -q -p no:cacheprovider
```

Expected: pass; no `config/benchmark/p1-formal-v1.json` exists until the exact implementation revision and Ubuntu gate are known.

- [ ] **Step 7: Commit the manifest machinery when authorized.**

```powershell
git add src/data_incident_gym/benchmark_manifest.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/artifacts.py src/data_incident_gym/evaluation_runner.py src/data_incident_gym/lab.py tests/unit/test_benchmark_manifest.py tests/unit/test_artifacts.py tests/unit/test_evaluation_runner.py tests/unit/test_diagnostic_agent.py
git diff --cached --check
git commit -m "feat: define the P1 benchmark manifest"
```

## Task 7: Make M12 an execution-only milestone

**Files:**

- Create: `src/data_incident_gym/benchmark_runner.py`
- Modify: `src/data_incident_gym/doctor.py`
- Modify: `src/data_incident_gym/cli.py`
- Modify: `src/data_incident_gym/evaluation_runner.py`
- Modify: `src/data_incident_gym/diagnosis.py`
- Modify: `src/data_incident_gym/templates/report.md.j2`
- Create: `tests/unit/test_benchmark_runner.py`
- Modify: `tests/unit/test_doctor.py`
- Modify: `tests/unit/test_cli.py`
- Modify: `tests/unit/test_evaluation_runner.py`

- [ ] **Step 1: Write RED one-pass runner tests.**

With fake doctor, fake lab, FunctionModel, and temporary files, prove:

- manifest/hash/revision/input-digest verification precedes doctor;
- complete doctor JSON is saved outside single-run directories;
- doctor failure creates no `STARTED` cell and makes zero diagnosis calls;
- every cell uses its predeclared run ID, fresh runner/session/state, reset, pollution check, build, diagnosis, restore, evaluation, and six files;
- `STARTED` is append-only before work; `COMPLETED` or `FAILED` is append-only after work;
- a previously `STARTED` but incomplete cell is materialized once as `RUN_SETUP_ERROR`, included, and never retried;
- completed/failed IDs cannot rerun or be replaced;
- manifest exhaustion is the only successful terminal;
- fixed-rule cells make zero model calls and all model cells total exactly 94.

- [ ] **Step 2: Materialize setup failures into the same artifact contract.**

Add `RUN_SETUP_ERROR` as the one fixed safe execution-error summary. Build a synthetic, policy-identity-bound `DiagnosisRunResult` with zero requests/tokens and empty evidence. Kernel variants receive a terminated Kernel state and normal terminal trace. The private evaluator marks the run failed; recovery status remains truthful. Do not drop the cell because reset/build/model setup failed.

- [ ] **Step 3: Implement the minimal append-only suite ledger.**

Store suite files under `artifacts/benchmarks/p1-formal-v1/`, outside `artifacts/<run_id>/`. Use exclusive creation and one local lock; reject symlinks/path escape. The ledger records only manifest cell identity, state, timestamps, artifact path, and fixed reason code—no prompt/completion or hidden reasoning.

- [ ] **Step 4: Finish P1 doctor checks without running them in M11.**

Extend doctor so a complete result proves:

- final ProfileSpec and healthy snapshot hashes/versions match;
- both aggregate backends can read all declared healthy relations under the diagnostic read-only role;
- arbitrary relation/identifier and undeclared metric probes fail safely;
- `raw_orders.order_count_by_day` has watermark `order_date`, parseable current watermark, and SLA `86400`;
- main tool schema is exactly six and no SQL argument exists.

Keep the real endpoint/model/tool probe, but invoke it only at authorized M12 startup. Unit tests inject fakes.

- [ ] **Step 5: Add guarded benchmark CLI commands.**

Expose:

```text
data-incident-gym benchmark freeze
data-incident-gym benchmark verify
data-incident-gym benchmark run
```

`benchmark run` requires the manifest path and exact SHA-256 confirmation, refuses a non-clean/formally invalid checkout, and has no retry/replace flags. Ordinary `diagnose` and `eval run` retain only two main strategy choices.

- [ ] **Step 6: Run the runner/doctor/CLI tests with no network.**

```powershell
uv run pytest tests/unit/test_benchmark_runner.py tests/unit/test_doctor.py tests/unit/test_cli.py tests/unit/test_evaluation_runner.py -q -p no:cacheprovider
```

Expected: pass; fake counters prove zero real endpoint/model request.

- [ ] **Step 7: Commit the execution-readiness slice when authorized.**

```powershell
git add src/data_incident_gym/benchmark_runner.py src/data_incident_gym/doctor.py src/data_incident_gym/cli.py src/data_incident_gym/evaluation_runner.py src/data_incident_gym/diagnosis.py src/data_incident_gym/templates/report.md.j2 tests/unit/test_benchmark_runner.py tests/unit/test_doctor.py tests/unit/test_cli.py tests/unit/test_evaluation_runner.py
git diff --cached --check
git commit -m "feat: enforce one-pass benchmark execution"
```

## Task 8: Verify, document, freeze, and stop before M12

**Files:**

- Modify: `README.md`
- Create only after exact-head Ubuntu CI: `config/benchmark/p1-formal-v1.json`

- [ ] **Step 1: Update README with bounded status only.**

State that M11 deterministic infrastructure reaches 17/17 and that `p1-formal-v1` is pending or frozen according to actual state. State explicitly that the 94 real-model runs have not started, fixed-rule results and aggregate metrics do not yet exist, M7's historical 1/8 observation is not the P1 benchmark, and no strategy advantage is claimed.

- [ ] **Step 2: Run all local non-real-model gates.**

```powershell
uv run ruff check . --no-cache
uv run pytest tests/unit -q -p no:cacheprovider
uv run pytest tests/integration -q -p no:cacheprovider
uv run pytest tests/e2e -m 'not real_model' -q -p no:cacheprovider
uv run pytest tests/e2e/test_p1_policy_matrix.py --collect-only -q -p no:cacheprovider
uv run pytest tests/e2e/test_real_model_m7_smoke.py --collect-only -q -p no:cacheprovider
uv lock --check
git diff --check
uv build
```

Expected: Ruff/lock/diff/build green, all unit/integration/non-real E2E business assertions green, exactly 34 matrix cells, exactly eight M7 smoke cells, and zero real-model execution. Record any Windows native crash separately; do not change product behavior merely to hide `0xC0000005`-class environment evidence.

- [ ] **Step 3: Prove final database and repository health.**

Read-only checks must show 113 payments, 99 orders, all frozen deleted rows restored, zero order-without-payment violations, no orphan/duplicate residue, clean submodule, empty index except intentional staging, and no generated artifacts staged.

- [ ] **Step 4: Run a result-input/leakage self-review.**

```powershell
rg -n 'silent_payment_drop_|89|92|111|TEST_CONFIRMABLE|TEST_INSUFFICIENT|answerability|expected_status|config/scenarios|\.dig/lab/private' src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/diagnostic_kernel.py src/data_incident_gym/fixed_rule.py src/data_incident_gym/prompts
git diff ae94dbc...HEAD -- src/data_incident_gym config/scenarios config/profiles tests README.md docs/superpowers/plans/2026-09-01-m11-silent-payment-drop-benchmark-freeze.md
```

Expected: no private diagnosis-plane match and every changed line traces to this plan.

- [ ] **Step 5: Commit documentation and establish the implementation revision.**

```powershell
git add README.md
git diff --cached --check
git commit -m "docs: mark M11 benchmark freeze readiness"
$implementationRevision = git rev-parse HEAD
$implementationRevision
git status --short --branch
```

Expected: exact 40-hex implementation revision, empty index, only pre-existing user materials unstaged. Do not amend result-affecting code after this point.

- [ ] **Step 6: Obtain explicit push authorization and exact-head Ubuntu CI.**

Push only if the user separately authorizes it. Then inspect the exact workflow/jobs and require Ubuntu unit, integration, and non-real E2E—including 34/34 matrix—to pass on `$implementationRevision`. A 403 reading annotations is not a CI failure if the run/jobs themselves are green.

If CI reveals a product defect, fix it under the smallest relevant earlier task, rerun gates, create a new implementation revision, and repeat CI. Do not generate `p1-formal-v1` before the implementation revision is green.

- [ ] **Step 7: Freeze `p1-formal-v1` without invoking a model.**

After exact-head CI is green, run:

```powershell
uv run data-incident-gym benchmark freeze --manifest-id p1-formal-v1 --implementation-revision $implementationRevision --output config/benchmark/p1-formal-v1.json
uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v1.json
```

Expected: exclusive creation of one manifest; verification reports 17 catalog scenarios, 12 formal scenarios, 106 unique cells, 94 model-backed cells, 12 fixed-rule cells, and all frozen hashes. These commands perform no endpoint/model request.

- [ ] **Step 8: Commit the manifest alone and validate the packaging boundary.**

```powershell
git add config/benchmark/p1-formal-v1.json
git diff --cached --name-status
git diff --cached --check
git commit -m "chore: freeze P1 benchmark manifest"
git diff --name-only $implementationRevision..HEAD
```

Expected: the only path after the implementation revision is `config/benchmark/p1-formal-v1.json`. Do not amend or overwrite the manifest.

- [ ] **Step 9: Obtain separate push authorization for the packaging commit and require final Ubuntu CI.**

After an authorized push, require CI green on the packaging commit and re-run `benchmark verify` in a clean checkout. If the manifest or result inputs drift, stop; generate `p1-formal-v2` only after a separately reviewed fix. Never edit v1 in place.

- [ ] **Step 10: Stop at the M12 release gate.**

Final handoff must report:

- implementation revision and packaging revision;
- Ubuntu run ID and job results;
- exact local test counts and any environment-unverified Windows evidence;
- final database fingerprint/counts and submodule state;
- `p1-formal-v1.json` path and SHA-256;
- manifest counts `106/94/12/72/12/5/5`;
- confirmation that no real model, doctor probe, fixed-rule formal cell, aggregate report, commit amendment, replacement run, or M12 execution occurred.

Do not run:

```powershell
$manifestSha256 = (Get-FileHash 'config/benchmark/p1-formal-v1.json' -Algorithm SHA256).Hash.ToLowerInvariant()
uv run data-incident-gym benchmark run --manifest config/benchmark/p1-formal-v1.json --confirm-sha256 $manifestSha256
```

That command is shown only to identify the M12 boundary. Its actual digest and execution require a later explicit user authorization.

## Final self-review checklist

- [ ] Scope is exactly four M11 scenarios plus the already-approved formal freeze/execution prerequisites.
- [ ] P1 is exactly 17/17; formal test set is exactly 12 and excludes all development/regression cases.
- [ ] Test twins differ only in evidence availability and private answerability fields.
- [ ] No private row ID, case ID, expected status, or Ground Truth enters any diagnosis policy, fixed rule, tool, or controller.
- [ ] Main allowlist is still exactly six; no SQL/raw-row/write/network tool was added.
- [ ] Static and Kernel main policies retain equal environmental facts and budgets.
- [ ] SLA uses logical incident time and current-watermark-bucket semantics, not EvidenceRecord wall time or historical fallback.
- [ ] Silent-loss confirmation requires positive compatible evidence, not dbt success or low volume alone.
- [ ] All formal run IDs/order/arms/hashes are predeclared and immutable.
- [ ] Formal progress cannot retry, replace, or omit a started cell.
- [ ] All formal model-backed outcomes can produce six files even on setup/model/protocol failure.
- [ ] M11 ran zero real-model calls and made no Kernel-advantage claim.
- [ ] User-owned materials and generated artifacts remain unstaged; submodule remains unchanged.
- [ ] Push and M12 remain separate approval gates.
