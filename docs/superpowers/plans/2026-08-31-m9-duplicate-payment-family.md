# M9 Duplicate-Payment Fault Family Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Do not delegate work unless the user explicitly authorizes delegation for that execution turn.

**Goal:** Add exactly the three duplicate-payment scenarios promised by M9, including the first dbt-success/data-anomaly path, and carry them through deterministic injection, private verification, both diagnosis strategies, deterministic evaluation, recovery, and the six-file artifact contract, bringing P1 from 7/17 to 10/17.

**Architecture:** Extend the existing P1 vertical slice in place. A closed DUPLICATE_PAYMENT_ROWS mutation inserts only two frozen payment batches and removes only the inserted multiplicity. The existing ProfileSpec remains unchanged: business-key duplicates, business-fingerprint duplicates, and payment-method groups provide all public aggregate facts. M9 adds an EXPECTED_ANOMALY verification path for incidents whose dbt build succeeds, while preserving the current EXPECTED_FAILURE and HEALTHY_CONTROL paths.

**Tech Stack:** Python 3.12, Pydantic v2, psycopg 3 SQL composition, dbt Core/PostgreSQL Jaffle Shop fixture, PydanticAI FunctionModel, pytest, Ruff, uv, PowerShell 7.

---

## 0. Approved scope and fixed decisions

### Planning baseline and M8 audit result

- This plan was written against local master at 443c4874e12b2c561b0dc4552ddfe3c9a7fe1f22, six commits ahead of its configured upstream.
- M8 is accepted as directionally correct and deterministically complete: 7/17 scenarios, 14/14 deterministic policy cells, 166/166 unit tests, Ruff, lock validation, and an exact eight-item collect-only M7 real-model smoke were independently rechecked.
- M8 has one narrow release defect: IncidentLab._write_null_target checks cursor.rowcount after leaving connection.transaction(). An unexpected multi-row update would commit and then raise. Task 0 moves that guard inside the transaction and proves rollback signalling. No other M8 redesign or retuning is authorized.
- The existing modified docs/requirements.md and user-owned untracked AGENTS.md, decision.md, earlier plans, and reports remain outside every M9 commit unless the user separately changes that boundary.
- No M8 or M9 real-model run is authorized by this plan.

### Exact M9 scenario matrix

| case_id | role | injected data | dbt result | exact affected model set | decisive public evidence | expected terminal |
|---|---|---|---|---|---|---|
| duplicate_payment_record | DEV_CONFIRMABLE | Insert a second byte-identical raw_payments row (1, 1, credit_card, 1000), including duplicate id=1 | unique_stg_payments_payment_id fails | model.jaffle_shop.stg_payments | id business-key duplicate_count=1 and fingerprint duplicate_count=1 | CONFIRMED |
| duplicate_payment_coupon_a | TEST_CONFIRMABLE | Copy source ids 47, 66, and 86 to new ids 114, 115, and 116; all three copies remain coupon payments with identical business fingerprints to their source rows | dbt succeeds with no failed or skipped nodes | model.jaffle_shop.customers, model.jaffle_shop.orders, model.jaffle_shop.stg_payments | id duplicate_count=0, order_payment_amount duplicate_count=3, coupon group count=16, and downstream lineage | CONFIRMED |
| duplicate_payment_coupon_b | TEST_INSUFFICIENT | Byte-for-byte the same payment batch and nullable source_batch_note distractor as variant A | dbt succeeds with no failed or skipped nodes | same three models as variant A | raw_payments profile is blocked and payment event identity is not observable | INSUFFICIENT_EVIDENCE |

The test pair must share the exact same IncidentBrief, mutations, database state, direct_failure=null, affected assets, distractor, and dbt result. It may differ only in private accepted explanations, observable raw_payments profile access, unresolved gaps, required public evidence types, answerability, and terminal status.

### Frozen source and inserted rows

The healthy fixture has 113 raw_payments rows and no duplicate id or order_payment_amount fingerprint.

| mode | source rows already present | inserted rows | injected row count | expected profile facts |
|---|---|---|---:|---|
| EXACT_RECORD | (1, 1, credit_card, 1000) | (1, 1, credit_card, 1000) | 114 | id=1; order_payment_amount=1; credit_card=56 |
| SEMANTIC_FINGERPRINT | (47, 42, coupon, 1700), (66, 58, coupon, 1800), (86, 76, coupon, 200) | (114, 42, coupon, 1700), (115, 58, coupon, 1800), (116, 76, coupon, 200) | 116 | id=0; order_payment_amount=3; coupon=16 |

No other source id, inserted id, payment method, amount, relation, row list, or mutation mode is accepted.

### Root-cause ontology added by M9

- SOURCE_EXACT_PAYMENT_DUPLICATE: an upstream payment record is duplicated including its business key.
- SOURCE_SEMANTIC_PAYMENT_DUPLICATE: upstream payment ids remain unique, but one or more declared business fingerprints repeat.
- LEGITIMATE_SPLIT_PAYMENT: repeated amount/channel activity can be a valid split payment when event identity is unavailable.

The development case accepts only SOURCE_EXACT_PAYMENT_DUPLICATE. Variant A accepts only SOURCE_SEMANTIC_PAYMENT_DUPLICATE. Variant B privately records SOURCE_SEMANTIC_PAYMENT_DUPLICATE and LEGITIMATE_SPLIT_PAYMENT as the two compatible explanations but returns no root-cause or affected-asset claim.

### Evidence boundary

| role | observable decisive facts | deliberately unavailable facts | required cited evidence types |
|---|---|---|---|
| DEV_CONFIRMABLE | failed unique test, upstream lineage, source schema, raw_payments profile with duplicate id and fingerprint | none needed after the source duplicate is observed | DBT_RUN_RESULTS, DBT_NODE_ERROR, DBT_LINEAGE, RELATION_SCHEMA, RELATION_DATA_PROFILE |
| TEST_CONFIRMABLE | successful dbt run, downstream lineage from seed.jaffle_shop.raw_payments, harmless nullable-column schema drift, raw_payments aggregate duplicate and channel facts | raw event rows remain unavailable, but the frozen business-fingerprint contract is sufficient | DBT_RUN_RESULTS, DBT_LINEAGE, RELATION_SCHEMA, RELATION_DATA_PROFILE |
| TEST_INSUFFICIENT | successful dbt run, downstream lineage, and the same harmless schema drift | raw_payments profile returns RELATION_NOT_ALLOWED; payment idempotency/channel event identity is NOT_OBSERVABLE | DBT_RUN_RESULTS, DBT_LINEAGE, RELATION_SCHEMA |

Variant B must emit exactly:

~~~json
[
  {
    "evidence_kind": "RELATION_DATA_PROFILE",
    "subject": "raw_payments",
    "reason_code": "RELATION_NOT_ALLOWED"
  },
  {
    "evidence_kind": "PAYMENT_EVENT_IDENTITY",
    "subject": "raw_payments",
    "reason_code": "NOT_OBSERVABLE"
  }
]
~~~

PAYMENT_EVENT_IDENTITY is a typed declaration of missing evidence, not a seventh tool. The model may declare it only for an IncidentBrief subject and cannot fabricate an EvidenceRecord for it.

### Required behavior changes

1. A non-health incident may be privately verified as EXPECTED_ANOMALY when dbt succeeds, no node fails or skips, the exact frozen data mutation is present, and the declared downstream model set matches manifest lineage.
2. Successful dbt execution is evidence about pipeline execution, not proof of NO_INCIDENT. Both policies must inspect the public incident signal and aggregate profile before choosing a terminal.
3. SOURCE_EXACT_PAYMENT_DUPLICATE requires a positive declared id business-key duplicate count.
4. SOURCE_SEMANTIC_PAYMENT_DUPLICATE requires zero id duplicates and a positive declared order_payment_amount fingerprint duplicate count.
5. Affected assets for a dbt-success source anomaly are supported by downstream lineage from the incident seed node.
6. Variant B requires one blocked raw_payments profile attempt plus the non-tool PAYMENT_EVENT_IDENTITY declaration.
7. ProfileSpec, the six evidence tools, budgets, Diagnosis shape, and six artifact filenames remain unchanged.

### Non-goals

- No raw-row evidence tool, arbitrary SQL, seventh evidence tool, event-log connector, new ProfileSpec field, case-ID dispatch, write-capable diagnosis tool, automatic repair, or user-defined mutation DSL.
- No edit to config/profiles/jaffle_shop.v1.json or the pinned Jaffle Shop submodule.
- No custom dbt test for semantic duplicate payments. The dbt-success anomaly path is intentional and is needed by later P1 data-quality families.
- No change to the shared 8 model requests, 8 business tool calls, 2 output retries, or 300-second timeout.
- No change to p1.diagnosis.v1 field names or the six artifact filenames.
- No controller tuning based on M7 real-model failures, no raised budget, no retry, no M8 or M9 real-model smoke, and no formal 94-run benchmark.
- No ten-cycle M9 loop on Windows. One real integration path per new scenario and the cumulative deterministic policy matrix are the M9 acceptance evidence. Existing long-loop native failures remain environment-unverified.
- No update to docs/requirements.md; sections 12.3 and 17 already define M9 sufficiently.
- No push. Commit and push remain separate authorization gates.

