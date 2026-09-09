# M8 Required-Field NULL Fault Family Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not delegate work unless the user explicitly authorizes delegation for that execution turn.

**Goal:** Add exactly the three required-field NULL scenarios promised by M8, carry them through deterministic injection, private verification, the two frozen diagnosis strategies, deterministic evaluation, recovery, and the six-file artifact contract, bringing the P1 catalog from 4/17 to 7/17.

**Architecture:** Extend the existing M7 vertical slice in place. A closed `SET_FIELD_NULL` mutation updates one allowlisted fixture row and is exactly reversible. Existing aggregate profile evidence supplies the decisive `null_count`; no raw rows or new evidence tools are exposed. Because this family fails dbt generic tests rather than model SQL, the private verifier, Kernel gate, and evaluator will map a failed test to its distance-1 tested model. The insufficient twin blocks only the decisive source profile and transformation definition while preserving all other observable facts.

**Tech Stack:** Python 3.12, Pydantic v2, psycopg 3 SQL composition, dbt Core/PostgreSQL Jaffle Shop fixture, PydanticAI `FunctionModel`, pytest, Ruff, uv, PowerShell 7.

---

## 0. Approved scope and fixed decisions

### Planning baseline

- Planning was performed against `master` at `f502458a5b90ae73e5808b40a03b2bb2df0b03a4`; re-check `HEAD`, status, and the diff before implementation.
- M7 is accepted as **infrastructure complete**. Its latest exact eight-cell real-model observation remains **1/8 diagnosis quality** and is historical development evidence only.
- The seven M7 failures remain fail-closed protocol/budget outcomes. M8 must not reinterpret them as product defects, retune M7, increase budgets, add retries, or run another M7 smoke.
- The existing working-tree changes to `docs/requirements.md` and the user-owned untracked `AGENTS.md`, `decision.md`, earlier plans, and reports must remain intact and unstaged unless the user separately changes that boundary.

### Exact M8 scenario matrix

| case_id | role | declared fault mutation | harmless distractor | direct dbt failure | exact affected model set | expected terminal |
|---|---|---|---|---|---|---|
| `required_null_payment_id` | `DEV_CONFIRMABLE` | Set `raw_payments.id` to `NULL` for the unique row selected by `order_id=1`; healthy value is `1` | none | `test.jaffle_shop.not_null_stg_payments_payment_id.c19cc50075` | `model.jaffle_shop.stg_payments` | `CONFIRMED` |
| `required_null_order_customer_a` | `TEST_CONFIRMABLE` | Set `raw_orders.user_id` to `NULL` for `id=42`; healthy value is `92` | Set nullable `raw_customers.last_name` to `NULL` for `id=7`; healthy value is `M.` | `test.jaffle_shop.not_null_orders_customer_id.c5f02694af` | `model.jaffle_shop.orders` | `CONFIRMED` |
| `required_null_order_customer_b` | `TEST_INSUFFICIENT` | Byte-for-byte the same two mutations as variant A | Byte-for-byte the same distractor as variant A | same as variant A | same as variant A | `INSUFFICIENT_EVIDENCE` |

The two test variants must also share the exact same public `IncidentBrief`. They differ only in answerability/private accepted causes, observable source-profile access, unresolved gaps, and expected terminal status.

### Root-cause ontology added by M8

- `SOURCE_REQUIRED_FIELD_NULL`: the required value is already NULL in the observable upstream relation.
- `TRANSFORMATION_REQUIRED_FIELD_NULL`: a transformation can introduce the downstream NULL even when the unavailable source-level fact might be healthy.

The two confirmable scenarios accept only `SOURCE_REQUIRED_FIELD_NULL`. The insufficient scenario records both codes as compatible private causes but returns no root-cause claim.

### Evidence boundary

| role | observable decisive evidence | deliberately unavailable evidence | required cited evidence types |
|---|---|---|---|
| `DEV_CONFIRMABLE` | failed not-null test, upstream lineage to `raw_payments`, `raw_payments.id.null_count == 1`, source schema | transformation definition remains outside all six tools but is not needed because the source NULL is directly observed | `DBT_RUN_RESULTS`, `DBT_NODE_ERROR`, `DBT_LINEAGE`, `RELATION_SCHEMA`, `RELATION_DATA_PROFILE` |
| `TEST_CONFIRMABLE` | failed not-null test, upstream lineage to `raw_orders`, `raw_orders.user_id.null_count == 1`, plus the unrelated `raw_customers.last_name.null_count == 1` distractor | transformation definition remains outside all six tools but is not decisive once the source NULL is observed | same five types |
| `TEST_INSUFFICIENT` | failed test, upstream lineage, healthy source schema, and the unrelated customer profile/distractor | `RELATION_DATA_PROFILE` for `raw_orders` returns `RELATION_NOT_ALLOWED`; `model.jaffle_shop.stg_orders` transformation definition is `NOT_OBSERVABLE` | same five types, with profile evidence coming only from the distractor relation |

The insufficient scenario's two exact unresolved declarations are:

```json
[
  {
    "evidence_kind": "RELATION_DATA_PROFILE",
    "subject": "raw_orders",
    "reason_code": "RELATION_NOT_ALLOWED"
  },
  {
    "evidence_kind": "TRANSFORMATION_DEFINITION",
    "subject": "model.jaffle_shop.stg_orders",
    "reason_code": "NOT_OBSERVABLE"
  }
]
```

### Required behavior changes

1. `run_results` status `fail` must be treated as a failed node, just like `error`.
2. A failed dbt test maps to its distance-1 model dependency for `affected_assets`; the test node itself is not an affected model.
3. A source-NULL root claim requires both the matching failed-node evidence and a matching aggregate profile whose declared column has exactly one NULL.
4. A profile evidence gap must be representable end-to-end as `RELATION_DATA_PROFILE`, bound to one blocked `get_relation_data_profile` attempt, and emitted by the Kernel without inventing evidence.
5. The public profile snapshot must contain no undeclared relation. Variant B must not accidentally publish `raw_orders` current profile facts.

### Non-goals

