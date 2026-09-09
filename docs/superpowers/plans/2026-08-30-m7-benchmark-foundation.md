# M7 Benchmark Contracts, Static Skill Baseline, and Type-Change Twin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the first honest P1 benchmark slice: four isolated scenarios, the final six read-only evidence tools, a Static Skill baseline, Diagnostic Kernel v2, and a strategy-neutral runner/evaluator/artifact contract without running the fixed 94-run benchmark.

**Architecture:** Replace the M6 Kernel-shaped public run contract with one policy-neutral diagnosis contract and two policy adapters. Lab-only `ScenarioSpec` and Ground Truth remain on the management/evaluator side; the diagnostic side receives only a run-bound `IncidentBrief`, dbt artifacts, aggregate snapshots, and the shared six-tool interface. Static Skill relies on a fixed general playbook and no typed investigation state. Diagnostic Kernel separately validates structured intent, hypotheses, evidence gaps, claim bindings, budgets, and terminal gates while invoking the exact same business tool schemas.

**Tech Stack:** Python 3.12, Pydantic 2, PydanticAI 2.34, Typer, psycopg 3, PostgreSQL, dbt-core/dbt-postgres, pytest/pytest-asyncio, Ruff, Jinja2, PowerShell 7, GitHub Actions Ubuntu.

---

## 0. Approved scope and non-negotiable decisions

This plan implements only M7 from [`docs/requirements.md`](../../requirements.md). It does not implement M8-M12 fault families, a frozen Benchmark Manifest, aggregate P1 metrics, the 94 formal real-model runs, user-defined faults, free SQL, raw-row access, repair actions, Airflow, OpenLineage, or a Web UI.

### M7 scenario matrix

| ID | P1 role | Environment | Observable evidence contract | Expected status |
| --- | --- | --- | --- | --- |
| `schema_type_change_payment_amount` | `DEV_CONFIRMABLE` | Existing `raw_payments.amount integer -> text`; direct failure `model.jaffle_shop.stg_payments` | Current source schema is observable | `CONFIRMED` |
| `schema_type_change_order_customer_a` | `TEST_CONFIRMABLE` | `raw_orders.user_id integer -> text` plus harmless nullable `raw_payments.source_batch_note text` distractor; direct failure `model.jaffle_shop.customers` | `raw_orders`, `raw_customers`, and `raw_payments` schema snapshots are observable | `CONFIRMED` |
| `schema_type_change_order_customer_b` | `TEST_INSUFFICIENT` | Same deterministic database mutation and symptom as variant A | Run/node/lineage/profile/history remain available, but the decisive `raw_orders` schema and transformation definition are outside the declared telemetry coverage | `INSUFFICIENT_EVIDENCE` |
| `order_volume_pattern_a` | `NO_INCIDENT_CONTROL` | No mutation; healthy dbt build; the 2018-04-02 Monday order count is 1 after 3 on 2018-03-26 | Run success plus current aggregates and prior Monday counts prove that 1 lies inside the observed healthy Monday range 1-3 | `NO_INCIDENT` |

`schema_rename_payment_amount` remains a P0 regression scenario and is not part of the 4/17 P1 count.

For the insufficient twin, the two compatible hypotheses are:

1. `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED` on `raw_orders.user_id`.
2. `TRANSFORMATION_COLUMN_CAST_CHANGED` on the `stg_orders.customer_id` projection.

The decisive unresolved gaps are `RELATION_SCHEMA/raw_orders` and `TRANSFORMATION_DEFINITION/model.jaffle_shop.stg_orders`. Only the first is an invocable evidence tool; its run-bound `RELATION_NOT_ALLOWED` result records the declared telemetry coverage gap. No tool is globally disabled, no empty result is fabricated, and all other evidence tools remain usable.

### Public versus private run data

`ScenarioSpec`, accepted answers, role, answerability, expected status, distractors, and evaluator rules are private to Lab/scenario verification/evaluation. Diagnosis receives only:

- random `run_id`;
- a sanitized `IncidentBrief` containing the observable alert;
- `runtime.json` with safe artifact paths, dbt exit code, observable relation names, and ProfileSpec hash;
- dbt `manifest.json` / `run_results.json`;
- run-bound schema and aggregate snapshots;
- the six evidence tools.

The model does not receive `incident_case_id`. The policy-neutral materializer adds case/run identity after model output. Code may carry the case ID solely for identity and artifact routing; neither policy may branch on it.

### Policy-neutral result contract

Both policies produce the same final `Diagnosis`:

- `CONFIRMED`: exactly one root-cause claim, one or more affected-asset claims, compatible Evidence IDs, and no unresolved gaps.
- `INSUFFICIENT_EVIDENCE`: no root cause/assets/claims and at least one typed unresolved evidence item.
- `NO_INCIDENT`: no root cause/assets, at least one positive health claim, and cited run/profile/history evidence.
- `MODEL_ERROR`: controller-produced fixed safe reason code; it is never used for business uncertainty or health.

`DiagnosisRunResult` always ends with a policy-neutral `DIAGNOSIS_TERMINAL` trace event. Kernel runs additionally contain exactly one `KERNEL_STATE` event immediately before it and expose `kernel_state`. Static runs have `kernel_state=None` and never fabricate Kernel hypotheses or gaps.

### Fair tool/control separation

The six business tool schemas contain only:

```text
get_dbt_run_results(run_id)
get_dbt_node_error(run_id, node_id)
get_relation_schema(relation_name)
get_dbt_lineage(node_id, direction)
get_relation_data_profile(relation_name)
get_relation_history(relation_name)
```

Kernel-only `InvestigationIntent` is an exact structured JSON text part paired with one business tool call. The model observation adapter validates and consumes that intent before execution. Static Skill neither emits nor calls this typed control protocol. This preserves identical tool JSON schemas while allowing Kernel-only state control; do not keep the M6 `gap_id` / `hypothesis_ids` fields inside tool arguments.

### Budget and authorization gates

- Every single diagnosis remains capped at 8 model requests, 8 total evidence-tool calls, 2 structured-output retries, and 300 seconds.
- Deterministic FunctionModel/TestModel runs are authorized by plan execution.
- The proposed M7 development smoke is exactly `4 scenarios x 2 policies x 1 run = 8` real-model runs, with no external retry or replacement. It is excluded from the formal 94-run denominator.
- Plan approval does **not** authorize those 8 external model calls or a Git push. Stop for explicit user authorization before Task 12's real-model and remote-CI steps.

## 1. Deep-module map

| Module | Interface owned | Hidden decisions |
| --- | --- | --- |
| `scenarios.py` | `load_scenario_spec(case_id)`, scenario IDs, strict `ScenarioSpec` types | private role/answerability/answers, mutation and telemetry-coverage contracts |
| `profiles.py` | `load_profile_spec()`, `AggregateSnapshotReader` | identifier allowlist, fixed aggregate query construction, output caps, snapshot equality |
| `run_context.py` | `ObservableRunContext`, `IncidentBrief`, active-run publication/resolution | fixed paths, symlink/duplicate-key checks, public/private manifest separation |
| `evidence_tools.py` | six typed read-only evidence calls | artifact validation, lineage/profile intersection, live-versus-snapshot drift rejection |
| `diagnosis.py` | common `Diagnosis`, claims, unresolved evidence, trace, policy identity, run result | policy-neutral cross-field and terminal invariants |
| `diagnostic_kernel.py` | `DiagnosticKernel` and typed `InvestigationState` | intent transitions, provenance, deduplication, sufficiency/health gates, stuck control |
| `diagnostic_agent.py` | `DiagnosisRunner.for_run(run_id, settings, strategy)` | one shared tool registry, two policy adapters, common budgets/error mapping |
| `evaluation.py` | `DeterministicEvaluator.evaluate(scenario, verification, diagnosis_run)` | status/claim/evidence/health scoring and optional Kernel-only audits |
| `evaluation_runner.py` | one scenario/strategy execution | reset -> prepare -> build -> diagnose -> freeze -> private evaluate -> restore -> six files |
| `artifacts.py` | atomic six-file bundle | P1 versions, hashes, conditional Kernel report, cross-file validation |

Do not introduce a `StaticSkillState` that mirrors `InvestigationState`, a second evidence implementation, strategy-specific EvidenceRecord types, case-ID dispatch tables in diagnostic code, or compatibility wrappers for M6-only artifact versions. Replace the M6 public seam and update its callers/tests.

## Task 1: Replace hard-coded incident records with the isolated scenario catalog

**Files:**