### Acceptance criteria

- Exactly three M9 scenarios load, and P1_M7_SCENARIO_IDS + P1_M8_SCENARIO_IDS + P1_M9_SCENARIO_IDS contains exactly 10 cases.
- The development case injects one exact duplicate, fails only test.jaffle_shop.unique_stg_payments_payment_id.3744510712, and recovers to 113 healthy payment rows.
- The two test variants inject the exact same three coupon copies and source_batch_note distractor; dbt succeeds with no failed or skipped node.
- Private verification proves the exact inserted multiset, aggregate duplicate counts, channel count, public profile scope, downstream affected models, and recovery.
- Variant A confirms SOURCE_SEMANTIC_PAYMENT_DUPLICATE from successful-run, profile, and lineage evidence.
- Variant B can pass only as INSUFFICIENT_EVIDENCE with the two exact unresolved declarations.
- Static Skill and Diagnostic Kernel retain the same six tools, public context, budgets, ontology, Diagnosis schema, evaluator, and artifact contract.
- The cumulative deterministic matrix is exactly 10 cases x 2 strategies = 20 passing cells, with six canonical artifacts per cell.
- The M7 real-model smoke remains collect-only and exactly eight items.
- Unit, integration, non-real-model E2E, Ruff, lock, diff, and build checks pass, subject only to the already documented Windows/dbt native-instability boundary.

## 1. Deep-module map

- src/data_incident_gym/scenarios.py owns the closed M9 catalog, duplicate-batch allowlist, test-twin contract, and unresolved evidence boundary.
- src/data_incident_gym/lab.py owns transaction-safe insert/delete operations and lifecycle state checks.
- src/data_incident_gym/lab_verifier.py independently proves the exact live multiset, dbt-failure or dbt-success anomaly mode, manifest-derived impact, and public profile scope.
- src/data_incident_gym/profiles.py and config/profiles/jaffle_shop.v1.json already expose every required aggregate fact and must not change.
- src/data_incident_gym/diagnosis.py owns the PAYMENT_EVENT_IDENTITY unresolved-evidence vocabulary.
- src/data_incident_gym/diagnostic_agent.py and both prompt files own the shared ontology and policy-neutral duplicate-payment reasoning.
- src/data_incident_gym/diagnostic_kernel.py owns public-subject provenance, successful-run root gates, claim-evidence compatibility, and typed missing-evidence binding.
- src/data_incident_gym/evaluation.py independently compares public evidence with the private duplicate mutation and EXPECTED_ANOMALY verification.
- tests/e2e/test_p1_policy_matrix.py remains the single cumulative deterministic policy seam.

## Task 0: Close the M8 transaction atomicity finding

**Files:**

- Modify: src/data_incident_gym/lab.py
- Modify: tests/unit/test_lab.py
- Include when implementation is committed: docs/superpowers/plans/2026-08-31-m9-duplicate-payment-family.md

- [ ] **Step 1: Make the existing fake transaction expose whether an exception crossed its boundary.**

Add this focused test helper:

~~~python
class _FakeTransaction:
    def __init__(self) -> None:
        self.saw_exception = False

    def __enter__(self) -> "_FakeTransaction":
        return self

    def __exit__(self, exc_type, *_: object) -> None:
        self.saw_exception = exc_type is not None


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self.cursor_instance = cursor
        self.transaction_instance = _FakeTransaction()

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance

    def transaction(self) -> _FakeTransaction:
        return self.transaction_instance
~~~

Then extend test_null_mutation_helpers_reject_non_unique_selector_or_update:

~~~python
connection = _FakeConnection(cursor)
lab.db_connect = lambda **_: connection

with pytest.raises(InvalidIncidentState, match="恰好一行"):
    lab._write_null_target(
        mutation,
        expected_current=mutation.expected_value,
        replacement=None,
    )

assert connection.transaction_instance.saw_exception is True
~~~

- [ ] **Step 2: Run the regression and observe RED.**

~~~powershell
uv run pytest tests/unit/test_lab.py::test_null_mutation_helpers_reject_non_unique_selector_or_update -q
~~~

Expected: FAIL because the row-count exception currently occurs after the transaction context has exited.

- [ ] **Step 3: Move the row-count guard into the transaction.**

Replace the tail of IncidentLab._write_null_target with:

~~~python
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    statement,
                    (replacement, mutation.selector_value, expected_current),
                )
                if cursor.rowcount != 1:
                    raise InvalidIncidentState("NULL mutation 必须更新恰好一行")
        except LabError:
            raise
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(f"写入 NULL mutation 目标失败：{self._redact(str(exc))}")
            ) from None
~~~

- [ ] **Step 4: Run focused and complete unit checks.**

~~~powershell
uv run pytest tests/unit/test_lab.py -q
uv run pytest tests/unit -q
~~~

Expected: all tests pass; the invalid row count crosses the transaction boundary as an exception.

- [ ] **Step 5: Commit only the atomicity repair and this plan.**

~~~powershell
git add src/data_incident_gym/lab.py tests/unit/test_lab.py docs/superpowers/plans/2026-08-31-m9-duplicate-payment-family.md
git diff --cached --check
git commit -m "fix: keep null row guard inside transaction"
~~~

Do not stage docs/requirements.md, AGENTS.md, decision.md, earlier plans, or reports.

## Task 1: Freeze the 10/17 catalog and closed duplicate-payment mutation

**Files:**

- Modify: src/data_incident_gym/scenarios.py
- Create: config/scenarios/duplicate_payment_record.json
- Create: config/scenarios/duplicate_payment_coupon_a.json
- Create: config/scenarios/duplicate_payment_coupon_b.json
- Modify: tests/unit/test_scenarios.py
- Modify: tests/unit/test_p1_isolation.py

- [ ] **Step 1: Write RED catalog, allowlist, test-twin, and leakage tests.**

Add assertions equivalent to:

~~~python
def test_m9_catalog_and_test_twin_are_exact(project_root: Path) -> None:
    assert P1_M9_SCENARIO_IDS == (
        "duplicate_payment_record",
        "duplicate_payment_coupon_a",
        "duplicate_payment_coupon_b",
    )
    assert len(P1_M7_SCENARIO_IDS + P1_M8_SCENARIO_IDS + P1_M9_SCENARIO_IDS) == 10

    dev, confirmable, insufficient = (
        load_scenario_spec(case_id, project_root) for case_id in P1_M9_SCENARIO_IDS
    )
    assert dev.variant_role is VariantRole.DEV_CONFIRMABLE
    assert confirmable.variant_role is VariantRole.TEST_CONFIRMABLE
    assert insufficient.variant_role is VariantRole.TEST_INSUFFICIENT
    for field_name in (
        "incident_brief",
        "reset_and_injection_contract",
        "direct_failure",
        "affected_assets",
        "distractors",
    ):
        assert getattr(confirmable, field_name) == getattr(insufficient, field_name)
    assert confirmable.direct_failure is None
    assert insufficient.direct_failure is None
    assert confirmable.observable_evidence_contract.profile_relations == ("raw_payments",)
    assert insufficient.observable_evidence_contract.profile_relations == ()
    assert tuple(
        (gap.gap_kind, gap.subject, gap.reason_code, gap.tool_name)
        for gap in insufficient.observable_evidence_contract.unresolved_gaps
    ) == (
        (
            "RELATION_DATA_PROFILE",
            "raw_payments",
            "RELATION_NOT_ALLOWED",
            "get_relation_data_profile",
        ),
        ("PAYMENT_EVENT_IDENTITY", "raw_payments", "NOT_OBSERVABLE", None),
    )
~~~

Add rejection tests for an unknown source id, inserted id, mode, relation, reordered list, or arbitrary row payload. Add an isolation assertion that case ids, source ids, inserted ids, and expected row values never enter public context, either prompt, diagnostic_agent.py, diagnostic_kernel.py, or evaluation artifacts.

- [ ] **Step 2: Add the closed catalog, fault family, mutation, and row projection.**

Add:

~~~python
P1_M9_SCENARIO_IDS = (
    "duplicate_payment_record",
    "duplicate_payment_coupon_a",
    "duplicate_payment_coupon_b",
)
SUPPORTED_SCENARIO_IDS = (
    REGRESSION_SCENARIO_IDS
    + P1_M7_SCENARIO_IDS
    + P1_M8_SCENARIO_IDS
    + P1_M9_SCENARIO_IDS
)