- No seventh evidence tool, raw-row reader, arbitrary SQL, user-defined mutation DSL, write-capable diagnosis tool, repair action, or fixture edit.
- No changes to `config/profiles/jaffle_shop.v1.json`; all three relevant columns are already declared.
- No change to the six-file artifact names or `p1.diagnosis.v1` data shape.
- No change to the shared `8 model requests / 8 business tool calls / 2 output retries / 300 seconds` budget.
- No M7 controller tuning, case-ID dispatch, fault-specific tool order in production code, or model-output repair.
- No real-model calls, M8 smoke, formal 94-run benchmark, benchmark manifest, aggregate metrics, or strategy comparison.
- No ten-cycle requirement is invented for M8. One real integration path per new scenario plus the six-cell deterministic policy matrix is the milestone acceptance evidence; the existing M7/P0 replay suites remain regressions.
- No update to `docs/requirements.md` is required: its M8 contract is already authoritative and sufficient.
- No push. A later push and Ubuntu CI observation require separate user authorization.

### Acceptance criteria

- Exactly three new P1 scenarios load, making `P1_M7_SCENARIO_IDS + P1_M8_SCENARIO_IDS` exactly 7 P1 cases; the P0 regression remains outside that count.
- Prepare/build/private verification/recovery succeeds for all three; the two test variants inject identical database state.
- The confirmable profiles expose exactly one NULL in the declared fault column. The test confirmable also exposes exactly one harmless NULL in `raw_customers.last_name`.
- The insufficient public snapshot exposes the distractor profile but not `raw_orders`; one blocked source-profile request and one unobservable transformation definition are required for a passing refusal.
- Both policies pass all three scenarios under deterministic `FunctionModel` execution, producing a six-cell M8 matrix and six canonical artifacts per cell.
- Existing M7 four-scenario matrix, real-model smoke collection shape, P0 regressions, and tool/budget fairness remain unchanged.
- Unit, integration, non-real-model E2E, Ruff, lock, diff, and build checks pass, subject to the already recorded Windows native instability boundary.

## 1. Deep-module map

- `src/data_incident_gym/scenarios.py` owns the private catalog, closed mutation union, answerability contract, and public evidence boundary. It must reject all NULL mutations outside the three fixed fixture targets.
- `src/data_incident_gym/lab.py` owns reversible database state changes. It may execute only identifier-composed, value-parameterized updates derived from validated mutations.
- `src/data_incident_gym/lab_verifier.py` independently proves database state, exact failed nodes, tested-model impact, public snapshot scope, and private scenario identity.
- `src/data_incident_gym/profiles.py` and `src/data_incident_gym/evidence_tools.py` already expose bounded aggregate `null_count` facts. They require no production change.
- `src/data_incident_gym/diagnosis.py` owns the public unresolved-evidence vocabulary.
- `src/data_incident_gym/diagnostic_agent.py` and the two prompt files own the shared root-cause ontology and policy protocol identities.
- `src/data_incident_gym/diagnostic_kernel.py` owns typed gap binding and terminal gates. It must bind a missing profile to a blocked profile intent and accept upstream test-to-model lineage for affected-model claims.
- `src/data_incident_gym/evaluation.py` independently checks the private expected answer against frozen public evidence; it must not trust model prose or Kernel state as Ground Truth.
- `tests/e2e/test_m7_policy_matrix.py` is the existing cumulative deterministic policy seam. Rename it to `tests/e2e/test_p1_policy_matrix.py` and extend it rather than creating a second duplicated harness.

## Task 1: Freeze the 7/17 catalog and the closed NULL mutation contract

**Files:**

- Modify: `src/data_incident_gym/scenarios.py`
- Create: `config/scenarios/required_null_payment_id.json`
- Create: `config/scenarios/required_null_order_customer_a.json`
- Create: `config/scenarios/required_null_order_customer_b.json`
- Modify: `tests/unit/test_scenarios.py`
- Include when implementation is committed: `docs/superpowers/plans/2026-08-31-m8-required-field-null-family.md`

- [ ] **Step 1: Write RED catalog, allowlist, twin, and leakage tests.**

Add focused assertions equivalent to:

```python
def test_m8_catalog_is_exact_and_test_pair_differs_only_by_evidence_boundary(
    project_root: Path,
) -> None:
    from data_incident_gym.scenarios import P1_M8_SCENARIO_IDS

    assert P1_M8_SCENARIO_IDS == (
        "required_null_payment_id",
        "required_null_order_customer_a",
        "required_null_order_customer_b",
    )
    dev, confirmable, insufficient = (
        load_scenario_spec(case_id, project_root) for case_id in P1_M8_SCENARIO_IDS
    )
    assert dev.variant_role is VariantRole.DEV_CONFIRMABLE
    assert confirmable.variant_role is VariantRole.TEST_CONFIRMABLE
    assert insufficient.variant_role is VariantRole.TEST_INSUFFICIENT
    for field in (
        "incident_brief",
        "reset_and_injection_contract",
        "direct_failure",
        "affected_assets",
        "distractors",
    ):
        assert getattr(confirmable, field) == getattr(insufficient, field)
    assert confirmable.observable_evidence_contract.profile_relations == (
        "raw_orders",
        "raw_customers",
    )
    assert insufficient.observable_evidence_contract.profile_relations == (
        "raw_customers",
    )
    assert tuple(
        (gap.gap_kind, gap.subject, gap.reason_code, gap.tool_name)
        for gap in insufficient.observable_evidence_contract.unresolved_gaps
    ) == (
        (
            "RELATION_DATA_PROFILE",
            "raw_orders",
            "RELATION_NOT_ALLOWED",
            "get_relation_data_profile",
        ),
        (
            "TRANSFORMATION_DEFINITION",
            "model.jaffle_shop.stg_orders",
            "NOT_OBSERVABLE",
            None,
        ),
    )


def test_m8_rejects_a_null_mutation_outside_the_frozen_fixture_target(
    project_root: Path,
) -> None:
    source = project_root / "config" / "scenarios" / "required_null_order_customer_a.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["reset_and_injection_contract"]["mutations"][0]["selector_value"] = 43
    with pytest.raises(ScenarioError):
        parse_scenario_spec(json.dumps(payload), "unsupported M8 target")


def test_m8_public_brief_contains_no_private_row_or_answer_contract(
    project_root: Path,
) -> None:
    scenario = load_scenario_spec("required_null_order_customer_b", project_root)
    public = scenario.incident_brief.model_dump_json()
    for forbidden in (
        "selector_column",
        "selector_value",
        "expected_value",
        "SOURCE_REQUIRED_FIELD_NULL",
        "TRANSFORMATION_REQUIRED_FIELD_NULL",
        "TEST_INSUFFICIENT",
        "INSUFFICIENT_EVIDENCE",
    ):
        assert forbidden not in public
```