- Create: `src/data_incident_gym/scenarios.py`
- Create: `config/scenarios/schema_rename_payment_amount.json`
- Create: `config/scenarios/schema_type_change_payment_amount.json`
- Create: `config/scenarios/schema_type_change_order_customer_a.json`
- Create: `config/scenarios/schema_type_change_order_customer_b.json`
- Create: `config/scenarios/order_volume_pattern_a.json`
- Modify: `src/data_incident_gym/run_context.py`
- Delete: `src/data_incident_gym/incidents.py`
- Delete: `config/incidents/schema_rename_payment_amount.json`
- Delete: `config/incidents/schema_type_change_payment_amount.json`
- Replace: `tests/unit/test_incidents.py` with `tests/unit/test_scenarios.py`
- Modify: all imports of `data_incident_gym.incidents` in `src/` and `tests/`

- [ ] **Step 1: Write RED tests for strict scenario parsing and the exact 4/17 catalog.**

Add tests that assert:

```python
assert P1_M7_SCENARIO_IDS == (
    "schema_type_change_payment_amount",
    "schema_type_change_order_customer_a",
    "schema_type_change_order_customer_b",
    "order_volume_pattern_a",
)
assert REGRESSION_SCENARIO_IDS == ("schema_rename_payment_amount",)

insufficient = load_scenario_spec("schema_type_change_order_customer_b", project_root)
assert insufficient.variant_role is VariantRole.TEST_INSUFFICIENT
assert insufficient.answerability is Answerability.INSUFFICIENT
assert insufficient.expected_status == "INSUFFICIENT_EVIDENCE"
assert set(insufficient.ground_truth_or_acceptable_root_causes) == {
    "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
    "TRANSFORMATION_COLUMN_CAST_CHANGED",
}
assert len(insufficient.observable_evidence_contract.unresolved_gaps) == 2
```

Also reject duplicate JSON keys, unknown IDs, role/status mismatches, an insufficient scenario with fewer than two compatible causes, a control with mutations, a confirmable scenario with no accepted root, and any forbidden-leakage list that omits `SCENARIO_SPEC`, `GROUND_TRUTH`, `VARIANT_ROLE`, `ANSWERABILITY`, or `EXPECTED_STATUS`.

- [ ] **Step 2: Run the focused RED test.**

Run:

```powershell
uv run pytest tests/unit/test_scenarios.py -q
```

Expected: FAIL because `scenarios.py` and the five `scenario.v1` files do not exist.

- [ ] **Step 3: Implement one strict ScenarioSpec union and safe IncidentBrief projection.**

Use closed enums/unions and cross-field validators. The central shape must be:

```python
class VariantRole(StrEnum):
    DEV_CONFIRMABLE = "DEV_CONFIRMABLE"
    TEST_CONFIRMABLE = "TEST_CONFIRMABLE"
    TEST_INSUFFICIENT = "TEST_INSUFFICIENT"
    NO_INCIDENT_CONTROL = "NO_INCIDENT_CONTROL"


class Answerability(StrEnum):
    CONFIRMABLE = "CONFIRMABLE"
    INSUFFICIENT = "INSUFFICIENT"
    NO_INCIDENT = "NO_INCIDENT"


class IncidentBrief(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["incident_brief.v1"]
    signal_code: StrictStr
    summary: StrictStr
    subjects: tuple[StrictStr, ...]
    logical_observed_at: datetime
    observations: tuple[ObservedSignal, ...]


class ScenarioSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["scenario.v1"]
    incident_case_id: CaseId
    suite: Literal["P0_REGRESSION", "P1"]
    fault_family: FaultFamily
    variant_role: VariantRole | None
    answerability: Answerability
    seed: SeedContract
    incident_brief: IncidentBrief
    reset_and_injection_contract: ResetAndInjectionContract
    ground_truth_or_acceptable_root_causes: tuple[RootCauseCode, ...]
    direct_failure: StrictStr | None
    affected_assets: tuple[StrictStr, ...]
    observable_evidence_contract: ObservableEvidenceContract
    required_evidence_types: tuple[StrictStr, ...]
    forbidden_leakage: tuple[ForbiddenLeakage, ...]
    distractors: tuple[DistractorSpec, ...]
    expected_status: Literal[
        "CONFIRMED",
        "INSUFFICIENT_EVIDENCE",
        "NO_INCIDENT",
    ]
```

The P0 rename record uses `suite=P0_REGRESSION` and `variant_role=null`. Every P1 record must use one of the four approved roles. Keep `IncidentBrief` in `run_context.py` so diagnostic code can consume the public type without importing `scenarios.py`.

- [ ] **Step 4: Encode the exact scenario facts from the matrix above.**

For both order/customer type variants, lock healthy/fault column metadata for:

- `raw_orders`: 99 rows; `id integer`, `user_id integer/text`, `order_date date`, `status text`.
- `raw_customers`: 100 rows and unchanged.
- `raw_payments`: 113 rows; add only nullable `source_batch_note text` as the distractor.

For the control, lock the alert observation to `raw_orders/order_count_by_day/2018-04-02 = 1` and comparison `2018-03-26 = 3`. Do not put the healthy range or expected status in `IncidentBrief`.

- [ ] **Step 5: Add a serialization leak test for the public projection.**

```python
private = load_scenario_spec("schema_type_change_order_customer_b", project_root)
public = private.incident_brief.model_dump_json()
for forbidden in (
    "TEST_INSUFFICIENT",
    "INSUFFICIENT_EVIDENCE",
    "TRANSFORMATION_COLUMN_CAST_CHANGED",
    "ground_truth",
    "answerability",
):
    assert forbidden not in public
```

- [ ] **Step 6: Run tests and commit.**

```powershell
uv run pytest tests/unit/test_scenarios.py tests/unit/test_run_context.py -q
uv run ruff check src/data_incident_gym/scenarios.py src/data_incident_gym/run_context.py tests/unit/test_scenarios.py tests/unit/test_run_context.py
git add config/scenarios src/data_incident_gym/scenarios.py src/data_incident_gym/run_context.py tests/unit/test_scenarios.py
git add -u config/incidents src/data_incident_gym/incidents.py tests/unit/test_incidents.py
git commit -m "feat(m7): define isolated scenario catalog"
```

Expected: focused tests PASS; the commit contains the single catalog migration and no diagnostic behavior.

## Task 2: Freeze ProfileSpec and the reusable aggregate snapshot reader

**Files:**

- Create: `src/data_incident_gym/profiles.py`
- Create: `config/profiles/jaffle_shop.v1.json`
- Create: `tests/unit/test_profiles.py`
- Create: `tests/integration/test_profiles.py`
- Modify: `src/data_incident_gym/baseline.py`
- Modify: `tests/unit/test_baseline.py`
- Modify: `tests/e2e/test_baseline_reproducibility.py`

- [ ] **Step 1: Write RED contract tests for a scenario-independent ProfileSpec.**

The checked-in spec must declare all three raw relations and only fixed aggregate capabilities:

```python
spec = load_profile_spec(project_root)
assert spec.schema_version == "profile_spec.v1"
assert tuple(item.relation_name for item in spec.relations) == (
    "raw_customers",
    "raw_orders",
    "raw_payments",
)
assert spec.max_group_rows == 128
assert spec.max_history_points == 90
assert "incident_case_id" not in spec.canonical_json()
assert "root_cause_code" not in spec.canonical_json()
assert "expected_status" not in spec.canonical_json()
```

The schema must support, without containing answer values:

- column null/distinct counts;
- business-key duplicate counts;
- business-fingerprint duplicate counts;
- declared relationship violation counts;
- declared group counts;
- count/ratio history series;
- a time source, optional declared join path, watermark, periodic key, and optional SLA seconds.

Reject duplicate names, non-identifier relation/column names, unknown referenced columns, undeclared join keys, limits above the global caps, free-form SQL/filter fields, case IDs, root-cause codes, and expected numeric thresholds.

- [ ] **Step 2: Run the focused RED test.**

```powershell
uv run pytest tests/unit/test_profiles.py -q
```

Expected: FAIL because ProfileSpec is not implemented.

- [ ] **Step 3: Implement the deep profile module.**

`profiles.py` owns parsing, canonical hashing, fixed query construction, and normalized snapshots. Its public reader is:

```python
class AggregateSnapshotReader:
    def __init__(
        self,
        *,
        schema_name: str,
        spec: ProfileSpec,
        db_connect: DatabaseConnect,
        connection_kwargs: dict[str, object],
    ) -> None:
        self._schema_name = schema_name
        self._spec = spec
        self._db_connect = db_connect
        self._connection_kwargs = connection_kwargs

    def read_current(self, relation_name: str) -> RelationProfileSnapshot:
        return self._read(relation_name, SnapshotKind.CURRENT)

    def read_history(self, relation_name: str) -> RelationHistorySnapshot:
        return self._read(relation_name, SnapshotKind.HISTORY)
```

Build SQL only from closed operation enums plus `psycopg.sql.Identifier` values that already passed ProfileSpec validation. Values such as time bucket and output caps must be query parameters. There is no method accepting SQL, predicates, columns, file paths, URLs, or scenario IDs.

The `raw_orders` spec must include `order_count_by_day` over `order_date` with `DAY_OF_WEEK` periodicity. The future-facing declarations for `raw_payments` must cover `id` duplicate keys, `(order_id,payment_method,amount)` business fingerprints, `order_id -> raw_orders.id` violations, `payment_method` groups, and the declared `raw_orders` join path for order-date history. These are neutral semantics, not expected results.

- [ ] **Step 4: Make the healthy baseline persist the public aggregate snapshot.**

Extend `BaselineBuilder.build()` to write `.dig/baseline/profile_snapshot.json` atomically after the healthy dbt build and read-only role provisioning. It contains ProfileSpec version/hash and normalized current/history snapshots, never ScenarioSpec data.

- [ ] **Step 5: Add real PostgreSQL integration assertions.**

Assert `raw_orders` row count 99, `raw_payments` row count 113, the 2018-04-02 daily count 1, the 2018-03-26 daily count 3, prior Monday range 1-3, timezone-aware observation timestamps, deterministic ordering, and byte-identical repeated snapshot JSON.

- [ ] **Step 6: Run tests and commit.**

```powershell
uv run pytest tests/unit/test_profiles.py tests/unit/test_baseline.py -q
uv run pytest tests/integration/test_profiles.py -q
uv run pytest tests/e2e/test_baseline_reproducibility.py -q
uv run ruff check src/data_incident_gym/profiles.py src/data_incident_gym/baseline.py tests/unit/test_profiles.py tests/integration/test_profiles.py
git add config/profiles src/data_incident_gym/profiles.py src/data_incident_gym/baseline.py tests/unit/test_profiles.py tests/integration/test_profiles.py tests/unit/test_baseline.py tests/e2e/test_baseline_reproducibility.py
git commit -m "feat(m7): freeze bounded aggregate profiles"
```

Expected: all tests PASS; two repeated profile reads are stable and use only aggregate rows.

## Task 3: Replace the fault-only Lab run seam with an isolated scenario run

**Files:**

- Modify: `src/data_incident_gym/dbt_runner.py`
- Modify: `src/data_incident_gym/lab.py`
- Modify: `src/data_incident_gym/lab_verifier.py`
- Modify: `src/data_incident_gym/run_context.py`
- Modify: `tests/unit/test_dbt_runner.py`
- Modify: `tests/unit/test_lab.py`
- Modify: `tests/unit/test_lab_verifier.py`
- Modify: `tests/unit/test_run_context.py`
- Modify: `tests/integration/test_incident_lab.py`
- Modify: `tests/e2e/test_incident_reproducibility.py`

- [ ] **Step 1: Write RED tests for public/private run separation.**

After build, require this internal layout:

```text
.dig/lab/runs/{run_id}/
  runtime.json
  incident_brief.json
  schema.json
  profile_snapshot.json
  dbt/target/manifest.json
  dbt/target/run_results.json
  dbt/logs/dbt.log
.dig/lab/private/{run_id}/
  scenario_snapshot.json
  verification.json
```

`runtime.json` uses `p1.runtime.v1` and contains no ScenarioSpec digest, Ground Truth, role, answerability, expected status, accepted cause, direct failure, affected assets, or private path. `ObservableRunContext` resolves only public files. `IncidentVerifier` resolves both roots and alone validates the private snapshot against the committed ScenarioSpec.

- [ ] **Step 2: Run focused RED tests.**

```powershell
uv run pytest tests/unit/test_run_context.py tests/unit/test_lab.py tests/unit/test_lab_verifier.py -q
```

Expected: FAIL because the M6 `m2.run.v1` / `EXPECTED_FAILURE` seam is still fault-only and mixed with Ground Truth.

- [ ] **Step 3: Replace, rather than wrap, the run models.**

Use these terminal environment states:

```python
class ScenarioVerificationStatus(StrEnum):
    EXPECTED_FAILURE = "EXPECTED_FAILURE"
    HEALTHY_CONTROL = "HEALTHY_CONTROL"


@dataclass(frozen=True)
class ScenarioRun:
    run_id: str
    artifact_dir: Path
    verification_status: ScenarioVerificationStatus
    dbt_exit_code: int
```

Rename `DbtRunner.run_incident` to `run_scenario`; it continues to run `dbt build --exclude-resource-type seed` and returns the actual exit code. The verifier, not the runner, decides whether zero/non-zero matches the private scenario contract.

`IncidentLab.reset` always performs the full seed refresh. `prepare` applies the ordered closed mutation list; `NO_MUTATION` verifies the healthy state and writes nothing. `build` captures all declared observable schema/profile snapshots before publishing the active run. `restore` delegates to reset and verifies every mutation/distractor is gone.

- [ ] **Step 4: Implement only the M7 mutation union.**

```python
ScenarioMutation = Annotated[
    ColumnRenameMutation | ColumnTypeMutation | AddNullableColumnMutation | NoMutation,
    Field(discriminator="kind"),
]
```

Allow only the exact M7 triples in a closed validation table. Use `psycopg.sql.Identifier` and closed type SQL tokens. Variant A/B apply:

1. drop the stale `stg_orders` view before type mutation;
2. alter `raw_orders.user_id` from `integer` to `text`;
3. add nullable `raw_payments.source_batch_note text`.

Restore is the exact reverse followed by the existing full-refresh baseline rebuild. Do not modify `third_party/jaffle_shop`.

- [ ] **Step 5: Make verification status-aware and exact.**

For fault scenarios, require non-zero dbt status, exact direct failure, exact blast radius, exact fault schema, distractor state, and private ScenarioSpec digest. For the control, require exit 0, no failed/skipped model nodes, all dbt tests successful, exact healthy schema/profile snapshot, and no database mutation.

The insufficient twin's public `schema.json` intentionally excludes `raw_orders` according to `observable_evidence_contract`, but the private verifier independently inspects live `raw_orders` to prove the mutation and verifies that all non-decisive tools remain observable.

- [ ] **Step 6: Run deterministic real-environment tests.**

```powershell
uv run pytest tests/unit/test_dbt_runner.py tests/unit/test_run_context.py tests/unit/test_lab.py tests/unit/test_lab_verifier.py -q
uv run pytest tests/integration/test_incident_lab.py -q
uv run pytest tests/e2e/test_incident_reproducibility.py -q
```

Expected: all five scenarios reset/prepare/build/restore; four P1 scenarios are counted exactly, both type variants produce the locked symptoms, and the control produces `HEALTHY_CONTROL`.

- [ ] **Step 7: Commit.**

```powershell
git add src/data_incident_gym/dbt_runner.py src/data_incident_gym/lab.py src/data_incident_gym/lab_verifier.py src/data_incident_gym/run_context.py tests/unit/test_dbt_runner.py tests/unit/test_lab.py tests/unit/test_lab_verifier.py tests/unit/test_run_context.py tests/integration/test_incident_lab.py tests/e2e/test_incident_reproducibility.py
git commit -m "feat(m7): execute isolated fault and control scenarios"
```

## Task 4: Add the two bounded aggregate EvidenceRecord tools

**Files:**

- Modify: `src/data_incident_gym/evidence.py`
- Modify: `src/data_incident_gym/evidence_tools.py`
- Modify: `tests/unit/test_evidence.py`
- Modify: `tests/unit/test_evidence_tools.py`
- Modify: `tests/integration/test_evidence_tools.py`

- [ ] **Step 1: Write RED model tests for the two new fact types.**

Add `RELATION_DATA_PROFILE` and `RELATION_HISTORY` with source `postgres_profile_snapshot`. Facts must include `run_id`, relation, ProfileSpec version/hash, timezone-aware observation time through the EvidenceRecord, and normalized aggregate content.