class FaultFamily(StrEnum):
    SCHEMA_RENAME = "SCHEMA_RENAME"
    SCHEMA_TYPE_CHANGE = "SCHEMA_TYPE_CHANGE"
    REQUIRED_FIELD_NULL = "REQUIRED_FIELD_NULL"
    PAYMENT_DUPLICATE = "PAYMENT_DUPLICATE"
    ORDER_VOLUME_PATTERN = "ORDER_VOLUME_PATTERN"


PaymentRow = tuple[int, int, str, int]

_M9_SOURCE_PAYMENT_ROWS: dict[int, PaymentRow] = {
    1: (1, 1, "credit_card", 1000),
    47: (47, 42, "coupon", 1700),
    66: (66, 58, "coupon", 1800),
    86: (86, 76, "coupon", 200),
}
_M9_DUPLICATE_BATCHES = {
    ("EXACT_RECORD", (1,), (1,)),
    ("SEMANTIC_FINGERPRINT", (47, 66, 86), (114, 115, 116)),
}


class DuplicatePaymentRowsMutation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["DUPLICATE_PAYMENT_ROWS"]
    purpose: Literal["FAULT"]
    relation: Literal["raw_payments"]
    mode: Literal["EXACT_RECORD", "SEMANTIC_FINGERPRINT"]
    source_payment_ids: tuple[StrictInt, ...]
    inserted_payment_ids: tuple[StrictInt, ...]

    @model_validator(mode="after")
    def validate_frozen_batch(self) -> Self:
        key = (self.mode, self.source_payment_ids, self.inserted_payment_ids)
        if key not in _M9_DUPLICATE_BATCHES:
            raise ValueError("unsupported duplicate-payment batch")
        return self


def duplicate_payment_rows(
    mutation: DuplicatePaymentRowsMutation,
) -> tuple[tuple[PaymentRow, PaymentRow], ...]:
    pairs: list[tuple[PaymentRow, PaymentRow]] = []
    for source_id, inserted_id in zip(
        mutation.source_payment_ids,
        mutation.inserted_payment_ids,
        strict=True,
    ):
        source = _M9_SOURCE_PAYMENT_ROWS[source_id]
        inserted = (inserted_id, source[1], source[2], source[3])
        pairs.append((source, inserted))
    return tuple(pairs)
~~~

Add DuplicatePaymentRowsMutation to ScenarioMutation. Add PAYMENT_EVENT_IDENTITY to ObservableEvidenceGap.gap_kind and map it to tool_name=None. Keep strict extra="forbid" validation.

- [ ] **Step 3: Add the fault-family contract without weakening earlier families.**

Add a PAYMENT_DUPLICATE branch:

~~~python
        if self.fault_family is FaultFamily.PAYMENT_DUPLICATE:
            duplicate_mutations = tuple(
                mutation
                for mutation in mutations
                if isinstance(mutation, DuplicatePaymentRowsMutation)
            )
            nullable_distractors = tuple(
                mutation
                for mutation in mutations
                if isinstance(mutation, AddNullableColumnMutation)
            )
            if len(duplicate_mutations) != 1:
                raise ValueError("duplicate-payment scenarios require one frozen payment batch")
            is_test_role = self.variant_role in {
                VariantRole.TEST_CONFIRMABLE,
                VariantRole.TEST_INSUFFICIENT,
            }
            expected_mode = "SEMANTIC_FINGERPRINT" if is_test_role else "EXACT_RECORD"
            if duplicate_mutations[0].mode != expected_mode:
                raise ValueError("duplicate-payment role and mutation mode do not match")
            if len(nullable_distractors) != (1 if is_test_role else 0):
                raise ValueError("duplicate-payment test roles require one schema distractor")
            if is_test_role:
                if len(self.distractors) != 1 or not isinstance(
                    self.distractors[0],
                    NullableColumnSchemaDriftDistractor,
                ):
                    raise ValueError("duplicate-payment test roles need the declared distractor")
                if self.direct_failure is not None or not self.affected_assets:
                    raise ValueError("semantic duplicate scenarios are dbt-success anomalies")
            elif self.distractors:
                raise ValueError("duplicate-payment development role has no distractor")
        elif any(isinstance(item, DuplicatePaymentRowsMutation) for item in mutations):
            raise ValueError("DUPLICATE_PAYMENT_ROWS is only valid for payment duplicates")
~~~

Change the general insufficient check so direct_failure may be null only for PAYMENT_DUPLICATE while affected_assets remains mandatory:

~~~python
        if self.answerability is Answerability.INSUFFICIENT:
            if len(self.ground_truth_or_acceptable_root_causes) < 2:
                raise ValueError("insufficient scenarios require two compatible causes")
            if len(self.observable_evidence_contract.unresolved_gaps) < 2:
                raise ValueError("insufficient scenarios require decisive evidence gaps")
            if not self.affected_assets:
                raise ValueError("insufficient scenarios retain the observed impact scope")
            if (
                self.direct_failure is None
                and self.fault_family is not FaultFamily.PAYMENT_DUPLICATE
            ):
                raise ValueError("this insufficient scenario requires a direct dbt failure")
~~~

- [ ] **Step 4: Create the development ScenarioSpec exactly.**

Create config/scenarios/duplicate_payment_record.json:

~~~json
{
  "schema_version": "scenario.v1",
  "incident_case_id": "duplicate_payment_record",
  "suite": "P1",
  "fault_family": "PAYMENT_DUPLICATE",
  "variant_role": "DEV_CONFIRMABLE",
  "answerability": "CONFIRMABLE",
  "seed": {
    "schema_version": "seed.v1",
    "fixture_path": "third_party/jaffle_shop",
    "fixture_commit": "36bde6cba69d962b83be1d52fc65a0dce1cb4ebb",
    "seed_names": ["raw_customers", "raw_orders", "raw_payments"],
    "refresh": "FULL_REFRESH"
  },
  "incident_brief": {
    "schema_version": "incident_brief.v1",
    "signal_code": "DBT_TEST_FAILED",
    "summary": "A payment-id uniqueness test failed in the payment pipeline.",
    "subjects": [
      "test.jaffle_shop.unique_stg_payments_payment_id.3744510712",
      "raw_payments"
    ],
    "logical_observed_at": "2018-04-09T12:00:00Z",
    "observations": [
      {
        "kind": "DBT_TEST_FAILURE",
        "subject": "test.jaffle_shop.unique_stg_payments_payment_id.3744510712",
        "value": "fail"
      }
    ]
  },
  "reset_and_injection_contract": {
    "schema_version": "reset_injection.v1",
    "mutations": [
      {
        "kind": "DUPLICATE_PAYMENT_ROWS",
        "purpose": "FAULT",
        "relation": "raw_payments",
        "mode": "EXACT_RECORD",
        "source_payment_ids": [1],
        "inserted_payment_ids": [1]
      }
    ],
    "restore_strategy": "FULL_REFRESH_BASELINE"
  },
  "ground_truth_or_acceptable_root_causes": ["SOURCE_EXACT_PAYMENT_DUPLICATE"],
  "direct_failure": "test.jaffle_shop.unique_stg_payments_payment_id.3744510712",
  "affected_assets": ["model.jaffle_shop.stg_payments"],
  "observable_evidence_contract": {
    "schema_version": "observable_evidence.v1",
    "schema_relations": ["raw_payments"],
    "profile_relations": ["raw_payments"],
    "history_relations": [],
    "unresolved_gaps": []
  },
  "required_evidence_types": [
    "DBT_RUN_RESULTS",
    "DBT_NODE_ERROR",
    "DBT_LINEAGE",
    "RELATION_SCHEMA",
    "RELATION_DATA_PROFILE"
  ],
  "forbidden_leakage": [
    "SCENARIO_SPEC",
    "GROUND_TRUTH",
    "VARIANT_ROLE",
    "ANSWERABILITY",
    "EXPECTED_STATUS",
    "PRIVATE_PATH"
  ],
  "distractors": [],
  "expected_status": "CONFIRMED"
}
~~~

- [ ] **Step 5: Create the confirmable test ScenarioSpec exactly.**

Create config/scenarios/duplicate_payment_coupon_a.json:

~~~json
{
  "schema_version": "scenario.v1",
  "incident_case_id": "duplicate_payment_coupon_a",
  "suite": "P1",
  "fault_family": "PAYMENT_DUPLICATE",
  "variant_role": "TEST_CONFIRMABLE",
  "answerability": "CONFIRMABLE",
  "seed": {
    "schema_version": "seed.v1",
    "fixture_path": "third_party/jaffle_shop",
    "fixture_commit": "36bde6cba69d962b83be1d52fc65a0dce1cb4ebb",
    "seed_names": ["raw_customers", "raw_orders", "raw_payments"],
    "refresh": "FULL_REFRESH"
  },
  "incident_brief": {
    "schema_version": "incident_brief.v1",
    "signal_code": "PAYMENT_DUPLICATE_ALERT",
    "summary": "A payment retry alert was triggered for the coupon channel.",
    "subjects": ["seed.jaffle_shop.raw_payments", "raw_payments"],
    "logical_observed_at": "2018-04-09T12:00:00Z",
    "observations": [
      {
        "kind": "CHANNEL_PAYMENT_RETRY_ALERT",
        "subject": "raw_payments",
        "value": "coupon"
      }
    ]
  },
  "reset_and_injection_contract": {
    "schema_version": "reset_injection.v1",
    "mutations": [
      {
        "kind": "DUPLICATE_PAYMENT_ROWS",
        "purpose": "FAULT",
        "relation": "raw_payments",
        "mode": "SEMANTIC_FINGERPRINT",
        "source_payment_ids": [47, 66, 86],
        "inserted_payment_ids": [114, 115, 116]
      },
      {
        "kind": "ADD_NULLABLE_COLUMN",
        "relation": "raw_payments",
        "column": "source_batch_note",
        "data_type": "text",
        "nullable": true
      }
    ],
    "restore_strategy": "FULL_REFRESH_BASELINE"
  },
  "ground_truth_or_acceptable_root_causes": [
    "SOURCE_SEMANTIC_PAYMENT_DUPLICATE"
  ],
  "direct_failure": null,
  "affected_assets": [
    "model.jaffle_shop.customers",
    "model.jaffle_shop.orders",
    "model.jaffle_shop.stg_payments"
  ],
  "observable_evidence_contract": {
    "schema_version": "observable_evidence.v1",
    "schema_relations": ["raw_payments"],
    "profile_relations": ["raw_payments"],
    "history_relations": [],
    "unresolved_gaps": []
  },
  "required_evidence_types": [
    "DBT_RUN_RESULTS",
    "DBT_LINEAGE",
    "RELATION_SCHEMA",
    "RELATION_DATA_PROFILE"
  ],
  "forbidden_leakage": [
    "SCENARIO_SPEC",
    "GROUND_TRUTH",
    "VARIANT_ROLE",
    "ANSWERABILITY",
    "EXPECTED_STATUS",
    "PRIVATE_PATH"
  ],
  "distractors": [
    {
      "kind": "NULLABLE_COLUMN_SCHEMA_DRIFT",
      "relation": "raw_payments",
      "column": "source_batch_note",
      "data_type": "text",
      "nullable": true
    }
  ],
  "expected_status": "CONFIRMED"
}
~~~

- [ ] **Step 6: Create the insufficient test ScenarioSpec exactly.**

Create config/scenarios/duplicate_payment_coupon_b.json:

~~~json
{
  "schema_version": "scenario.v1",
  "incident_case_id": "duplicate_payment_coupon_b",
  "suite": "P1",
  "fault_family": "PAYMENT_DUPLICATE",
  "variant_role": "TEST_INSUFFICIENT",
  "answerability": "INSUFFICIENT",
  "seed": {
    "schema_version": "seed.v1",
    "fixture_path": "third_party/jaffle_shop",
    "fixture_commit": "36bde6cba69d962b83be1d52fc65a0dce1cb4ebb",
    "seed_names": ["raw_customers", "raw_orders", "raw_payments"],
    "refresh": "FULL_REFRESH"
  },
  "incident_brief": {
    "schema_version": "incident_brief.v1",
    "signal_code": "PAYMENT_DUPLICATE_ALERT",
    "summary": "A payment retry alert was triggered for the coupon channel.",
    "subjects": ["seed.jaffle_shop.raw_payments", "raw_payments"],
    "logical_observed_at": "2018-04-09T12:00:00Z",
    "observations": [
      {
        "kind": "CHANNEL_PAYMENT_RETRY_ALERT",
        "subject": "raw_payments",
        "value": "coupon"
      }
    ]
  },
  "reset_and_injection_contract": {
    "schema_version": "reset_injection.v1",
    "mutations": [
      {
        "kind": "DUPLICATE_PAYMENT_ROWS",
        "purpose": "FAULT",
        "relation": "raw_payments",
        "mode": "SEMANTIC_FINGERPRINT",
        "source_payment_ids": [47, 66, 86],
        "inserted_payment_ids": [114, 115, 116]
      },
      {
        "kind": "ADD_NULLABLE_COLUMN",
        "relation": "raw_payments",
        "column": "source_batch_note",
        "data_type": "text",
        "nullable": true
      }
    ],
    "restore_strategy": "FULL_REFRESH_BASELINE"
  },
  "ground_truth_or_acceptable_root_causes": [
    "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
    "LEGITIMATE_SPLIT_PAYMENT"
  ],
  "direct_failure": null,
  "affected_assets": [
    "model.jaffle_shop.customers",
    "model.jaffle_shop.orders",
    "model.jaffle_shop.stg_payments"
  ],
  "observable_evidence_contract": {
    "schema_version": "observable_evidence.v1",
    "schema_relations": ["raw_payments"],
    "profile_relations": [],
    "history_relations": [],
    "unresolved_gaps": [
      {
        "gap_kind": "RELATION_DATA_PROFILE",
        "subject": "raw_payments",
        "reason_code": "RELATION_NOT_ALLOWED",
        "tool_name": "get_relation_data_profile"
      },
      {
        "gap_kind": "PAYMENT_EVENT_IDENTITY",
        "subject": "raw_payments",
        "reason_code": "NOT_OBSERVABLE",
        "tool_name": null
      }
    ]
  },
  "required_evidence_types": [
    "DBT_RUN_RESULTS",
    "DBT_LINEAGE",
    "RELATION_SCHEMA"
  ],
  "forbidden_leakage": [
    "SCENARIO_SPEC",
    "GROUND_TRUTH",
    "VARIANT_ROLE",
    "ANSWERABILITY",
    "EXPECTED_STATUS",
    "PRIVATE_PATH"
  ],
  "distractors": [
    {
      "kind": "NULLABLE_COLUMN_SCHEMA_DRIFT",
      "relation": "raw_payments",
      "column": "source_batch_note",
      "data_type": "text",
      "nullable": true
    }
  ],
  "expected_status": "INSUFFICIENT_EVIDENCE"
}
~~~

- [ ] **Step 7: Run scenario and isolation tests.**

~~~powershell
uv run pytest tests/unit/test_scenarios.py tests/unit/test_p1_isolation.py -q
$policyFiles = @(
  'src/data_incident_gym/diagnostic_agent.py',
  'src/data_incident_gym/diagnostic_kernel.py',
  'src/data_incident_gym/prompts/static_skill.md',
  'src/data_incident_gym/prompts/diagnostic_kernel.md'
)
rg -n 'duplicate_payment_(record|coupon_[ab])|47|66|86|114|115|116' $policyFiles
~~~

Expected: tests pass and rg returns no matches.

- [ ] **Step 8: Commit only the catalog contract.**

~~~powershell
git add src/data_incident_gym/scenarios.py tests/unit/test_scenarios.py tests/unit/test_p1_isolation.py config/scenarios/duplicate_payment_record.json config/scenarios/duplicate_payment_coupon_a.json config/scenarios/duplicate_payment_coupon_b.json
git diff --cached --check
git commit -m "feat: define M9 duplicate payment scenarios"
~~~

## Task 2: Implement reversible duplicate injection and private anomaly verification

**Files:**

- Modify: src/data_incident_gym/lab.py
- Modify: src/data_incident_gym/lab_verifier.py
- Modify: tests/unit/test_lab.py
- Modify: tests/unit/test_lab_verifier.py
- Modify: tests/integration/test_incident_lab.py

- [ ] **Step 1: Write RED lifecycle tests for both frozen batches.**

Cover these invariants:

~~~python
@pytest.mark.parametrize(
    ("case_id", "healthy_count", "injected_count"),
    (
        ("duplicate_payment_record", 113, 114),
        ("duplicate_payment_coupon_a", 113, 116),
        ("duplicate_payment_coupon_b", 113, 116),
    ),
)
def test_duplicate_payment_lifecycle_is_exact(
    case_id: str,
    healthy_count: int,
    injected_count: int,
) -> None:
    scenario = load_scenario_spec(case_id)
    mutation = next(
        item
        for item in scenario.reset_and_injection_contract.mutations
        if isinstance(item, DuplicatePaymentRowsMutation)
    )
    pairs = duplicate_payment_rows(mutation)
    assert healthy_count == 113
    assert injected_count == 113 + len(pairs)
    if mutation.mode == "EXACT_RECORD":
        assert pairs == (((1, 1, "credit_card", 1000), (1, 1, "credit_card", 1000)),)
    else:
        assert pairs == (
            ((47, 42, "coupon", 1700), (114, 42, "coupon", 1700)),
            ((66, 58, "coupon", 1800), (115, 58, "coupon", 1800)),
            ((86, 76, "coupon", 200), (116, 76, "coupon", 200)),
        )
~~~