- [ ] **Step 2: Run the focused RED tests.**

```powershell
uv run pytest tests/unit/test_scenarios.py -q
```

Expected: the new imports/cases fail because M8 is not yet registered.

- [ ] **Step 3: Add one exact mutation model, not a general data-edit DSL.**

Add these concepts to `scenarios.py`:

```python
P1_M8_SCENARIO_IDS = (
    "required_null_payment_id",
    "required_null_order_customer_a",
    "required_null_order_customer_b",
)
SUPPORTED_SCENARIO_IDS = (
    REGRESSION_SCENARIO_IDS + P1_M7_SCENARIO_IDS + P1_M8_SCENARIO_IDS
)


class FaultFamily(StrEnum):
    SCHEMA_RENAME = "SCHEMA_RENAME"
    SCHEMA_TYPE_CHANGE = "SCHEMA_TYPE_CHANGE"
    REQUIRED_FIELD_NULL = "REQUIRED_FIELD_NULL"
    ORDER_VOLUME_PATTERN = "ORDER_VOLUME_PATTERN"


_M8_NULL_TARGETS: dict[tuple[str, str, str, str, int], int | str] = {
    ("FAULT", "raw_payments", "id", "order_id", 1): 1,
    ("FAULT", "raw_orders", "user_id", "id", 42): 92,
    ("DISTRACTOR", "raw_customers", "last_name", "id", 7): "M.",
}


class SetFieldNullMutation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["SET_FIELD_NULL"]
    purpose: Literal["FAULT", "DISTRACTOR"]
    relation: Literal["raw_payments", "raw_orders", "raw_customers"]
    column: Literal["id", "user_id", "last_name"]
    selector_column: Literal["id", "order_id"]
    selector_value: StrictInt
    expected_value: StrictInt | StrictStr

    @model_validator(mode="after")
    def validate_frozen_target(self) -> Self:
        key = (
            self.purpose,
            self.relation,
            self.column,
            self.selector_column,
            self.selector_value,
        )
        if _M8_NULL_TARGETS.get(key) != self.expected_value:
            raise ValueError("unsupported required-field NULL mutation")
        return self
```

Add `StrictInt` to the existing Pydantic imports. Do not weaken `StrictInt | StrictStr` to an unvalidated `object` or accept caller-provided identifiers outside `_M8_NULL_TARGETS`.

Add `SetFieldNullMutation` to the discriminated `ScenarioMutation` union. Extend the evidence-gap contract without allowing kind/tool mismatches:

```python
class ObservableEvidenceGap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gap_kind: Literal[
        "RELATION_SCHEMA",
        "RELATION_DATA_PROFILE",
        "TRANSFORMATION_DEFINITION",
    ]
    subject: StrictStr
    reason_code: Literal["NOT_OBSERVABLE", "RELATION_NOT_ALLOWED"]
    tool_name: Literal[
        "get_relation_schema",
        "get_relation_data_profile",
    ] | None

    @model_validator(mode="after")
    def validate_tool_binding(self) -> Self:
        expected = {
            "RELATION_SCHEMA": "get_relation_schema",
            "RELATION_DATA_PROFILE": "get_relation_data_profile",
            "TRANSFORMATION_DEFINITION": None,
        }[self.gap_kind]
        if self.tool_name != expected:
            raise ValueError("observable evidence gap/tool mismatch")
        return self
```

Replace the single distractor model with one strict discriminated union:

```python
class NullableColumnSchemaDriftDistractor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["NULLABLE_COLUMN_SCHEMA_DRIFT"]
    relation: Literal["raw_payments"]
    column: Literal["source_batch_note"]
    data_type: Literal["text"]
    nullable: Literal[True]


class NullableFieldNullDistractor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["NULLABLE_FIELD_NULL"]
    relation: Literal["raw_customers"]
    column: Literal["last_name"]
    selector_column: Literal["id"]
    selector_value: Literal[7]


DistractorSpec = Annotated[
    NullableColumnSchemaDriftDistractor | NullableFieldNullDistractor,
    Field(discriminator="kind"),
]
```

In `ScenarioSpec.validate_contract`, require every `REQUIRED_FIELD_NULL` scenario to contain exactly one `purpose="FAULT"` mutation. Require both test roles to contain exactly one matching `purpose="DISTRACTOR"` mutation and matching distractor declaration; require the development role to contain none. Reject `SET_FIELD_NULL` under another fault family.

- [ ] **Step 4: Add the exact three ScenarioSpec files.**

Create `required_null_payment_id.json` with this complete contract:

```json
{
  "schema_version": "scenario.v1",
  "incident_case_id": "required_null_payment_id",
  "suite": "P1",
  "fault_family": "REQUIRED_FIELD_NULL",
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
    "summary": "A required-field dbt test failed while processing payment data.",
    "subjects": [
      "test.jaffle_shop.not_null_stg_payments_payment_id.c19cc50075",
      "raw_payments"
    ],
    "logical_observed_at": "2018-04-09T12:00:00Z",
    "observations": [
      {
        "kind": "DBT_TEST_FAILURE",
        "subject": "test.jaffle_shop.not_null_stg_payments_payment_id.c19cc50075",
        "value": "fail"
      }
    ]
  },
  "reset_and_injection_contract": {
    "schema_version": "reset_injection.v1",
    "mutations": [
      {
        "kind": "SET_FIELD_NULL",
        "purpose": "FAULT",
        "relation": "raw_payments",
        "column": "id",
        "selector_column": "order_id",
        "selector_value": 1,
        "expected_value": 1
      }
    ],
    "restore_strategy": "FULL_REFRESH_BASELINE"
  },
  "ground_truth_or_acceptable_root_causes": ["SOURCE_REQUIRED_FIELD_NULL"],
  "direct_failure": "test.jaffle_shop.not_null_stg_payments_payment_id.c19cc50075",
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
```