```python
assert tuple(EvidenceType) == (
    EvidenceType.DBT_RUN_RESULTS,
    EvidenceType.DBT_NODE_ERROR,
    EvidenceType.RELATION_SCHEMA,
    EvidenceType.DBT_LINEAGE,
    EvidenceType.RELATION_DATA_PROFILE,
    EvidenceType.RELATION_HISTORY,
)
```

Add typed `PROFILE_SPEC_INVALID`, `PROFILE_METRIC_UNAVAILABLE`, `PROFILE_SNAPSHOT_MISMATCH`, and `PROFILE_OUTPUT_LIMIT` errors. None may degrade to an empty tuple or a zero-valued healthy fact.

- [ ] **Step 2: Write RED safety tests for exact signatures and invalid inputs.**

```python
assert tuple(inspect.signature(EvidenceTools.get_relation_data_profile).parameters) == (
    "self",
    "relation_name",
)
assert tuple(inspect.signature(EvidenceTools.get_relation_history).parameters) == (
    "self",
    "relation_name",
)
```

Reject unknown relations, qualified identifiers, SQL fragments, arbitrary columns, paths, URLs, snapshot/profile hash drift, live aggregate drift, output over caps, non-read-only transactions, and relations absent from any of: run snapshot, manifest catalog, or ProfileSpec.

- [ ] **Step 3: Run focused RED tests.**

```powershell
uv run pytest tests/unit/test_evidence.py tests/unit/test_evidence_tools.py -q
```

Expected: FAIL because the evidence union and tool methods still contain only four facts/tools.

- [ ] **Step 4: Implement live-read plus run-snapshot equality.**

Both methods must:

1. resolve the exact relation contract from ProfileSpec;
2. prove the relation is in the public run snapshot and dbt manifest;
3. start a diagnostic-role transaction and execute `SET TRANSACTION READ ONLY`;
4. collect normalized aggregates with `AggregateSnapshotReader`;
5. compare the complete normalized result with `profile_snapshot.json`;
6. return exactly one deterministic EvidenceRecord or a typed error.

Never return raw rows. Group labels and time buckets are allowed only when declared in ProfileSpec and are capped/deterministically ordered.

- [ ] **Step 5: Prove the six-tool surface and read-only SQL shape.**

Unit fakes must capture every statement and assert there are no DDL/DML statements, no interpolated user text, and no query outside the fixed compiler. Integration tests call both tools twice and assert stable IDs/content for `raw_orders`. On the insufficient twin, `get_relation_schema("raw_orders")` returns `RELATION_NOT_ALLOWED` while `get_relation_data_profile("raw_orders")` and `get_relation_history("raw_orders")` still return facts if declared by the observable contract; this preserves real ambiguity rather than disabling all evidence.

- [ ] **Step 6: Run tests and commit.**

```powershell
uv run pytest tests/unit/test_evidence.py tests/unit/test_evidence_tools.py -q
uv run pytest tests/integration/test_evidence_tools.py -q
uv run ruff check src/data_incident_gym/evidence.py src/data_incident_gym/evidence_tools.py tests/unit/test_evidence.py tests/unit/test_evidence_tools.py tests/integration/test_evidence_tools.py
git add src/data_incident_gym/evidence.py src/data_incident_gym/evidence_tools.py tests/unit/test_evidence.py tests/unit/test_evidence_tools.py tests/integration/test_evidence_tools.py
git commit -m "feat(m7): expose bounded profile evidence tools"
```

Expected: six tools are available; invalid or drifting observations fail closed.

## Task 5: Make Diagnosis, claims, trace, and run results policy-neutral

**Files:**

- Modify: `src/data_incident_gym/diagnosis.py`
- Modify: `src/data_incident_gym/diagnostic_kernel.py`
- Modify: `tests/unit/test_diagnosis.py`
- Modify: `tests/unit/test_diagnostic_kernel.py`
- Modify: `tests/unit/test_cli.py`
- Modify: shared Diagnosis fixtures in `tests/unit/test_artifacts.py`, `tests/unit/test_evaluation.py`, and `tests/unit/test_evaluation_runner.py`

- [ ] **Step 1: Write RED tests for the four terminal states and common claims.**

Move claim vocabulary out of `diagnostic_kernel.py` and make it part of `diagnosis.py`:

```python
class DiagnosisStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_INCIDENT = "NO_INCIDENT"
    MODEL_ERROR = "MODEL_ERROR"


class DiagnosticStrategy(StrEnum):
    STATIC_SKILL = "STATIC_SKILL"
    DIAGNOSTIC_KERNEL = "DIAGNOSTIC_KERNEL"


DiagnosisClaim = Annotated[
    RootCauseClaim | AffectedAssetClaim | HealthStateClaim,
    Field(discriminator="kind"),
]


class UnresolvedEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_kind: Literal["RELATION_SCHEMA", "TRANSFORMATION_DEFINITION"]
    subject: NonBlankStr
    reason_code: Literal["NOT_OBSERVABLE", "RELATION_NOT_ALLOWED"]
```

Add strict cross-field tests:

- `CONFIRMED` claims project exactly to top-level root cause, affected assets, and de-duplicated Evidence IDs.
- `INSUFFICIENT_EVIDENCE` has no claims/root/assets and has at least one unresolved item.
- `NO_INCIDENT` has only health claims, empty root/assets, non-empty Evidence IDs, and no unresolved items.
- `MODEL_ERROR` has no claims/root/assets/unresolved items and a fixed safe summary code.

- [ ] **Step 2: Write RED tests for an honest common terminal trace.**

```python
class DiagnosisTerminalTraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["DIAGNOSIS_TERMINAL"]
    strategy: DiagnosticStrategy
    status: DiagnosisStatus
    evidence_inventory: tuple[EvidenceId, ...]
```

`DiagnosisRunResult` must carry `strategy`, `policy_identity`, `diagnosis`, evidence, trace, metrics, and `kernel_state: InvestigationState | None`. It validates:

- exactly one final `DIAGNOSIS_TERMINAL` event;
- terminal strategy/status/inventory match the result;
- Kernel strategy has exactly one matching `KERNEL_STATE` immediately before terminal;
- Static strategy has no `KERNEL_STATE` and `kernel_state is None`;
- metrics match trace tool attempts for both policies;
- all evidence is run-bound and uniquely identified.

- [ ] **Step 3: Run the RED contract tests.**

```powershell
uv run pytest tests/unit/test_diagnosis.py -q
```

Expected: FAIL because `NO_INCIDENT`, common claims, strategy identity, and policy-neutral termination do not exist.

- [ ] **Step 4: Implement the P1 public schemas and remove M6-only result assumptions.**

Use `p1.diagnosis.v1` and `p1.investigation.v1`. `PolicyIdentity` contains:

```python
class PolicyIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: DiagnosticStrategy
    base_prompt_version: Literal["p1.base.v1"]
    base_prompt_sha256: Digest
    strategy_prompt_version: NonBlankStr
    strategy_prompt_sha256: Digest
    controller_protocol_version: NonBlankStr
    controller_protocol_sha256: Digest
    tool_schema_sha256: Digest
```

The controller protocol hash is SHA-256 over canonical JSON containing strategy, protocol version, six canonical tool schemas, budgets, and the relevant decision/state JSON schemas. Git revision remains separately recorded in artifacts.

Keep `MODEL_ERROR` controller-generated. Model decision schemas may output only `CONFIRMED`, `INSUFFICIENT_EVIDENCE`, or `NO_INCIDENT`.

- [ ] **Step 5: Upgrade Kernel state enums without implementing the Agent adapter yet.**

Add:

- `KernelFinalStatus.NO_INCIDENT`;
- `EvidenceGapKind.PROFILE_RELATION` -> `get_relation_data_profile`;
- `EvidenceGapKind.COMPARE_HISTORY` -> `get_relation_history`;
- `TRANSFORMATION_COLUMN_CAST_CHANGED` to the allowed M7 ontology;
- subject checks for the two new fact types;
- typed health claims and unresolved-evidence projection.

Do not put role, expected status, accepted answers, or case IDs into `InvestigationState`.

- [ ] **Step 6: Run contract tests and commit.**

```powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_kernel.py -q
uv run ruff check src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_kernel.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_kernel.py
git add src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_kernel.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_kernel.py tests/unit/test_cli.py tests/unit/test_artifacts.py tests/unit/test_evaluation.py tests/unit/test_evaluation_runner.py
git commit -m "refactor(m7): make diagnosis results policy neutral"
```

Expected: model/result invariants PASS for both policies without a fake Static Skill investigation state.

## Task 6: Register one six-tool surface and implement Diagnostic Kernel v2