Add fake-cursor tests proving all SQL identifiers are composed, all values are bound parameters, insert and delete row-count guards raise inside a transaction, restore is a no-op from healthy state, and unknown multiplicity is rejected.

- [ ] **Step 2: Add exact row-state helpers.**

Import DuplicatePaymentRowsMutation and duplicate_payment_rows. Add:

~~~python
    def _payment_row_count(self, row: tuple[int, int, str, int]) -> int:
        statement = sql.SQL(
            "SELECT count(*) FROM {}.{} "
            "WHERE id IS NOT DISTINCT FROM %s "
            "AND order_id IS NOT DISTINCT FROM %s "
            "AND payment_method IS NOT DISTINCT FROM %s "
            "AND amount IS NOT DISTINCT FROM %s"
        ).format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier("raw_payments"),
        )
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(statement, row)
                result = cursor.fetchone()
        except LabError:
            raise
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(
                    f"读取 duplicate-payment 状态失败：{self._redact(str(exc))}"
                )
            ) from None
        if result is None:
            raise InvalidIncidentState("duplicate-payment 计数不可用")
        return int(result[0])

    def _payment_row_total(self) -> int:
        relation = self._healthy_relation("raw_payments")
        return relation.row_count
~~~

Add _duplicate_payment_state(mutation) returning HEALTHY, INJECTED, or UNKNOWN by comparing total row count and every frozen row multiplicity. EXACT_RECORD is healthy at one matching row and injected at two. SEMANTIC_FINGERPRINT is healthy when each source is present once and each new id is absent; it is injected when every source and inserted row is present once.

- [ ] **Step 3: Insert the frozen batch atomically.**

Use one parameterized VALUES statement and keep the row-count guard inside the transaction:

~~~python
    def _insert_payment_duplicates(self, mutation: DuplicatePaymentRowsMutation) -> None:
        rows = tuple(inserted for _, inserted in duplicate_payment_rows(mutation))
        values = sql.SQL(", ").join(sql.SQL("(%s, %s, %s, %s)") for _ in rows)
        statement = sql.SQL(
            "INSERT INTO {}.{} (id, order_id, payment_method, amount) VALUES {}"
        ).format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(mutation.relation),
            values,
        )
        parameters = tuple(value for row in rows for value in row)
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement, parameters)
                if cursor.rowcount != len(rows):
                    raise InvalidIncidentState(
                        "duplicate-payment mutation 必须插入精确行数"
                    )
        except LabError:
            raise
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(
                    f"写入 duplicate-payment mutation 失败：{self._redact(str(exc))}"
                )
            ) from None
~~~

- [ ] **Step 4: Remove exactly one inserted multiplicity per frozen row.**

For each inserted row, use PostgreSQL ctid only as a private physical selector. Deleting either physical copy of the exact development row is valid because the two rows are byte-identical and the resulting multiset is the healthy singleton.

~~~python
    def _delete_payment_duplicates(self, mutation: DuplicatePaymentRowsMutation) -> None:
        statement = sql.SQL(
            "WITH target AS ("
            "SELECT ctid FROM {}.{} "
            "WHERE id IS NOT DISTINCT FROM %s "
            "AND order_id IS NOT DISTINCT FROM %s "
            "AND payment_method IS NOT DISTINCT FROM %s "
            "AND amount IS NOT DISTINCT FROM %s "
            "ORDER BY ctid DESC LIMIT 1"
            ") DELETE FROM {}.{} WHERE ctid IN (SELECT ctid FROM target)"
        ).format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(mutation.relation),
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(mutation.relation),
        )
        inserted_rows = tuple(
            inserted for _, inserted in duplicate_payment_rows(mutation)
        )
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                deleted = 0
                for row in inserted_rows:
                    cursor.execute(statement, row)
                    deleted += cursor.rowcount
                if deleted != len(inserted_rows):
                    raise InvalidIncidentState(
                        "duplicate-payment restore 必须删除精确行数"
                    )
        except LabError:
            raise
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(
                    f"恢复 duplicate-payment mutation 失败：{self._redact(str(exc))}"
                )
            ) from None
~~~

Wire the mutation into _ensure_healthy_for_prepare, _apply_mutations, _validate_prepared_state, _restore_mutations, and _verify_restored. Preserve declared apply order and reverse restore order so source_batch_note is removed before duplicate rows.

- [ ] **Step 5: Add EXPECTED_ANOMALY and independent duplicate verification.**

Add:

~~~python
class ScenarioVerificationStatus(StrEnum):
    EXPECTED_FAILURE = "EXPECTED_FAILURE"
    EXPECTED_ANOMALY = "EXPECTED_ANOMALY"
    HEALTHY_CONTROL = "HEALTHY_CONTROL"
~~~

Extend _validate_mutation_schema so DuplicatePaymentRowsMutation changes row_count by len(inserted_payment_ids) but changes no column. Add a private _validate_payment_duplicates method that independently queries the four declared payment columns and proves:

~~~python
expected = {
    "duplicate_payment_record": {
        "row_count": 114,
        "id_duplicates": 1,
        "fingerprint_duplicates": 1,
        "channel": ("credit_card", 56),
    },
    "duplicate_payment_coupon_a": {
        "row_count": 116,
        "id_duplicates": 0,
        "fingerprint_duplicates": 3,
        "channel": ("coupon", 16),
    },
    "duplicate_payment_coupon_b": {
        "row_count": 116,
        "id_duplicates": 0,
        "fingerprint_duplicates": 3,
        "channel": ("coupon", 16),
    },
}
~~~

Do not put this expected map in ProfileSpec, runtime.json, evidence output, prompts, or policy code. It belongs only to private verification tests or private verifier derivation from the mutation.

- [ ] **Step 6: Generalize manifest impact only for a dbt-success source anomaly.**

Allow _affected_models to start at a seed node and traverse model children. Resolve the anchor by requiring exactly one manifest node whose resource_type is seed and whose name equals mutation.relation.

In verify(), retain HEALTHY_CONTROL first, then add:

~~~python
        elif scenario.direct_failure is None:
            if dbt_exit_code != 0 or failed_nodes or skipped_nodes:
                raise _clean(
                    LabVerificationError("data anomaly 场景的 dbt build 未健康完成")
                )
            mutation = next(
                item
                for item in scenario.reset_and_injection_contract.mutations
                if isinstance(item, DuplicatePaymentRowsMutation)
            )
            seed_nodes = tuple(
                node_id
                for node_id, node in manifest["nodes"].items()
                if node.get("resource_type") == "seed"
                and node.get("name") == mutation.relation
            )
            if len(seed_nodes) != 1:
                raise _clean(LabVerificationError("duplicate-payment seed anchor 不唯一"))
            affected = self._affected_models(manifest, seed_nodes[0])
            if affected != set(scenario.affected_assets):
                raise _clean(LabVerificationError("data anomaly 影响模型集合不匹配"))
            self._validate_payment_duplicates(scenario, profile)
            status = ScenarioVerificationStatus.EXPECTED_ANOMALY
~~~

The existing direct-failure branch remains EXPECTED_FAILURE and must also call _validate_payment_duplicates when the scenario contains the new mutation.

- [ ] **Step 7: Add one real-PostgreSQL M9 integration seam.**

Update the existing status selection:

~~~python
if scenario.expected_status == "NO_INCIDENT":
    expected_status = ScenarioVerificationStatus.HEALTHY_CONTROL
elif scenario.direct_failure is None:
    expected_status = ScenarioVerificationStatus.EXPECTED_ANOMALY
else:
    expected_status = ScenarioVerificationStatus.EXPECTED_FAILURE
~~~

Add one M9-focused test that runs each new case once and proves the exact public profile boundary and final recovery:

~~~python
expected_public = {
    "duplicate_payment_record": (1, 1, ("credit_card", 56)),
    "duplicate_payment_coupon_a": (0, 3, ("coupon", 16)),
    "duplicate_payment_coupon_b": None,
}
~~~

For A and development, read business_key_duplicates, business_fingerprint_duplicates, and groups from profile_snapshot.json. For B, assert raw_payments is absent. After restore, assert raw_payments has 113 rows, zero id duplicates, zero fingerprint duplicates, and coupon count 13.

- [ ] **Step 8: Run lab/verifier checks.**

~~~powershell
uv run pytest tests/unit/test_lab.py tests/unit/test_lab_verifier.py -q
uv run pytest tests/integration/test_incident_lab.py -q
~~~

Expected: unit tests pass; each M9 integration case completes once, obtains its exact verification status, and restores the healthy database. A Windows native dbt exit without a Python assertion is recorded as environment-unverified and is not relabelled as a product pass.

- [ ] **Step 9: Commit only injection and private verification files.**

~~~powershell
git add src/data_incident_gym/lab.py src/data_incident_gym/lab_verifier.py tests/unit/test_lab.py tests/unit/test_lab_verifier.py tests/integration/test_incident_lab.py
git diff --cached --check
git commit -m "feat: inject and verify duplicate payments"
~~~