Create `required_null_order_customer_a.json`:

```json
{
  "schema_version": "scenario.v1",
  "incident_case_id": "required_null_order_customer_a",
  "suite": "P1",
  "fault_family": "REQUIRED_FIELD_NULL",
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
    "signal_code": "DBT_TEST_FAILED",
    "summary": "A required-field dbt test failed in the order pipeline.",
    "subjects": [
      "test.jaffle_shop.not_null_orders_customer_id.c5f02694af",
      "raw_orders",
      "raw_customers"
    ],
    "logical_observed_at": "2018-04-09T12:00:00Z",
    "observations": [
      {
        "kind": "DBT_TEST_FAILURE",
        "subject": "test.jaffle_shop.not_null_orders_customer_id.c5f02694af",
        "value": "fail"
      }
    ]
  },
  "reset_and_injection_contract": {
    "schema_version": "reset_injection.v1",
    "mutations": [
      {
        "kind": "SET_FIELD_NULL",
        "purpose": "FAULT",
        "relation": "raw_orders",
        "column": "user_id",
        "selector_column": "id",
        "selector_value": 42,
        "expected_value": 92
      },
      {
        "kind": "SET_FIELD_NULL",
        "purpose": "DISTRACTOR",
        "relation": "raw_customers",
        "column": "last_name",
        "selector_column": "id",
        "selector_value": 7,
        "expected_value": "M."
      }
    ],
    "restore_strategy": "FULL_REFRESH_BASELINE"
  },
  "ground_truth_or_acceptable_root_causes": ["SOURCE_REQUIRED_FIELD_NULL"],
  "direct_failure": "test.jaffle_shop.not_null_orders_customer_id.c5f02694af",
  "affected_assets": ["model.jaffle_shop.orders"],
  "observable_evidence_contract": {
    "schema_version": "observable_evidence.v1",
    "schema_relations": ["raw_orders", "raw_customers"],
    "profile_relations": ["raw_orders", "raw_customers"],
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
  "distractors": [
    {
      "kind": "NULLABLE_FIELD_NULL",
      "relation": "raw_customers",
      "column": "last_name",
      "selector_column": "id",
      "selector_value": 7
    }
  ],
  "expected_status": "CONFIRMED"
}
```

Create `required_null_order_customer_b.json` as this complete standalone ScenarioSpec:

```json
{
  "schema_version": "scenario.v1",
  "incident_case_id": "required_null_order_customer_b",
  "suite": "P1",
  "fault_family": "REQUIRED_FIELD_NULL",
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
    "signal_code": "DBT_TEST_FAILED",
    "summary": "A required-field dbt test failed in the order pipeline.",
    "subjects": [
      "test.jaffle_shop.not_null_orders_customer_id.c5f02694af",
      "raw_orders",
      "raw_customers"
    ],
    "logical_observed_at": "2018-04-09T12:00:00Z",
    "observations": [
      {
        "kind": "DBT_TEST_FAILURE",
        "subject": "test.jaffle_shop.not_null_orders_customer_id.c5f02694af",
        "value": "fail"
      }
    ]
  },
  "reset_and_injection_contract": {
    "schema_version": "reset_injection.v1",
    "mutations": [
      {
        "kind": "SET_FIELD_NULL",
        "purpose": "FAULT",
        "relation": "raw_orders",
        "column": "user_id",
        "selector_column": "id",
        "selector_value": 42,
        "expected_value": 92
      },
      {
        "kind": "SET_FIELD_NULL",
        "purpose": "DISTRACTOR",
        "relation": "raw_customers",
        "column": "last_name",
        "selector_column": "id",
        "selector_value": 7,
        "expected_value": "M."
      }
    ],
    "restore_strategy": "FULL_REFRESH_BASELINE"
  },
  "ground_truth_or_acceptable_root_causes": [
    "SOURCE_REQUIRED_FIELD_NULL",
    "TRANSFORMATION_REQUIRED_FIELD_NULL"
  ],
  "direct_failure": "test.jaffle_shop.not_null_orders_customer_id.c5f02694af",
  "affected_assets": ["model.jaffle_shop.orders"],
  "observable_evidence_contract": {
    "schema_version": "observable_evidence.v1",
    "schema_relations": ["raw_orders", "raw_customers"],
    "profile_relations": ["raw_customers"],
    "history_relations": [],
    "unresolved_gaps": [
      {
        "gap_kind": "RELATION_DATA_PROFILE",
        "subject": "raw_orders",
        "reason_code": "RELATION_NOT_ALLOWED",
        "tool_name": "get_relation_data_profile"
      },
      {
        "gap_kind": "TRANSFORMATION_DEFINITION",
        "subject": "model.jaffle_shop.stg_orders",
        "reason_code": "NOT_OBSERVABLE",
        "tool_name": null
      }
    ]
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
  "distractors": [
    {
      "kind": "NULLABLE_FIELD_NULL",
      "relation": "raw_customers",
      "column": "last_name",
      "selector_column": "id",
      "selector_value": 7
    }
  ],
  "expected_status": "INSUFFICIENT_EVIDENCE"
}
```

Do not introduce inheritance, `$ref`, overlays, or runtime composition.

- [ ] **Step 5: Run the scenario contract suite and a placeholder scan.**

```powershell
uv run pytest tests/unit/test_scenarios.py tests/unit/test_p1_isolation.py -q
rg -n 'TODO|TBD|PLACEHOLDER|similar to|same as above' src/data_incident_gym/scenarios.py config/scenarios/required_null_payment_id.json config/scenarios/required_null_order_customer_a.json config/scenarios/required_null_order_customer_b.json tests/unit/test_scenarios.py
```