**Files:**

- Create: `src/data_incident_gym/prompts/base_safety.md`
- Create: `src/data_incident_gym/prompts/diagnostic_kernel.md`
- Modify: `src/data_incident_gym/diagnostic_agent.py`
- Modify: `src/data_incident_gym/diagnostic_kernel.py`
- Modify: `tests/unit/test_diagnostic_agent.py`
- Modify: `tests/unit/test_diagnostic_kernel.py`
- Modify: `tests/integration/test_diagnostic_agent.py`

- [ ] **Step 1: Write RED tests proving the business Tool Schema is pure and canonical.**

Capture PydanticAI's declared function tools and assert the exact ordered names and business parameters:

```python
assert tuple(tool.name for tool in observed_tools) == (
    "get_dbt_run_results",
    "get_dbt_node_error",
    "get_relation_schema",
    "get_dbt_lineage",
    "get_relation_data_profile",
    "get_relation_history",
)
for tool in observed_tools:
    serialized = json.dumps(tool.parameters_json_schema, sort_keys=True)
    assert "gap_id" not in serialized
    assert "gap_kind" not in serialized
    assert "hypothesis_ids" not in serialized
```

Build a Static and Kernel runner over the same fake tools and assert byte-identical canonical tool-schema JSON and hash.

- [ ] **Step 2: Write RED tests for the separate Kernel intent transport.**

For every Kernel response containing one business tool call, require exactly one text part whose whole content validates as:

```python
class InvestigationIntentTransport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.kernel_intent.v1"]
    gap_id: GapId
    gap_kind: EvidenceGapKind
    hypothesis_ids: tuple[HypothesisId, ...]
    new_hypotheses: tuple[Hypothesis, ...]
```

Reject missing/extra text, malformed JSON, duplicate keys, more than one business tool call, intent/tool mismatch, repeated gap IDs, invalid hypothesis references, and intent reuse. Record only the structured intent, not free-form reasoning.

- [ ] **Step 3: Run focused RED tests.**

```powershell
uv run pytest tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py -q
```

Expected: FAIL because M6 embeds Kernel transport fields inside four tool schemas.

- [ ] **Step 4: Extract the shared base prompt and one shared tool registrar.**

`base_safety.md` contains only common safety, event, evidence citation, status, and budget rules. `diagnostic_kernel.md` contains Kernel intent/state rules. Load package resources as UTF-8, normalize to `\n`, hash exact bytes, and reject blank content.

One private `_register_evidence_tools(agent, state)` function defines all six PydanticAI tools. The executor receives a strategy adapter:

```python
class _PolicyAdapter(Protocol):
    def prepare(
        self,
        *,
        tool_name: str,
        arguments: dict[str, str],
        observation: ModelResponse,
    ) -> PreparedEvidenceCall:
        raise NotImplementedError

    def accept(
        self,
        prepared: PreparedEvidenceCall,
        records: tuple[EvidenceRecord, ...],
    ) -> tuple[EvidenceRecord, ...]:
        raise NotImplementedError

    def reject(self, prepared: PreparedEvidenceCall, error_code: str) -> None:
        raise NotImplementedError
```

`_KernelPolicyAdapter` validates the structured text intent and delegates to `DiagnosticKernel`. The Static adapter is added in Task 7; it receives only business calls and generic budget/deduplication checks.

The initial argument-provenance set contains only subjects from the public `IncidentBrief`. A relation tool call is accepted only when that subject is also present in the applicable public run snapshot, dbt manifest catalog, and ProfileSpec; later node/relation subjects may be added only from accepted EvidenceRecords. This lets the healthy control investigate `raw_orders` without exposing a private expected answer.

- [ ] **Step 5: Implement the three Kernel terminal gates.**

`CONFIRMED`:

- at least two registered hypotheses;
- exactly one selected supported hypothesis;
- at least one evidence-refuted alternative;
- all claim Evidence IDs exist, are closed, run-bound, subject-compatible, and type-compatible;
- no open or blocked decisive gap.

`INSUFFICIENT_EVIDENCE`:

- at least two live compatible hypotheses;
- at least one real blocked/open decisive gap;
- typed unresolved items exactly project those gaps;
- no root/asset/health claim.

`NO_INCIDENT`:

- no failed dbt nodes and `DbtRunResultsFact.run_status == "SUCCEEDED"`;
- a `HealthStateClaim` cites run result, current profile, and history for the same relation/series/bucket;
- current value equals the referenced profile/history point;
- at least four prior same-period points exist;
- current value is within inclusive prior same-period min/max;
- watermark/SLA, when declared, is satisfied;
- no root/asset claim and no unresolved decisive gap.

Any missing positive health evidence yields a rejected decision; it never becomes `NO_INCIDENT` by absence.

- [ ] **Step 6: Add stuck and budget invariants.**

Keep 8/8/2/300. Repeated/equivalent calls share a normalized business fingerprint and are rejected. A blocked decisive gap may terminate as insufficient; repeated attempts against the same blocked gap may not consume unbounded retries. Provider/protocol failures map to the existing fixed `MODEL_ERROR` codes.

- [ ] **Step 7: Run unit and FunctionModel integration tests.**

```powershell
uv run pytest tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py -q
uv run pytest tests/integration/test_diagnostic_agent.py -q
uv run ruff check src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/diagnostic_kernel.py
```

Expected: Kernel confirms both confirmable type cases, refuses the insufficient twin, proves the healthy control, and never sees private scenario fields.

- [ ] **Step 8: Commit.**

```powershell
git add src/data_incident_gym/prompts/base_safety.md src/data_incident_gym/prompts/diagnostic_kernel.md src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/diagnostic_kernel.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/integration/test_diagnostic_agent.py
git commit -m "feat(m7): upgrade the diagnostic kernel policy"
```

## Task 7: Implement the independent Static Skill baseline

**Files:**

- Create: `src/data_incident_gym/prompts/static_skill.md`
- Modify: `src/data_incident_gym/diagnostic_agent.py`
- Create: `tests/unit/test_static_skill.py`
- Create: `tests/integration/test_static_skill.py`
- Modify: `tests/unit/test_diagnostic_agent.py`

- [ ] **Step 1: Write the Static Skill prompt before wiring it to the runner.**

Write an independent, general dbt/data-quality investigation playbook inspired by the public workflow categories already cited in requirements. It must tell the model to:

1. inspect run status and failure symptoms;
2. form multiple plausible explanations internally;
3. choose the next observable evidence gap;
4. use only the six tools and exact returned identifiers;
5. bind every root/asset/health claim to compatible Evidence IDs;
6. return insufficient when decisive evidence is unavailable;
7. return no incident only with positive success/profile/history evidence.

It must not mention Kernel types, `InvestigationState`, `EvidenceGap` IDs, any scenario/case ID, specific Jaffle values, expected answers, variant roles, Ground Truth, or evaluator checks.

- [ ] **Step 2: Write RED prompt and behavior tests.**

```python
prompt = load_strategy_prompt(DiagnosticStrategy.STATIC_SKILL)
for forbidden in (
    "schema_type_change_payment_amount",
    "schema_type_change_order_customer_a",
    "schema_type_change_order_customer_b",
    "order_volume_pattern_a",
    "InvestigationState",
    "gap_id",
    "TEST_CONFIRMABLE",
    "TEST_INSUFFICIENT",
):
    assert forbidden not in prompt
```

With FunctionModel scripts, prove the Static policy can produce each non-error terminal status, maps protocol failure to `MODEL_ERROR`, uses at most eight business calls, and returns `kernel_state=None` with no `KERNEL_STATE` event.

- [ ] **Step 3: Run focused RED tests.**

```powershell
uv run pytest tests/unit/test_static_skill.py -q
```

Expected: FAIL because the Static policy adapter and prompt do not exist.

- [ ] **Step 4: Implement the minimal Static adapter.**

The Static adapter:

- uses the shared base prompt, incident brief, model settings, six tools, output schema, budgets, error mapping, trace recorder, and context clipping;
- records evidence and business-call fingerprints;
- validates only common structured-output facts such as known Evidence IDs and identity projection;
- does not create hypotheses, typed gaps, state transitions, deterministic sufficiency gates, or stuck-state decisions.

Its strategy decision schema contains common claims/unresolved items but no Kernel assessment fields. The deterministic evaluator, not the Static adapter, decides correctness.

- [ ] **Step 5: Prove fairness mechanically.**

Add a single test comparing Static and Kernel runner construction:

```python
assert static.model_identity == kernel.model_identity
assert static.budget == kernel.budget
assert static.tool_schema_sha256 == kernel.tool_schema_sha256
assert static.final_diagnosis_schema_sha256 == kernel.final_diagnosis_schema_sha256
assert static.incident_brief == kernel.incident_brief
```

Only strategy prompt/protocol hashes and optional Kernel state may differ.

- [ ] **Step 6: Run tests and commit.**

```powershell
uv run pytest tests/unit/test_static_skill.py tests/unit/test_diagnostic_agent.py -q
uv run pytest tests/integration/test_static_skill.py -q
uv run ruff check src/data_incident_gym/diagnostic_agent.py tests/unit/test_static_skill.py tests/integration/test_static_skill.py
git add src/data_incident_gym/prompts/static_skill.md src/data_incident_gym/diagnostic_agent.py tests/unit/test_static_skill.py tests/integration/test_static_skill.py tests/unit/test_diagnostic_agent.py
git commit -m "feat(m7): add the static diagnosis skill baseline"
```

Expected: Static and Kernel differ only at the control-policy seam.

## Task 8: Replace the M6 evaluator with scenario/status/claim scoring

**Files:**

- Modify: `src/data_incident_gym/evaluation.py`
- Modify: `tests/unit/test_evaluation.py`
- Create: `tests/unit/test_health_evaluation.py`
- Create: `tests/unit/test_insufficient_evaluation.py`

- [ ] **Step 1: Write RED tests for a canonical common check set.**

Use these common checks in this order:

```python
class EvaluationCheckCode(StrEnum):
    ENVIRONMENT_VERIFIED = "ENVIRONMENT_VERIFIED"
    STATUS_EXACT = "STATUS_EXACT"
    ROOT_CAUSE_ACCEPTED = "ROOT_CAUSE_ACCEPTED"
    AFFECTED_ASSETS_EXACT = "AFFECTED_ASSETS_EXACT"
    EVIDENCE_IDS_EXIST = "EVIDENCE_IDS_EXIST"
    EVIDENCE_RUN_SCOPE = "EVIDENCE_RUN_SCOPE"
    REQUIRED_EVIDENCE_TYPES_PRESENT = "REQUIRED_EVIDENCE_TYPES_PRESENT"
    CLAIM_EVIDENCE_COMPATIBLE = "CLAIM_EVIDENCE_COMPATIBLE"
    INSUFFICIENCY_GAP_DECLARED = "INSUFFICIENCY_GAP_DECLARED"
    POSITIVE_HEALTH_EVIDENCE = "POSITIVE_HEALTH_EVIDENCE"
    TOOL_ALLOWLIST_EXACT = "TOOL_ALLOWLIST_EXACT"
    TRACE_READ_ONLY_SAFE = "TRACE_READ_ONLY_SAFE"
    RECOVERY_HEALTHY = "RECOVERY_HEALTHY"
```

Each check has `applicability: APPLICABLE | NOT_APPLICABLE`. Non-applicable checks carry a fixed `NOT_APPLICABLE` reason and cannot hide a required check for the scenario status.

- [ ] **Step 2: Write adversarial status/claim tests.**

Reject:

- correct root with wrong status;
- confirmed answers on the insufficient twin;
- insufficient answers with no/mismatched unresolved item;
- no incident based only on no failed nodes;
- no incident using a history record for another relation/series/bucket;
- a current point outside the prior same-period range;
- root claims supported only by lineage or profile evidence;
- asset claims with no direct-error/downstream-lineage evidence;
- health claims with fabricated/foreign Evidence IDs;
- any unregistered tool, SQL-shaped trace argument, secret/path leakage, or evidence inventory not produced by trace.

- [ ] **Step 3: Run focused RED tests.**

```powershell
uv run pytest tests/unit/test_evaluation.py tests/unit/test_health_evaluation.py tests/unit/test_insufficient_evaluation.py -q
```

Expected: FAIL because the current evaluator assumes every passing run is M6 `CONFIRMED`.

- [ ] **Step 4: Implement common scoring from the frozen run result only.**

The evaluator receives private `ScenarioSpec`, private `ScenarioVerification`, the already-frozen `DiagnosisRunResult`, and recovery status. It may not query PostgreSQL, rerun tools, or mutate the result.

Common claim compatibility:

- root cause: node error plus decisive schema/profile evidence appropriate to the scenario;
- direct asset: matching node error;
- downstream asset: matching downstream lineage;
- health: successful run result plus matching profile/history and deterministic range/SLA calculation.

For insufficient scenarios, match `Diagnosis.unresolved_evidence` to the ScenarioSpec's declared unresolved gaps and verify the corresponding failed ToolTraceEvent when the gap is tool-observable.

- [ ] **Step 5: Keep Kernel-only audits supplementary.**

`EvaluationResult` carries `controller_checks` in addition to common `checks`. Kernel adds `KERNEL_STATE_VALID` and the status-appropriate hypothesis/gap audit. Static marks controller checks absent, not failed. These checks may fail a Kernel attempt but are not used as a cross-policy primary metric.

- [ ] **Step 6: Run tests and commit.**

```powershell
uv run pytest tests/unit/test_evaluation.py tests/unit/test_health_evaluation.py tests/unit/test_insufficient_evaluation.py -q
uv run ruff check src/data_incident_gym/evaluation.py tests/unit/test_evaluation.py tests/unit/test_health_evaluation.py tests/unit/test_insufficient_evaluation.py
git add src/data_incident_gym/evaluation.py tests/unit/test_evaluation.py tests/unit/test_health_evaluation.py tests/unit/test_insufficient_evaluation.py
git commit -m "feat(m7): score status and claim evidence fairly"
```

Expected: each policy is scored against the same scenario facts; no composite or Kernel-win rule is introduced.

## Task 9: Wire the strategy-neutral workflow, six-file artifacts, report, and CLI

**Files:**

- Modify: `src/data_incident_gym/evaluation_runner.py`
- Modify: `src/data_incident_gym/artifacts.py`
- Modify: `src/data_incident_gym/templates/report.md.j2`
- Modify: `src/data_incident_gym/cli.py`
- Modify: `tests/unit/test_evaluation_runner.py`
- Modify: `tests/integration/test_evaluation_runner.py`
- Modify: `tests/unit/test_artifacts.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: Write RED tests for diagnosis-before-private-evaluation ordering.**

Use spies to assert the workflow order:

```python
assert calls == [
    "lab.reset",
    "lab.prepare",
    "lab.build",
    "diagnosis.create",
    "diagnosis.run",
    "lab.restore",
    "scenario.load_private",
    "verification.load_private",
    "evaluate.frozen_result",
    "artifact.write",
]
```

The diagnosis factory accepts only `run_id`, `strategy`, diagnostic settings, and public project root. It never receives a ScenarioSpec, expected status, Ground Truth, answerability, or verification object. If diagnosis produced a `DiagnosisRunResult`, the workflow must still evaluate and write six files for all four terminal statuses.

- [ ] **Step 2: Write RED tests for P1 artifact metadata and both trace forms.**

Keep exactly:

```text
metadata.json
trace.jsonl
evidence.json
diagnosis.json
evaluation.json
report.md
```

Use `p1.metadata.v1`, `p1.trace.v1`, `p1.evidence.v1`, and `p1.evaluation.v1`. Metadata adds:

- `strategy`;
- base/strategy prompt versions and hashes;
- controller protocol version/hash;
- common tool-schema hash;
- `benchmark_manifest_sha256: null` for M7 development runs;
- the unchanged 8/8/2/300 budget;
- role/answerability only after diagnosis, copied by ArtifactWriter from evaluator output.

Static trace ends with `DIAGNOSIS_TERMINAL` and contains no Kernel state. Kernel trace ends `KERNEL_STATE` then `DIAGNOSIS_TERMINAL`. Both are atomically validated before rename.

- [ ] **Step 3: Run focused RED tests.**

```powershell
uv run pytest tests/unit/test_evaluation_runner.py tests/unit/test_artifacts.py tests/unit/test_cli.py -q
```

Expected: FAIL because M6 runner/artifacts require one fault, one policy, and a terminal Kernel state.

- [ ] **Step 4: Implement the strategy-neutral runner.**

```python
async def run(
    self,
    incident_case_id: str,
    strategy: DiagnosticStrategy,
) -> EvaluationAttemptResult:
    started_at = self._clock()
    mutation_started = False
    recovery_succeeded = False
    scenario_run: ScenarioRun | None = None
    diagnosis_run: DiagnosisRunResult | None = None
    self._lab.reset(incident_case_id)
    mutation_started = True
    try:
        self._lab.prepare(incident_case_id)
        scenario_run = self._lab.build(incident_case_id)
        diagnosis_run = await self._diagnosis_factory(
            scenario_run.run_id,
            strategy,
        ).diagnose()
    finally:
        if mutation_started:
            recovery_succeeded = self._lab.restore(incident_case_id).state == "HEALTHY"
    if diagnosis_run is None or scenario_run is None:
        raise EvaluationWorkflowError("DIAGNOSIS_FAILED")
    scenario = self._private_scenario_loader(incident_case_id)
    verification = self._private_verification_loader(scenario_run.run_id)
    evaluation = self._evaluator(
        scenario,
        verification,
        diagnosis_run,
        recovery_succeeded=recovery_succeeded,
    )
    return self._persist(started_at, scenario_run, diagnosis_run, evaluation)