## Task 3: Extend the shared diagnosis protocol for successful-run anomalies

**Files:**

- Modify: src/data_incident_gym/diagnosis.py
- Modify: src/data_incident_gym/diagnostic_agent.py
- Modify: src/data_incident_gym/diagnostic_kernel.py
- Modify: src/data_incident_gym/prompts/static_skill.md
- Modify: src/data_incident_gym/prompts/diagnostic_kernel.md
- Modify: tests/unit/test_diagnosis.py
- Modify: tests/unit/test_diagnostic_agent.py
- Modify: tests/unit/test_diagnostic_kernel.py
- Modify: tests/unit/test_policy_fairness.py

- [ ] **Step 1: Write RED tests for ontology parity and missing-event declarations.**

Assert both policies receive the same three new codes, PAYMENT_EVENT_IDENTITY parses only with NOT_OBSERVABLE, the Kernel rejects that declaration for a subject absent from IncidentBrief, and no policy receives case ids or frozen row values.

Add a successful-run Kernel test whose public records contain:

~~~python
DbtRunResultsFact(run_status="SUCCEEDED", failed_nodes=(), skipped_nodes=())
RelationDataProfileFact(
    relation_name="raw_payments",
    snapshot=profile_with(
        id_duplicate_count=0,
        fingerprint_duplicate_count=3,
        coupon_count=16,
    ),
)
DbtLineageFact(
    node_id="seed.jaffle_shop.raw_payments",
    direction="downstream",
    related_nodes=(
        model_node("model.jaffle_shop.stg_payments", "stg_payments", 1),
        model_node("model.jaffle_shop.customers", "customers", 2),
        model_node("model.jaffle_shop.orders", "orders", 2),
    ),
)
~~~

The decision must confirm SOURCE_SEMANTIC_PAYMENT_DUPLICATE, assess LEGITIMATE_SPLIT_PAYMENT as refuted, and bind every affected-asset claim to downstream lineage. Add negative tests for a positive id duplicate, zero fingerprint duplicates, missing successful-run evidence, or a relation not named by the public incident.

- [ ] **Step 2: Extend the public vocabulary and shared ontology.**

Add PAYMENT_EVENT_IDENTITY to UnresolvedEvidence.evidence_kind. Update:

~~~python
P1_ROOT_CAUSE_CODES = (
    "SOURCE_SCHEMA_COLUMN_RENAMED",
    "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
    "TRANSFORMATION_COLUMN_CAST_CHANGED",
    "SOURCE_REQUIRED_FIELD_NULL",
    "TRANSFORMATION_REQUIRED_FIELD_NULL",
    "SOURCE_EXACT_PAYMENT_DUPLICATE",
    "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
    "LEGITIMATE_SPLIT_PAYMENT",
)

KERNEL_PROMPT_VERSION = "p1.kernel.v4"
STATIC_PROMPT_VERSION = "p1.static.v4"
CONTROLLER_PROTOCOL_VERSION = "p1.controller.v3"
~~~

Do not change budget constants, output retry count, tool names, Diagnosis field names, or artifact schemas.

- [ ] **Step 3: Permit lineage from a public incident node without opening arbitrary node access.**

Change only the get_dbt_lineage provenance condition:

~~~python
        if (
            tool_name == "get_dbt_lineage"
            and arguments["node_id"]
            not in (
                self._known_failed_nodes()
                | self._known_lineage_nodes()
                | self._incident_subjects
            )
        ):
            self._error("NODE_ARGUMENT_NOT_PROVEN", fingerprint)
~~~

The evidence tool still validates the node against the current run manifest. Do not allow arbitrary node ids, paths, SQL, or relations.

- [ ] **Step 4: Add generic duplicate-profile predicates.**

Add helpers in diagnostic_kernel.py:

~~~python
def _duplicate_count(
    profile: RelationDataProfileFact,
    collection: str,
    name: str,
) -> int | None:
    facts = getattr(profile.snapshot, collection)
    fact = next((item for item in facts if item.name == name), None)
    return None if fact is None else fact.duplicate_count


def _duplicate_root_supported(
    root_cause_code: str,
    records: list[EvidenceRecord],
    incident_subjects: set[str],
) -> bool:
    runs = [
        record.content
        for record in records
        if isinstance(record.content, DbtRunResultsFact)
    ]
    profiles = [
        record.content
        for record in records
        if isinstance(record.content, RelationDataProfileFact)
        and record.content.relation_name in incident_subjects
    ]
    if len(runs) != 1 or len(profiles) != 1:
        return False
    profile = profiles[0]
    key_count = _duplicate_count(profile, "business_key_duplicates", "id")
    fingerprint_count = _duplicate_count(
        profile,
        "business_fingerprint_duplicates",
        "order_payment_amount",
    )
    payment_method_group = next(
        (item for item in profile.snapshot.groups if item.name == "payment_method"),
        None,
    )
    if root_cause_code == "SOURCE_EXACT_PAYMENT_DUPLICATE":
        return (
            key_count is not None
            and key_count > 0
            and fingerprint_count is not None
            and payment_method_group is not None
        )
    if root_cause_code == "SOURCE_SEMANTIC_PAYMENT_DUPLICATE":
        return (
            runs[0].run_status == "SUCCEEDED"
            and not runs[0].failed_nodes
            and key_count == 0
            and fingerprint_count is not None
            and fingerprint_count > 0
            and payment_method_group is not None
        )
    return False
~~~

In _validate_confirmed, route the two duplicate codes through this predicate. Keep the existing node-error/upstream-relation gate unchanged for schema and NULL roots. Keep the exact-duplicate development case tied to its failed unique test by requiring a DbtNodeErrorFact in its root evidence.

- [ ] **Step 5: Keep affected-asset evidence generic.**

Retain the current direct node-error, upstream tested-model, and downstream-lineage alternatives. The successful semantic case must pass only through:

~~~python
isinstance(record.content, DbtLineageFact)
and record.content.direction == "downstream"
and any(
    node.node_id == claim.value or node.name == claim.value
    for node in record.content.related_nodes
)
~~~

Do not infer affected assets from the private scenario, case id, profile counts, or prompt text.

- [ ] **Step 6: Bind PAYMENT_EVENT_IDENTITY without inventing a tool.**

In _validate_unresolved_declarations:

~~~python
            elif item.evidence_kind == "PAYMENT_EVENT_IDENTITY":
                if (
                    item.reason_code != "NOT_OBSERVABLE"
                    or item.subject not in self._incident_subjects
                ):
                    self._error("UNRESOLVED_EVIDENCE_UNBOUND")
~~~

The derived-unresolved logic continues to synthesize only blocked schema/profile gaps. PAYMENT_EVENT_IDENTITY must come from the model decision and be validated against public context.

- [ ] **Step 7: Teach both prompts the same policy-neutral semantics.**

Add equivalent text to both prompt files:

~~~text
A successful dbt run proves only that the executed models and tests completed. It does not prove
that a public data-quality alert is healthy. For a payment duplicate alert, inspect the declared
raw_payments aggregate profile and downstream lineage.

Confirm SOURCE_EXACT_PAYMENT_DUPLICATE only when the declared id business-key duplicate count is
positive. Confirm SOURCE_SEMANTIC_PAYMENT_DUPLICATE only when id duplicates are zero and the
declared order_payment_amount business-fingerprint duplicate count is positive. Bind affected
models to downstream lineage.

When the raw_payments profile is unavailable and payment idempotency or channel-event identity is
not observable, preserve SOURCE_SEMANTIC_PAYMENT_DUPLICATE and LEGITIMATE_SPLIT_PAYMENT as
alternatives and return INSUFFICIENT_EVIDENCE. PAYMENT_EVENT_IDENTITY is a missing-evidence
declaration, not a business tool.
~~~

Do not mention case ids, source ids, inserted ids, expected counts, coupon count 16, Ground Truth, variant role, or evaluator behavior.

- [ ] **Step 8: Run protocol and fairness tests.**

~~~powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_policy_fairness.py -q
~~~

Expected: all tests pass; successful-run duplicate claims are gated by public run/profile/lineage facts; ontology, budgets, tools, and public context remain equal.

- [ ] **Step 9: Commit only shared protocol files.**

~~~powershell
git add src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/diagnostic_kernel.py src/data_incident_gym/prompts/static_skill.md src/data_incident_gym/prompts/diagnostic_kernel.md tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_policy_fairness.py
git diff --cached --check
git commit -m "feat: add duplicate payment diagnosis protocol"
~~~

## Task 4: Score exact and semantic duplicate evidence independently

**Files:**

- Modify: src/data_incident_gym/evaluation.py
- Modify: tests/unit/test_evaluation.py

- [ ] **Step 1: Write RED evaluator tests for all three roles.**