Expected: tests pass and `rg` returns no matches.

- [ ] **Step 6: Commit only Task 1 files when implementation has been authorized.**

```powershell
git add src/data_incident_gym/scenarios.py tests/unit/test_scenarios.py config/scenarios/required_null_payment_id.json config/scenarios/required_null_order_customer_a.json config/scenarios/required_null_order_customer_b.json docs/superpowers/plans/2026-08-31-m8-required-field-null-family.md
git diff --cached --check
git commit -m "feat: define M8 required-null scenarios"
```

Do not stage `docs/requirements.md`, `AGENTS.md`, `decision.md`, earlier plans, or reports.

## Task 2: Implement exact reversible NULL injection and independent test-failure verification

**Files:**

- Modify: `src/data_incident_gym/lab.py`
- Modify: `src/data_incident_gym/lab_verifier.py`
- Modify: `tests/unit/test_lab.py`
- Modify: `tests/unit/test_lab_verifier.py`
- Modify: `tests/integration/test_incident_lab.py`

- [ ] **Step 1: Write RED unit tests for state transitions and dbt `fail` handling.**

Cover only these state transitions:

- healthy selector row has `expected_value` and total target-column NULL count `0`;
- prepare changes exactly one selected value to `NULL` and total NULL count to `1`;
- a second prepare is rejected as not healthy;
- restore accepts either the injected NULL or an already-restored expected value, but rejects any third value or non-unique selector;
- `run_results` status `fail` enters `failed_nodes`;
- a failed test's distance-1 model parent is the exact affected model.

Use fake cursors for SQL shape/row-count tests; do not introduce a SQL parser or a new database abstraction.

- [ ] **Step 2: Run the focused RED tests.**

```powershell
uv run pytest tests/unit/test_lab.py tests/unit/test_lab_verifier.py -q
```

Expected: failures identify the missing `SetFieldNullMutation`, `fail` status, and test-to-model mapping.

- [ ] **Step 3: Implement three small SQL helpers and thread the mutation through existing state gates.**

In `lab.py`, use validated identifiers and bound values only:

```python
def _read_null_target(self, mutation: SetFieldNullMutation) -> object:
    statement = sql.SQL("SELECT {} FROM {}.{} WHERE {} = %s").format(
        sql.Identifier(mutation.column),
        sql.Identifier(self.settings.postgres_schema),
        sql.Identifier(mutation.relation),
        sql.Identifier(mutation.selector_column),
    )
    with (
        self.db_connect(**self._connection_kwargs()) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(statement, (mutation.selector_value,))
        rows = cursor.fetchall()
    if len(rows) != 1:
        raise InvalidIncidentState("NULL mutation selector must match exactly one row")
    return rows[0][0]


def _null_count(self, mutation: SetFieldNullMutation) -> int:
    statement = sql.SQL("SELECT count(*) FROM {}.{} WHERE {} IS NULL").format(
        sql.Identifier(self.settings.postgres_schema),
        sql.Identifier(mutation.relation),
        sql.Identifier(mutation.column),
    )
    with (
        self.db_connect(**self._connection_kwargs()) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)
        row = cursor.fetchone()
    if row is None:
        raise InvalidIncidentState("NULL mutation count is unavailable")
    return int(row[0])


def _write_null_target(
    self,
    mutation: SetFieldNullMutation,
    *,
    expected_current: object,
    replacement: object,
) -> None:
    statement = sql.SQL(
        "UPDATE {}.{} SET {} = %s "
        "WHERE {} = %s AND {} IS NOT DISTINCT FROM %s"
    ).format(
        sql.Identifier(self.settings.postgres_schema),
        sql.Identifier(mutation.relation),
        sql.Identifier(mutation.column),
        sql.Identifier(mutation.selector_column),
        sql.Identifier(mutation.column),
    )
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
            raise InvalidIncidentState("NULL mutation must update exactly one row")
```

Use these helpers in the existing methods:

- `_ensure_healthy_for_prepare`: value must equal `expected_value`, NULL count must be `0`.
- `_apply_mutations`: call `_write_null_target(mutation, expected_current=mutation.expected_value, replacement=None)`.
- `_validate_prepared_state`: selected value must be `None`, total NULL count must be `1`.
- `_restore_mutations`: if already `expected_value`, do nothing; if `None`, restore; otherwise fail closed.
- `_verify_restored`: selected value equals `expected_value` and total NULL count is `0`.

Keep mutation ordering and reverse restoration ordering unchanged so the test twin applies fault first, distractor second, and restores distractor first.

- [ ] **Step 4: Generalize the private verifier only as far as M8 requires.**

In `lab_verifier.py`:

1. Treat `status in {"error", "fail"}` as failed.
2. Replace `_model_descendants` with a helper that preserves existing model-failure behavior and adds this exact test behavior:

```python
if direct_node["resource_type"] == "test":
    affected = {
        node_id
        for node_id in manifest["parent_map"][direct_failure]
        if manifest["nodes"][node_id]["resource_type"] == "model"
    }
    if not affected:
        raise LabVerificationError("failed test has no tested model")
    return affected
```

Do not include the test node, seeds, deeper upstream models, or every lineage descendant in `affected_assets`.

3. Let `_validate_mutation_schema` preserve baseline schema unchanged for `SetFieldNullMutation`.
4. Add an independent live-data check that each declared NULL mutation has exactly one selected NULL and exactly one total NULL in its target column.
5. Compare public current-profile relation names with the exact union of declared `profile` and `history` relations, and compare public history names exactly with declared history relations. This prevents an undeclared `raw_orders` profile from leaking into variant B.
6. When a declared NULL mutation relation is publicly profiled, verify the saved aggregate column fact has `null_count == 1`. When it is not publicly profiled, require that it is absent rather than synthesizing or inspecting a public fact.

- [ ] **Step 5: Add focused real-PostgreSQL assertions to the existing integration seam.**

Keep the current `SUPPORTED_SCENARIO_IDS` parameterization. Add one M8-only test that proves:

```python
expected_profiles = {
    "required_null_payment_id": {("raw_payments", "id"): 1},
    "required_null_order_customer_a": {
        ("raw_orders", "user_id"): 1,
        ("raw_customers", "last_name"): 1,
    },
    "required_null_order_customer_b": {
        ("raw_customers", "last_name"): 1,
    },
}
```

For variant B, additionally assert that `raw_orders` is absent from the public current profile while the private verifier still proves the injected row. After `restore`, query the three fixed selectors and assert the exact healthy values `1`, `92`, and `M.` with zero NULLs in those target columns.

- [ ] **Step 6: Run unit and integration verification.**

```powershell
uv run pytest tests/unit/test_lab.py tests/unit/test_lab_verifier.py -q
uv run pytest tests/integration/test_incident_lab.py -q
```

Expected: all eight supported scenarios pass the generic integration path; M8 failure nodes have test IDs and recovery restores exact source values.

- [ ] **Step 7: Commit only Task 2 files.**

```powershell
git add src/data_incident_gym/lab.py src/data_incident_gym/lab_verifier.py tests/unit/test_lab.py tests/unit/test_lab_verifier.py tests/integration/test_incident_lab.py
git diff --cached --check
git commit -m "feat: inject and verify required null faults"
```

## Task 3: Extend the shared diagnosis ontology and profile-gap protocol

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

- [ ] **Step 1: Write RED tests for the additive protocol.**

The focused tests must prove:

- `UnresolvedEvidence` accepts `RELATION_DATA_PROFILE` and still rejects arbitrary evidence kinds;
- both prompts expose the same five-code ontology;
- the tool surface remains exactly six and both policies retain the same budget;
- a blocked `PROFILE_RELATION` Kernel gap can bind only a `RELATION_DATA_PROFILE` unresolved declaration for the same subject and error code;
- a failed-test node error plus upstream lineage can support the distance-1 tested model claim;
- a profile gap cannot be satisfied by a blocked schema call or by a different relation.

- [ ] **Step 2: Run the focused RED tests.**

```powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_policy_fairness.py -q
```

- [ ] **Step 3: Extend the public unresolved-evidence enum and version the changed controller contract.**

Change only the allowed literal:

```python
evidence_kind: Literal[
    "RELATION_SCHEMA",
    "RELATION_DATA_PROFILE",
    "TRANSFORMATION_DEFINITION",
]
```

In `diagnostic_agent.py`, define one tuple used to initialize every Kernel:

```python
P1_ROOT_CAUSE_CODES = (
    "SOURCE_SCHEMA_COLUMN_RENAMED",
    "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
    "TRANSFORMATION_COLUMN_CAST_CHANGED",
    "SOURCE_REQUIRED_FIELD_NULL",
    "TRANSFORMATION_REQUIRED_FIELD_NULL",
)
```

Use it in `_kernel`. Because the structured decision enum and both prompt contracts change, set:

```python
KERNEL_PROMPT_VERSION = "p1.kernel.v3"
STATIC_PROMPT_VERSION = "p1.static.v3"
CONTROLLER_PROTOCOL_VERSION = "p1.controller.v2"
```

Do not change the budget constants, tool names, Diagnosis schema version, or artifact schema.

- [ ] **Step 4: Teach both policies the same generic NULL reasoning and test-node asset semantics.**

Update both prompts with the two new ontology values and these policy-neutral rules:

```text
For a required-field NULL, confirm SOURCE_REQUIRED_FIELD_NULL only when a matching upstream
relation profile reports a positive null_count for the implicated column. A downstream
not-null failure without that source profile is also compatible with a transformation that
introduced the NULL, so return INSUFFICIENT_EVIDENCE when the source profile and transformation
definition are both unavailable.

When the direct failed node is a dbt test, affected assets are its distance-1 upstream model
dependencies, not the test node or the upstream seed relations. Bind those model claims to
the failed-test node error and upstream-lineage evidence.
```

Do not add case IDs, fixed row selectors, expected NULL counts, or relation-specific tool ordering to either prompt.

- [ ] **Step 5: Bind blocked profile gaps and test-model claims in the Kernel.**

Mirror the existing blocked-schema logic:

```python
blocked_profiles = {
    (gap.subject, gap.error_code)
    for gap in self._gaps
    if gap.gap_kind is EvidenceGapKind.PROFILE_RELATION
    and gap.status is EvidenceGapStatus.BLOCKED
}
```

Require a `RELATION_DATA_PROFILE` unresolved declaration to match that set. When deriving unresolved declarations, map:

- `DISCRIMINATE_SCHEMA` -> `RELATION_SCHEMA`;
- `PROFILE_RELATION` -> `RELATION_DATA_PROFILE`.

In the affected-asset gate, retain the existing direct-model/downstream rule and add acceptance of an upstream lineage record whose `node_id` equals the failed test and whose matching model node has `distance == 1`.

- [ ] **Step 6: Run protocol, fairness, and M7 controller regressions.**

```powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_policy_fairness.py tests/unit/test_m7_contracts.py -q
```

Expected: all pass; fairness still proves byte-identical business Tool Schemas and equal budgets.

- [ ] **Step 7: Commit only Task 3 files.**

```powershell
git add src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/diagnostic_kernel.py src/data_incident_gym/prompts/static_skill.md src/data_incident_gym/prompts/diagnostic_kernel.md tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_policy_fairness.py
git diff --cached --check
git commit -m "feat: add required null diagnosis protocol"
```

## Task 4: Make deterministic evaluation distinguish the real source NULL from the distractor

**Files:**

- Modify: `src/data_incident_gym/evaluation.py`
- Modify: `tests/unit/test_evaluation.py`

- [ ] **Step 1: Write three adversarial RED evaluator tests.**

1. A correct `SOURCE_REQUIRED_FIELD_NULL` root claim with failed-test evidence, upstream lineage, and the fault relation/column profile passes.
2. The same claim citing only `raw_customers.last_name.null_count == 1` fails `CLAIM_EVIDENCE_COMPATIBLE` even though that distractor is a real observed NULL.
3. The insufficient result passes only when it declares both exact gaps and the trace contains exactly one failed `get_relation_data_profile(relation_name="raw_orders")` attempt; a schema failure or a successful profile call does not substitute.