```

Preserve the first safe stage error code, including a distinct restoration failure, and initialize `scenario_run` before reading it after the `try/finally` block. Do not load private evaluation facts before `diagnosis_run` is frozen and restoration has been attempted.

- [ ] **Step 5: Make the report policy/status aware.**

The report always renders strategy, final status, claims, cited evidence, unresolved evidence, metrics, and common checks. It conditionally renders the Kernel hypotheses/gaps section only when `kernel_state` exists. For `NO_INCIDENT` it labels health evidence positively; for `INSUFFICIENT_EVIDENCE` it lists the typed blocking gap. Retain HTML escaping and the statement that hidden reasoning is not stored.

- [ ] **Step 6: Add explicit CLI strategy selection without changing the default.**

Keep Diagnostic Kernel as the default P0-compatible strategy:

```powershell
uv run data-incident-gym diagnose schema_type_change_payment_amount --strategy diagnostic-kernel
uv run data-incident-gym eval run schema_type_change_payment_amount --strategy static-skill
```

Typer accepts only `diagnostic-kernel` and `static-skill`. Help text identifies the 4/17 M7 scenarios and the extra P0 rename regression. No command accepts a free-text question, SQL, or custom fault definition.

- [ ] **Step 7: Run unit and integration tests.**

```powershell
uv run pytest tests/unit/test_evaluation_runner.py tests/unit/test_artifacts.py tests/unit/test_cli.py -q
uv run pytest tests/integration/test_evaluation_runner.py -q
uv run ruff check src/data_incident_gym/evaluation_runner.py src/data_incident_gym/artifacts.py src/data_incident_gym/cli.py
```

Expected: both strategies write the same six filenames and pass cross-file identity checks.

- [ ] **Step 8: Commit.**

```powershell
git add src/data_incident_gym/evaluation_runner.py src/data_incident_gym/artifacts.py src/data_incident_gym/templates/report.md.j2 src/data_incident_gym/cli.py tests/unit/test_evaluation_runner.py tests/integration/test_evaluation_runner.py tests/unit/test_artifacts.py tests/unit/test_cli.py
git commit -m "feat(m7): run and report both diagnosis policies"
```

## Task 10: Extend doctor and add mechanical leakage/fairness audits

**Files:**

- Modify: `src/data_incident_gym/doctor.py`
- Modify: `tests/unit/test_doctor.py`
- Create: `tests/unit/test_p1_isolation.py`
- Create: `tests/unit/test_policy_fairness.py`
- Modify: `tests/unit/test_package.py`

- [ ] **Step 1: Write RED doctor tests for the P1 profile plane.**

Add exact checks:

```python
class DoctorCheckCode(StrEnum):
    PROFILE_SPEC = "PROFILE_SPEC"
    PROFILE_SNAPSHOT = "PROFILE_SNAPSHOT"
    PROFILE_READ_ONLY = "PROFILE_READ_ONLY"
    PROFILE_BOUNDS = "PROFILE_BOUNDS"
```

The doctor loads only ProfileSpec and `.dig/baseline/profile_snapshot.json`, connects with the diagnostic role, repeats current/history reads for `raw_orders`, proves snapshot equality, confirms read-only transaction state, and probes one invalid relation locally. It does not load ScenarioSpec or Ground Truth and does not make extra model calls beyond the existing single doctor capability probe.

- [ ] **Step 2: Write RED import/content isolation tests.**

Parse the AST for `diagnostic_agent.py`, `diagnostic_kernel.py`, `diagnosis.py`, `evidence_tools.py`, and prompt resources. Fail if they:

- import `data_incident_gym.scenarios`;
- open `config/scenarios` or `.dig/lab/private`;
- contain any TEST scenario ID;
- contain `DEV_CONFIRMABLE`, `TEST_CONFIRMABLE`, `TEST_INSUFFICIENT`, `NO_INCIDENT_CONTROL`, `answerability`, `expected_status`, or accepted answer literals used for dispatch;
- compare `incident_case_id` to choose a prompt, hypothesis, tool, or diagnosis.

Allow root-cause ontology members in Kernel validation; reject mapping them to case IDs or fixed observed values.

- [ ] **Step 3: Write RED fairness tests from generated runtime objects.**

For each M7 scenario, construct Static/Kernel runners and assert:

- same model/provider/sampling settings;
- same public IncidentBrief bytes;
- same six tool JSON schemas and schema hash;
- same output Diagnosis JSON schema hash;
- same 8/8/2/300 budgets;
- same evidence clipping and typed tool errors;
- no private scenario field in model request messages or tool return content;
- only strategy prompt/protocol hash and Kernel state protocol differ.

- [ ] **Step 4: Run RED tests.**

```powershell
uv run pytest tests/unit/test_doctor.py tests/unit/test_p1_isolation.py tests/unit/test_policy_fairness.py -q
```

Expected: FAIL until doctor checks and mechanical isolation/fairness seams are complete.

- [ ] **Step 5: Implement the minimum doctor changes and make audits green.**

Keep doctor read-only. A missing baseline profile snapshot is `UNAVAILABLE` with a fixed recommendation; doctor must not build/reset/inject to create one. Redact credentials, SQL, paths, provider bodies, and raw database values in failed observations.

- [ ] **Step 6: Run tests and commit.**

```powershell
uv run pytest tests/unit/test_doctor.py tests/unit/test_p1_isolation.py tests/unit/test_policy_fairness.py tests/unit/test_package.py -q
uv run ruff check src/data_incident_gym/doctor.py tests/unit/test_doctor.py tests/unit/test_p1_isolation.py tests/unit/test_policy_fairness.py
git add src/data_incident_gym/doctor.py tests/unit/test_doctor.py tests/unit/test_p1_isolation.py tests/unit/test_policy_fairness.py tests/unit/test_package.py
git commit -m "test(m7): enforce profile and policy isolation"
```

## Task 11: Close the deterministic 4/17 vertical slice and preserve P0 regression

**Files:**

- Create: `tests/e2e/test_m7_scenario_reproducibility.py`
- Create: `tests/e2e/test_m7_policy_matrix.py`
- Modify: `tests/e2e/test_real_model_diagnosis.py`
- Modify: `tests/e2e/test_real_model_evaluation.py`
- Modify: `tests/integration/test_evaluation_runner.py`
- Modify: `README.md`
- Modify: `.github/workflows/ci.yml` only if a named deterministic M7 test command is needed; do not add secrets or real-model jobs

- [ ] **Step 1: Add ten-cycle scenario reproducibility tests.**

For each of the four M7 scenarios, run ten `reset -> prepare -> build -> private verify -> restore` cycles and compare:

- dbt status/failure set;
- exact schema and profile snapshot digests;
- distractor state;
- public runtime/brief schema excluding random run ID/timestamps;
- private ScenarioSpec digest;
- restore baseline fingerprint.

The control must remain unmutated and healthy on all ten cycles. The P0 rename regression must still complete its existing deterministic cycle.

- [ ] **Step 2: Add an 8-cell deterministic policy matrix.**

Run one FunctionModel attempt for every `4 scenarios x 2 strategies` combination. All eight must:

- reach the scenario's exact expected status;
- pass every applicable common evaluator check;
- pass Kernel-only checks when applicable;
- stay within 8/8/2/300;
- use only the six tools;
- write exactly six artifact files;
- restore healthy state.

The scripted models may inspect returned EvidenceRecords but may not branch on case ID. Drive behavior from evidence kind/content and the public brief only.

- [ ] **Step 3: Turn old M6 real-model tests into explicitly historical/non-default tests.**

Do not let `tests/e2e` accidentally spend model calls. Keep all real-model files marked `real_model` and gated by `DIG_RUN_REAL_MODEL_TESTS=1`. Replace M6 sample-count assertions with M7 development-smoke metadata assertions, but do not execute them in this task.

- [ ] **Step 4: Run the complete deterministic suite in the same shape as CI.**

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -m "not real_model" -q
```