Add one passing run per role and parameterize the exact private/public combinations below. Build
each DiagnosisRunResult from DbtRunResultsFact, DbtNodeErrorFact when applicable,
DbtLineageFact, RelationSchemaFact, and RelationDataProfileFact; use the existing EvidenceRecord
and terminal-trace factories in this test module.

~~~python
M9_EVALUATOR_CASES = (
    pytest.param(
        "duplicate_payment_record",
        "FAILED",
        1,
        1,
        "EXPECTED_FAILURE",
        (),
        EvaluationStatus.PASSED,
        id="exact-record",
    ),
    pytest.param(
        "duplicate_payment_coupon_a",
        "SUCCEEDED",
        0,
        3,
        "EXPECTED_ANOMALY",
        (),
        EvaluationStatus.PASSED,
        id="semantic-confirmable",
    ),
    pytest.param(
        "duplicate_payment_coupon_a",
        "SUCCEEDED",
        1,
        3,
        "EXPECTED_ANOMALY",
        (EvaluationCheckCode.CLAIM_EVIDENCE_COMPATIBLE,),
        EvaluationStatus.FAILED,
        id="semantic-rejects-key-duplicate",
    ),
    pytest.param(
        "duplicate_payment_coupon_a",
        "SUCCEEDED",
        0,
        0,
        "EXPECTED_ANOMALY",
        (EvaluationCheckCode.CLAIM_EVIDENCE_COMPATIBLE,),
        EvaluationStatus.FAILED,
        id="semantic-rejects-zero-fingerprint",
    ),
)

M9_INSUFFICIENT_GAPS = {
    ("RELATION_DATA_PROFILE", "raw_payments", "RELATION_NOT_ALLOWED"),
    ("PAYMENT_EVENT_IDENTITY", "raw_payments", "NOT_OBSERVABLE"),
}
~~~

For the first two rows, assert no failed check code. For the negative rows, assert the listed code
is present. Add a separate insufficient test that compares M9_INSUFFICIENT_GAPS exactly, and an
environment test that changes either failed_nodes or skipped_nodes from the empty tuple and
expects ENVIRONMENT_VERIFIED to fail. Add one semantic-profile mutation that changes coupon count
from 16 to 15 and assert CLAIM_EVIDENCE_COMPATIBLE fails. No test may inspect model prose.

- [ ] **Step 2: Recognize EXPECTED_ANOMALY in environment verification.**

Replace _environment_verified with the three explicit modes:

~~~python
def _environment_verified(
    scenario: ScenarioSpec,
    verification: ScenarioVerification,
    run_id: str,
) -> bool:
    if (
        verification.run_id != run_id
        or verification.incident_case_id != scenario.incident_case_id
    ):
        return False
    if scenario.answerability is Answerability.NO_INCIDENT:
        return (
            verification.status == "HEALTHY_CONTROL"
            and verification.dbt_exit_code == 0
            and not verification.failed_nodes
            and not verification.skipped_nodes
        )
    if scenario.direct_failure is None:
        return (
            verification.status == "EXPECTED_ANOMALY"
            and verification.dbt_exit_code == 0
            and not verification.failed_nodes
            and not verification.skipped_nodes
            and tuple(sorted(verification.affected_assets))
            == tuple(sorted(scenario.affected_assets))
        )
    return (
        verification.status == "EXPECTED_FAILURE"
        and verification.dbt_exit_code != 0
        and verification.failed_nodes == (scenario.direct_failure,)
        and tuple(sorted(verification.affected_assets))
        == tuple(sorted(scenario.affected_assets))
    )
~~~

- [ ] **Step 3: Add exact private-to-public duplicate compatibility.**

Import DuplicatePaymentRowsMutation. In _root_cause_evidence_compatible, branch before the existing node-error requirement:

~~~python
    duplicate = next(
        (
            item
            for item in scenario.reset_and_injection_contract.mutations
            if isinstance(item, DuplicatePaymentRowsMutation)
        ),
        None,
    )
    if duplicate is not None:
        run = next(
            (
                record.content
                for record in root_records
                if isinstance(record.content, DbtRunResultsFact)
            ),
            None,
        )
        profile = next(
            (
                record.content
                for record in root_records
                if isinstance(record.content, RelationDataProfileFact)
                and record.content.relation_name == duplicate.relation
            ),
            None,
        )
        if run is None or profile is None:
            return False
        key = next(
            (
                item
                for item in profile.snapshot.business_key_duplicates
                if item.name == "id"
            ),
            None,
        )
        fingerprint = next(
            (
                item
                for item in profile.snapshot.business_fingerprint_duplicates
                if item.name == "order_payment_amount"
            ),
            None,
        )
        payment_method_group = next(
            (
                item
                for item in profile.snapshot.groups
                if item.name == "payment_method"
            ),
            None,
        )
        if key is None or fingerprint is None or payment_method_group is None:
            return False
        grouped_counts = dict(
            zip(
                (values[0] for values in payment_method_group.values),
                payment_method_group.counts,
                strict=True,
            )
        )
        if duplicate.mode == "EXACT_RECORD":
            return (
                root_cause_code == "SOURCE_EXACT_PAYMENT_DUPLICATE"
                and key.duplicate_count == 1
                and fingerprint.duplicate_count == 1
                and grouped_counts.get("credit_card") == 56
                and any(
                    isinstance(record.content, DbtNodeErrorFact)
                    and record.content.node_id == scenario.direct_failure
                    for record in root_records
                )
            )
        return (
            root_cause_code == "SOURCE_SEMANTIC_PAYMENT_DUPLICATE"
            and run.run_status == "SUCCEEDED"
            and not run.failed_nodes
            and key.duplicate_count == 0
            and fingerprint.duplicate_count == len(duplicate.inserted_payment_ids)
            and grouped_counts.get("coupon") == 16
        )
~~~

This exact count logic belongs in the private evaluator. Do not copy it into ProfileSpec or public prompts.

- [ ] **Step 4: Accept downstream lineage for a source anomaly asset claim.**

The existing downstream predicate already supports this shape. Add a regression proving each of the three semantic affected assets is present in one downstream DbtLineageFact whose node_id is seed.jaffle_shop.raw_payments. Do not add a fallback from scenario.affected_assets.

- [ ] **Step 5: Require the exact insufficient gap pair.**

The existing _insufficiency_matches exact set comparison remains authoritative. Add PAYMENT_EVENT_IDENTITY to the allowed model and assert:

~~~python
{
    ("RELATION_DATA_PROFILE", "raw_payments", "RELATION_NOT_ALLOWED"),
    ("PAYMENT_EVENT_IDENTITY", "raw_payments", "NOT_OBSERVABLE"),
}
~~~

The profile gap must still bind to exactly one failed get_relation_data_profile call. The non-tool event gap has no ToolTraceEvent.

- [ ] **Step 6: Run evaluator tests.**

~~~powershell
uv run pytest tests/unit/test_evaluation.py -q
~~~

Expected: all M7/M8 regressions and the new M9 positive/negative evidence checks pass.

- [ ] **Step 7: Commit only evaluator files.**

~~~powershell
git add src/data_incident_gym/evaluation.py tests/unit/test_evaluation.py
git diff --cached --check
git commit -m "feat: score duplicate payment evidence"
~~~

## Task 5: Close the cumulative 20-cell policy matrix and update milestone documentation

**Files:**

- Modify: tests/e2e/test_p1_policy_matrix.py
- Modify: tests/unit/test_artifacts.py
- Modify: README.md

- [ ] **Step 1: Extend the deterministic model from public signals, never case ids.**

Pass context.incident_brief.signal_code and context.incident_brief.subjects into the FunctionModel callback. Use:

~~~python
if run_fact.run_status == "SUCCEEDED" and signal_code == "PAYMENT_DUPLICATE_ALERT":
    return _duplicate_payment_response(
        messages,
        agent_info,
        run_id=run_id,
        strategy=strategy,
        profile_relations=profile_relations,
    )
if run_fact.run_status == "SUCCEEDED":
    return _health_response(
        messages,
        agent_info,
        run_id=run_id,
        strategy=strategy,
        profile_relations=profile_relations,
    )
if node_error.content.node_id.endswith(
    "unique_stg_payments_payment_id.3744510712"
):
    return _exact_duplicate_response(
        messages,
        agent_info,
        run_id=run_id,
        strategy=strategy,
        schema_relations=schema_relations,
        profile_relations=profile_relations,
    )
~~~

Extract the existing successful-run health branch into _health_response with the signature shown
above. Implement _duplicate_payment_response with the semantic sequence in Step 2 and
_exact_duplicate_response with the failed-test sequence in Step 2. This branching exists only in
the deterministic FunctionModel test harness and uses public run/brief/node facts. Production
prompts, controllers, tools, and evaluator must contain no case-id dispatch.

- [ ] **Step 2: Encode the exact public investigation sequence.**