Also cover the distance-1 `model.jaffle_shop.orders` affected claim and reject the failed test ID as an affected asset.

- [ ] **Step 2: Run the evaluator RED tests.**

```powershell
uv run pytest tests/unit/test_evaluation.py -q
```

- [ ] **Step 3: Add one explicit source-NULL compatibility branch.**

Import `SetFieldNullMutation`, select the sole `purpose == "FAULT"` mutation, and require the matching aggregate fact:

```python
fault = next(
    (
        mutation
        for mutation in scenario.reset_and_injection_contract.mutations
        if isinstance(mutation, SetFieldNullMutation)
        and mutation.purpose == "FAULT"
    ),
    None,
)
profile = next(
    (
        record.content
        for record in root_records
        if isinstance(record.content, RelationDataProfileFact)
        and fault is not None
        and record.content.relation_name == fault.relation
    ),
    None,
)
column = next(
    (
        item
        for item in profile.snapshot.columns
        if fault is not None and item.column_name == fault.column
    ),
    None,
) if profile is not None else None
return (
    root_cause_code == "SOURCE_REQUIRED_FIELD_NULL"
    and column is not None
    and column.null_count == 1
)
```

Keep the existing rename/type branches unchanged. Do not accept `TRANSFORMATION_REQUIRED_FIELD_NULL` for a confirmable source-injection scenario.

For asset claims, accept the tested model only when upstream lineage starts at `scenario.direct_failure` and the matching model is distance `1`. Keep the existing direct-model and downstream-model rules for M7.

Tighten `_insufficiency_matches` so the single matching failed tool event must have `error_code == gap.reason_code`; a merely non-empty error code is not sufficient.

- [ ] **Step 4: Run evaluator and artifact regressions.**

```powershell
uv run pytest tests/unit/test_evaluation.py tests/unit/test_artifacts.py tests/unit/test_evaluation_runner.py -q
```

- [ ] **Step 5: Commit only Task 4 files.**

```powershell
git add src/data_incident_gym/evaluation.py tests/unit/test_evaluation.py
git diff --cached --check
git commit -m "feat: score required null evidence"
```

## Task 5: Extend the cumulative deterministic policy matrix and document 7/17

**Files:**

- Rename: `tests/e2e/test_m7_policy_matrix.py` -> `tests/e2e/test_p1_policy_matrix.py`
- Modify: `tests/e2e/test_p1_policy_matrix.py`
- Modify: `README.md`

- [ ] **Step 1: Rename the cumulative matrix and make its cardinality explicit.**

```powershell
git mv tests/e2e/test_m7_policy_matrix.py tests/e2e/test_p1_policy_matrix.py
```

Set:

```python
MATRIX_CASES = P1_M7_SCENARIO_IDS + P1_M8_SCENARIO_IDS
MATRIX_STRATEGIES = (
    DiagnosticStrategy.STATIC_SKILL,
    DiagnosticStrategy.DIAGNOSTIC_KERNEL,
)
assert len(MATRIX_CASES) == 7
assert len(MATRIX_CASES) * len(MATRIX_STRATEGIES) == 14
```

Leave `tests/e2e/test_real_model_m7_smoke.py` unchanged at exactly 4 cases x 2 strategies = 8 cells.

- [ ] **Step 2: Add one evidence-driven M8 `FunctionModel` branch.**

The branch must derive every identifier from successful evidence except the public relations named in the current runtime. Its sequence is bounded as follows:

1. `get_dbt_run_results`;
2. `get_dbt_node_error` for the returned failed test;
3. upstream `get_dbt_lineage` from that test;
4. `get_relation_schema` for the implicated source;
5. read the distractor profile when declared;
6. request the implicated source profile;
7. finalize.

For confirmable scenarios, the source profile succeeds and the final root claim cites the failed-test and matching source-profile records. The affected-model claim cites the upstream-lineage record.

The top-level diagnosis `evidence_ids` must include the successful run-results, node-error, lineage, schema, and profile records required by the ScenarioSpec; claim-level IDs remain the smaller compatible subsets.

For variant B, the source-profile request fails with `RELATION_NOT_ALLOWED`. Register both M8 hypotheses before that Kernel call and return the two exact unresolved declarations from Section 0. Do not turn the distractor NULL into a root claim.

This is deterministic protocol coverage, not a production heuristic and not a fake accuracy result.

- [ ] **Step 3: Add M8-specific matrix assertions.**

For all six new cells assert:

- evaluator status `PASSED` and no failed check codes;
- exact expected terminal status for the scenario role;
- all six artifact filenames exist;
- confirmable root cause is exactly `SOURCE_REQUIRED_FIELD_NULL`;
- confirmable affected models equal the private exact set;
- insufficient root cause and affected assets are empty;
- variant B has exactly the two expected unresolved declarations;
- no trace event invokes a tool outside the existing six-tool allowlist.

- [ ] **Step 4: Run the exact 14-cell deterministic matrix.**

```powershell
uv run pytest tests/e2e/test_p1_policy_matrix.py -q -s
```

Expected: `14 passed`. This command makes no real-model calls.

- [ ] **Step 5: Update README with facts only.**

Update the current-state section and scenario table to state:

- M8 deterministic implementation brings the catalog to 7/17 P1 scenarios;
- the three new case IDs, roles, public symptom, and expected terminal;
- M7 real-model development observation remains 1/8 and is not a formal benchmark result;
- M8 performed no real-model run and does not change the frozen 94-run contract;
- the ordinary verification command remains `tests/e2e -m 'not real_model'`.

Do not claim model-quality improvement, strategy superiority, production readiness, or a passed M8 real-model smoke.

- [ ] **Step 6: Run README/path and smoke-cardinality regressions.**

```powershell
uv run pytest tests/e2e/test_real_model_m7_smoke.py --collect-only -m real_model -q
rg -n '7/17|1/8|required_null_payment_id|required_null_order_customer_a|required_null_order_customer_b' README.md
```

Expected: the smoke collection remains exactly eight items, and README contains each required fact.

- [ ] **Step 7: Commit only Task 5 files.**