Expected:

- all deterministic tests PASS;
- real-model tests are skipped/deselected;
- no model endpoint call occurs;
- no repository file outside the plan's listed scope changes during tests.

- [ ] **Step 5: Run the high-risk tests again in isolation.**

```powershell
uv run pytest tests/e2e/test_m7_scenario_reproducibility.py -q
uv run pytest tests/e2e/test_m7_policy_matrix.py -q
uv run pytest tests/unit/test_p1_isolation.py tests/unit/test_policy_fairness.py -q
git diff --check
```

Expected: all PASS and `git diff --check` produces no output.

- [ ] **Step 6: Update README with facts only and commit.**

README may say M7 deterministic infrastructure and 4/17 scenarios are implemented only after the tests above pass. It must say the 94-run benchmark has not started and no Kernel advantage conclusion exists. Do not claim the development real-model smoke passed before Task 12 actually runs.

```powershell
git add tests/e2e/test_m7_scenario_reproducibility.py tests/e2e/test_m7_policy_matrix.py tests/e2e/test_real_model_diagnosis.py tests/e2e/test_real_model_evaluation.py tests/integration/test_evaluation_runner.py README.md .github/workflows/ci.yml
git commit -m "test(m7): close the deterministic benchmark slice"
```

If `.github/workflows/ci.yml` did not require a change, do not stage it.

## Task 12: Gated real-model smoke, independent review, and Ubuntu CI

**Files:**

- Create: `tests/e2e/test_real_model_m7_smoke.py`
- Create after an authorized smoke: `docs/superpowers/reports/2026-08-30-m7-development-smoke.md`
- Modify after verified completion: `README.md`

- [ ] **Step 1: Add the exact eight-run smoke harness without executing it.**

The parameter matrix is canonical:

```python
SMOKE_CASES = (
    "schema_type_change_payment_amount",
    "schema_type_change_order_customer_a",
    "schema_type_change_order_customer_b",
    "order_volume_pattern_a",
)
SMOKE_STRATEGIES = (
    DiagnosticStrategy.STATIC_SKILL,
    DiagnosticStrategy.DIAGNOSTIC_KERNEL,
)
assert len(SMOKE_CASES) * len(SMOKE_STRATEGIES) == 8
```

Each cell executes once in declared order, with a fresh session and environment reset. No pytest rerun plugin, loop retry, replacement sample, or raised limit is permitted. Every started diagnosis that yields a `DiagnosisRunResult` writes six files even when evaluation fails. The report records all eight outcomes and clearly labels them “M7 development smoke; excluded from the formal 94-run denominator.”

- [ ] **Step 2: Run the harness collection-only and deterministic guards.**

```powershell
uv run pytest tests/e2e/test_real_model_m7_smoke.py --collect-only -q
uv run pytest tests/unit/test_p1_isolation.py tests/unit/test_policy_fairness.py -q
```

Expected: exactly 8 smoke items collected; no model request occurs.

- [ ] **Step 3: Stop and obtain explicit authorization for eight real-model calls.**

Do not infer this authorization from approval of the requirements or this implementation plan.

- [ ] **Step 4: After authorization, execute exactly once.**

```powershell
$env:DIG_RUN_REAL_MODEL_TESTS='1'
uv run pytest tests/e2e/test_real_model_m7_smoke.py -m real_model -q
Remove-Item Env:DIG_RUN_REAL_MODEL_TESTS
```

Expected: exactly 8 attempts, no ninth call under any outcome. M7 smoke protocol is valid when all attempts are retained, artifacts are complete for every produced run result, and no safety/hard-gate failure occurs. Diagnostic correctness is reported per cell; do not silently tune a `TEST_*` scenario from these outputs.

- [ ] **Step 5: Write the smoke report from saved structured artifacts.**

Report exact numerator/denominator by status and policy, model/provider, prompt/controller/tool hashes, artifact paths, failures, and confirmation that the runs are excluded from 94. Do not calculate P1 aggregate metrics or declare a winner.

- [ ] **Step 6: Request an independent code review before remote publication.**

Use the `requesting-code-review` skill. Review against:

- `docs/requirements.md` sections 10.5, 10.6, 11.2, 11.3, 12.3-12.7, 13-15, and M7;
- this plan's scenario matrix and public/private seam;
- the fixed pre-M7 base commit recorded before implementation.

Resolve only in-scope findings and rerun the smallest affected RED/GREEN test plus the full deterministic suite.

- [ ] **Step 7: Final local verification.**

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -m "not real_model" -q
git diff --check
git status --short
```

Expected: lint/tests PASS, diff check is silent, and status contains only intentional M7/report/document changes.

- [ ] **Step 8: Commit the authorized smoke evidence.**

```powershell
git add tests/e2e/test_real_model_m7_smoke.py docs/superpowers/reports/2026-08-30-m7-development-smoke.md README.md
git commit -m "docs(m7): record bounded development smoke"
```

If real-model authorization was not granted, commit only the unexecuted harness with truthful README wording and leave M7 completion explicitly pending smoke evidence.

- [ ] **Step 9: Stop and obtain explicit authorization before push.**

After authorization, push the current branch without rewriting remote history:

```powershell
$m7Branch = git branch --show-current
git push -u origin $m7Branch
gh run list --branch $m7Branch --workflow ci --limit 5
```

Wait for the matching commit's Ubuntu workflow and inspect jobs:

```powershell
$m7Head = git rev-parse HEAD
$m7Run = gh run list --branch $m7Branch --workflow ci --commit $m7Head --limit 1 --json databaseId --jq '.[0].databaseId'
gh run view $m7Run --json status,conclusion,headSha,jobs
```

Expected: `headSha` equals `$m7Head` and every required job concludes `success`. A human-observed Ubuntu pass may be recorded as such, but do not manufacture a `gh` result if credentials prevent inspection.

## Final M7 acceptance checklist

- [ ] Exactly 4/17 P1 scenarios exist with strict ScenarioSpec and reproducible private verification.
- [ ] The existing type-change scenario is `DEV_CONFIRMABLE`; variant A is `TEST_CONFIRMABLE`; variant B is a real `TEST_INSUFFICIENT` twin; the periodic control is `NO_INCIDENT_CONTROL`.
- [ ] `CONFIRMED`, `INSUFFICIENT_EVIDENCE`, `NO_INCIDENT`, and `MODEL_ERROR` have consistent Schema, trace, evaluator, report, and CLI semantics.
- [ ] Final tool allowlist is exactly six and both policies expose byte-identical Tool Schemas under the same total eight-call budget.
- [ ] ProfileSpec contains no scenario IDs, answer values, expected thresholds, root-cause labels, free SQL, arbitrary identifiers, or raw-row access.
- [ ] Static Skill has no typed Kernel state/gap interface; Kernel intent is separated from business tool arguments.
- [ ] Public run context contains no role, answerability, expected status, accepted answer, Ground Truth, private path, or evaluator rule.
- [ ] The model receives no case ID; identity is added after semantic output without answer dispatch.
- [ ] Common evaluator checks score frozen Diagnosis/evidence/trace only; Kernel-only checks are supplementary and never a Static-only disadvantage.
- [ ] Both strategies produce exactly six files for every available DiagnosisRunResult, including non-confirmed/error results.
- [ ] Ten-cycle scenario replay, 8-cell deterministic policy matrix, P0 regressions, unit/integration/E2E, Ruff, and diff checks pass.
- [ ] The eight-run development smoke is either explicitly authorized and fully reported, or M7 is truthfully marked pending; it is never merged into the 94 formal runs.
- [ ] No final Benchmark Manifest, P1 aggregate score, strategy winner, or 94-run execution is claimed in M7.
- [ ] Ubuntu CI is verified only after an authorized push.

## Implementation handoff

Before coding, record the exact base commit containing the approved requirements and this plan:

```powershell
git status --short --branch
git rev-parse HEAD
git diff -- docs/requirements.md docs/superpowers/plans/2026-08-30-m7-benchmark-foundation.md
```

Use an isolated worktree/branch named `codex/m7-benchmark-foundation` if implementation is not continuing in this already isolated requirements worktree. Preserve unrelated user files and do not copy changes from the dirty local `master` checkout.