For semantic A and B, the deterministic model must:

1. call get_dbt_run_results;
2. call get_dbt_lineage for seed.jaffle_shop.raw_payments with direction=downstream;
3. call get_relation_schema for raw_payments and observe the harmless source_batch_note column;
4. call get_relation_data_profile for raw_payments;
5. confirm semantic duplicate when the profile succeeds, or return INSUFFICIENT_EVIDENCE after the one RELATION_NOT_ALLOWED failure and the PAYMENT_EVENT_IDENTITY declaration.

For Kernel intents, register:

~~~python
new_hypotheses = [
    {
        "hypothesis_id": "h_semantic_duplicate",
        "root_cause_code": "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
    },
    {
        "hypothesis_id": "h_legitimate_split",
        "root_cause_code": "LEGITIMATE_SPLIT_PAYMENT",
    },
]
~~~

For the development case, register h_exact_duplicate and h_semantic_duplicate, cite the failed unique test and source profile for the root claim, and cite distance-1 tested-model lineage for model.jaffle_shop.stg_payments.

- [ ] **Step 3: Expand the cumulative matrix exactly.**

Update:

~~~python
MATRIX_CASES = P1_M7_SCENARIO_IDS + P1_M8_SCENARIO_IDS + P1_M9_SCENARIO_IDS
MATRIX_STRATEGIES = (
    DiagnosticStrategy.STATIC_SKILL,
    DiagnosticStrategy.DIAGNOSTIC_KERNEL,
)
assert len(MATRIX_CASES) == 10
assert len(MATRIX_CASES) * len(MATRIX_STRATEGIES) == 20
~~~

Add exact terminal assertions:

~~~python
M9_EXPECTED = {
    "duplicate_payment_record": (
        "CONFIRMED",
        "SOURCE_EXACT_PAYMENT_DUPLICATE",
        ["model.jaffle_shop.stg_payments"],
    ),
    "duplicate_payment_coupon_a": (
        "CONFIRMED",
        "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
        [
            "model.jaffle_shop.customers",
            "model.jaffle_shop.orders",
            "model.jaffle_shop.stg_payments",
        ],
    ),
    "duplicate_payment_coupon_b": (
        "INSUFFICIENT_EVIDENCE",
        None,
        [],
    ),
}
~~~

For each cell, retain the exact six-file artifact assertion and trace allowlist assertion. For B, assert the exact two unresolved declarations.

- [ ] **Step 4: Keep artifact/report schemas unchanged.**

Add a fixture-level regression showing EXPECTED_ANOMALY flows through metadata/report content as the private verification status without adding a seventh artifact or a new public field. Do not change ARTIFACT_FILENAMES or p1 metadata/evaluation schema versions.

- [ ] **Step 5: Update README with evidence-bounded M9 status.**

State:

- P1 deterministic infrastructure now covers 10/17 scenarios.
- M9 adds exact duplicate, semantic duplicate, and insufficient duplicate/split variants.
- Semantic duplicate variants intentionally exercise a dbt-success data anomaly.
- The latest real-model observation remains the historical M7 1/8; M8 and M9 have no real-model result.
- Windows long-loop verification remains environment-unverified when the known native dbt/Python crash occurs.

Do not claim model-quality improvement, Kernel superiority, production readiness, or a passed M9 smoke.

- [ ] **Step 6: Run the cumulative matrix and smoke-cardinality guard.**

~~~powershell
uv run pytest tests/e2e/test_p1_policy_matrix.py -q
uv run pytest tests/e2e/test_real_model_m7_smoke.py --collect-only -q -p no:cacheprovider
~~~

Expected: 20 policy cells pass and exactly 8 real-model smoke items are collected without execution.

- [ ] **Step 7: Run leakage and artifact checks.**

~~~powershell
$productionPolicyFiles = @(
  'src/data_incident_gym/diagnostic_agent.py',
  'src/data_incident_gym/diagnostic_kernel.py',
  'src/data_incident_gym/prompts/static_skill.md',
  'src/data_incident_gym/prompts/diagnostic_kernel.md'
)
rg -n 'duplicate_payment_(record|coupon_[ab])|47|66|86|114|115|116|coupon_count' $productionPolicyFiles
rg -n '10/17|M9|1/8|real-model' README.md
~~~

Expected: the first rg returns no matches; README contains the evidence-bounded milestone statements.

- [ ] **Step 8: Commit only matrix and README files.**

~~~powershell
git add tests/e2e/test_p1_policy_matrix.py tests/unit/test_artifacts.py README.md
git diff --cached --check
git commit -m "test: close M9 deterministic policy matrix"
~~~

## Task 6: Run the M9 release gate without real-model calls or push

**Files:**

- Verify only; do not add generated artifacts, .dig content, dist output, decision.md, or reports.

- [ ] **Step 1: Reconcile the exact implementation diff and preserved user files.**

~~~powershell
git status --short
git log --oneline --decorate -8
git diff 443c4874e12b2c561b0dc4552ddfe3c9a7fe1f22...HEAD --stat
git diff 443c4874e12b2c561b0dc4552ddfe3c9a7fe1f22...HEAD --check
~~~

Expected: only planned M9 commits appear after 443c487; docs/requirements.md and the pre-existing untracked files remain unstaged; diff check is clean.

- [ ] **Step 2: Run the complete deterministic local verification set.**

~~~powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -m 'not real_model' -q
uv run pytest tests/e2e/test_real_model_m7_smoke.py --collect-only -q -p no:cacheprovider
uv lock --check
git diff --check
uv build
~~~

Expected:

- every unit test passes;
- integration and non-real-model E2E pass unless the already documented Windows native process crash occurs;
- the cumulative deterministic matrix contributes exactly 20 passing cells;
- smoke collection remains exactly 8 and makes no network request;
- Ruff, lock, diff, and build pass.

Any Python assertion failure, wrong duplicate count, wrong terminal, wrong affected set, leakage match, artifact mismatch, or unhealthy recovery is a product failure and blocks M9. Only a reproduced native Windows/dbt process exit without a business assertion may be recorded as environment-unverified.

- [ ] **Step 3: Prove final database recovery.**

~~~powershell
uv run data-incident-gym lab reset duplicate_payment_record
uv run pytest tests/integration/test_incident_lab.py::test_m9_duplicate_profiles_and_recovery -q
~~~

Expected: the reset reports HEALTHY; raw_payments returns to 113 rows with zero declared key/fingerprint duplicates and coupon count 13.

- [ ] **Step 4: Check generated and user-owned boundaries.**

~~~powershell
git status --short
git -C third_party/jaffle_shop status --short
git diff --cached --name-only
~~~

Expected: the submodule is clean; no generated artifacts are tracked; docs/requirements.md, AGENTS.md, decision.md, earlier plans, and reports remain outside the index unless the user separately authorized them.

- [ ] **Step 5: Stop at the release gate.**

Do not set DIG_RUN_REAL_MODEL_TESTS=1. Do not call a model endpoint. Do not push. Report:

- reviewed HEAD and commit list;
- deterministic test counts and commands;
- exact 20-cell matrix result;
- exact 8-item collect-only smoke result;
- database recovery evidence;
- any Windows environment-unverified command and native exit code;
- the unchanged dirty/user-owned file list;
- that real-model execution and push still require separate authorization.

## Final M9 acceptance checklist

- [ ] The M8 NULL update guard raises inside its transaction.
- [ ] Exactly three M9 scenarios exist and the cumulative P1 catalog is exactly 10/17.
- [ ] The development case duplicates the exact id=1 record and fails only the frozen unique test.
- [ ] Test variants A and B inject identical three-row coupon batches and the same harmless schema distractor.
- [ ] Variant A has dbt success, profile duplicate evidence, downstream lineage, and a confirmed semantic-duplicate root.
- [ ] Variant B has the same data and dbt result but can pass only with the exact profile and event-identity gaps.
- [ ] EXPECTED_FAILURE, EXPECTED_ANOMALY, and HEALTHY_CONTROL remain distinct private environment states.
- [ ] Successful dbt execution is never treated as sufficient proof of NO_INCIDENT.
- [ ] Exact duplicate, semantic duplicate, and legitimate split semantics are represented without case-ID dispatch.
- [ ] The existing ProfileSpec and all six evidence-tool schemas remain unchanged.
- [ ] Static Skill and Diagnostic Kernel share ontology, tools, budgets, public context, Diagnosis schema, evaluator, and artifacts.
- [ ] The cumulative deterministic matrix is exactly 20/20 and every cell writes six canonical artifacts.
- [ ] The M7 real-model smoke remains exactly eight collect-only items; M8/M9 real-model quality is unmeasured.
- [ ] Database state and the pinned submodule are healthy after verification.
- [ ] No generated artifact, secret, user-owned note, or unrelated dirty file is staged.
- [ ] No real-model call or push occurs without a later explicit authorization.