```powershell
git add tests/e2e/test_m7_policy_matrix.py tests/e2e/test_p1_policy_matrix.py README.md
git diff --cached --check
git commit -m "test: close M8 deterministic policy matrix"
```

The old path in the first `git add` is intentional for recording the rename; do not stage other files.

## Task 6: Run the deterministic milestone gate and hand off without model calls or push

**Files:**

- No new production files.
- Modify only a Task 1-5 file if verification exposes a direct M8 defect; add no workaround layer or unrelated cleanup.

- [ ] **Step 1: Confirm scope before the full run.**

```powershell
git status --short
git diff --check
git diff --name-only HEAD
```

Expected: only planned M8 files plus the known pre-existing user-owned files appear. Verify explicitly that `docs/requirements.md`, `AGENTS.md`, and `decision.md` are not staged.

- [ ] **Step 2: Run static and package checks.**

```powershell
uv run ruff check .
uv lock --check
uv build
git diff --check
```

Expected: all commands exit `0`.

- [ ] **Step 3: Run the full unit suite.**

```powershell
uv run pytest tests/unit -q
```

Expected: all pass; do not accept a lower collected-test count caused by accidental deletion.

- [ ] **Step 4: Run the full integration suite against PostgreSQL.**

```powershell
uv run pytest tests/integration -q
```

Expected: all pass and the final database state is healthy.

- [ ] **Step 5: Run all non-real-model E2E tests.**

```powershell
uv run pytest tests/e2e -m 'not real_model' -q -s
```

Expected: all pass, including the 14-cell cumulative deterministic matrix and existing replay regressions. No model endpoint is contacted.

If Windows returns the already recorded native exit codes such as `3221225477` or `3221226505`, preserve the exact failing command and distinguish **environment-unverified** from a business assertion failure. Do not add affinity, parser, retry, dependency, or platform workarounds to product code and do not loop until green.

- [ ] **Step 6: Run focused M8 checks once more after the full suite.**

```powershell
uv run pytest tests/unit/test_scenarios.py tests/unit/test_lab.py tests/unit/test_lab_verifier.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_kernel.py tests/unit/test_evaluation.py -q
uv run pytest tests/integration/test_incident_lab.py -q
uv run pytest tests/e2e/test_p1_policy_matrix.py -q -s
```

Expected: all pass.

- [ ] **Step 7: Perform the final leakage and placeholder audit.**

```powershell
$m8Files = @(
  'src/data_incident_gym/scenarios.py',
  'src/data_incident_gym/lab.py',
  'src/data_incident_gym/lab_verifier.py',
  'src/data_incident_gym/diagnosis.py',
  'src/data_incident_gym/diagnostic_agent.py',
  'src/data_incident_gym/diagnostic_kernel.py',
  'src/data_incident_gym/evaluation.py',
  'src/data_incident_gym/prompts/static_skill.md',
  'src/data_incident_gym/prompts/diagnostic_kernel.md',
  'tests/e2e/test_p1_policy_matrix.py'
)
rg -n 'required_null_(payment_id|order_customer_[ab])|selector_value|expected_value' $m8Files
rg -n 'TODO|TBD|PLACEHOLDER|similar to|same as above' $m8Files
```

Expected:

- case IDs and private row selectors appear only in scenario/lab/test-side code, never in either production prompt or policy branch;
- no placeholders remain.

- [ ] **Step 8: Inspect the final commits and stop at the publication gate.**

```powershell
git status --short
git log -6 --oneline
git diff HEAD~5..HEAD --stat
```

Report:

- exact HEAD;
- verification commands/results;
- 7/17 catalog status;
- confirmation that no real-model request ran;
- confirmation that no push occurred;
- any Windows-only command left environment-unverified.

Do not push. If the user later authorizes a push, first verify the intended remote default branch, push the exact reviewed HEAD, and observe Ubuntu CI with `gh` before calling the remote state verified.

## Final M8 acceptance checklist

- [ ] Exactly three M8 scenarios exist and the cumulative P1 catalog is exactly 7/17.
- [ ] The development case mutates only `raw_payments.id` for the frozen selector and confirms only `SOURCE_REQUIRED_FIELD_NULL`.
- [ ] Test variants A and B share identical brief, injected database state, direct failure, affected model, and distractor.
- [ ] Variant A observes the source NULL and ignores the real but harmless customer NULL.
- [ ] Variant B cannot observe the source profile or transformation definition and can pass only as `INSUFFICIENT_EVIDENCE` with both exact gaps.
- [ ] `fail` test results are recorded as failed nodes, and the tested model—not the test node—is the affected asset.
- [ ] Private verification proves exact row mutation, exact public profile scope, exact failed node/model set, and exact recovery values.
- [ ] Static Skill and Diagnostic Kernel share the five-code ontology, six business tools, equal budgets, public context, Diagnosis schema, evaluator, and artifact contract.
- [ ] Kernel profile gaps are typed, bound to one blocked tool attempt, and fail closed on mismatched subjects or evidence kinds.
- [ ] Evaluator accepts only the matching fault relation/column profile; the distractor profile cannot support the root claim.
- [ ] The cumulative deterministic policy matrix is exactly 7 cases x 2 strategies = 14 passing cells.
- [ ] Every deterministic cell writes exactly six canonical artifacts.
- [ ] The M7 real-model smoke harness remains exactly eight cells and is not rerun.
- [ ] No budgets, tool schemas, ProfileSpec, raw-row access, SQL freedom, repair behavior, benchmark manifest, or formal metric changed.
- [ ] Unit, integration, non-real-model E2E, Ruff, lock, build, and diff checks pass or any known Windows native failure is reported strictly as environment-unverified.
- [ ] `docs/requirements.md`, `AGENTS.md`, `decision.md`, earlier plans, and reports remain preserved and unstaged.
- [ ] No real-model call and no push occurred during M8 implementation.

## Implementation handoff

This document authorizes no code execution by itself. When the user separately authorizes M8 implementation, execute Tasks 1-6 in order, keep each commit scoped to its explicit file list, and stop at the push/Ubuntu-CI gate.
